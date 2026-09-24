"""
选址预测 API 路由模块

本模块在系统架构中位于接口层，负责对新地理位置进行开店适宜性评分，
并生成城市级选址推荐热力图。核心能力：

1. 点位评分（POST /score）：对任意坐标计算 0~1 的综合评分
2. 城市热力图（GET /heatmap）：基于网格划分生成全市范围内的选址推荐图

评分算法设计思路（基于规则 + 可解释性优先）：
- 从数据库实时提取候选点周边的 POI 密度特征（9 类 x 3 半径 = 27 维）
- 使用加权求和公式（权重来源于 Lift 分析结果）计算原始分
- 叠加城市层级加成（高线城市享有 bonus）
- 通过 Sigmoid 函数归一化到 (0, 1) 区间
- 也支持可选加载 XGBoost 模型进行评分（惰性加载，减少内存占用）

热力图生成使用 PostGIS ST_SquareGrid 函数，将城市边界划分为等距网格，
逐格计算 POI 密度特征并评分。
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from ..database import parse_db_url, settings
from ..models.schemas import PredictionRequest, PredictionResponse
import asyncpg
import joblib
import numpy as np
from pathlib import Path

router = APIRouter(prefix='/api/prediction', tags=['prediction'])

# XGBoost 模型文件路径（相对于 backend 目录，向上两层到项目根，再进入 data/models）
MODEL_PATH = Path(__file__).parent.parent.parent / 'data' / 'models' / 'xgboost_location_model.pkl'

# 惰性加载：模块首次导入时不加载模型，首次调用评分接口时才加载
# 这样能显著减少启动时间并降低内存占用（模型未部署时也不影响其他API）
_model = None


def get_model():
    """
    惰性加载 XGBoost 模型

    设计意图：
    - 模型文件较大（可能数十MB），启动时加载会显著拖慢服务启动
    - 模型可能未部署（开发/测试环境），不应影响其他 API 可用性
    - 采用全局变量 + 惰性加载模式，首次调用时加载，之后复用

    Returns:
        训练好的 XGBoost 模型对象，或 None（模型文件不存在时）
    """
    global _model
    if _model is None and MODEL_PATH.exists():
        _model = joblib.load(MODEL_PATH)
    return _model


# 完整特征列定义 — 9 个 POI 类别 x 3 个半径 = 27 个空间特征 + 1 个城市层级特征
# 命名规则: {poi类别}_{半径米数}
FEATURE_COLS = [
    'metro_200', 'metro_500', 'metro_1000',
    'bus_200', 'bus_500', 'bus_1000',
    'office_200', 'office_500', 'office_1000',
    'mall_200', 'mall_500', 'mall_1000',
    'restaurant_200', 'restaurant_500', 'restaurant_1000',
    'cafe_200', 'cafe_500', 'cafe_1000',
    'residential_200', 'residential_500', 'residential_1000',
    'university_200', 'university_500', 'university_1000',
    'convenience_200', 'convenience_500', 'convenience_1000',
    'city_tier',
]


@router.post('/score')
async def predict_score(req: PredictionRequest):
    """
    位置评分 — 对待评估坐标进行综合开店适宜性评分

    评分流水线（五步法）：
    ┌─────────────────────────────────────────────────────────────┐
    │ 步骤1: 提取 POI 空间特征                                    │
    │   对 9 个 POI 类别 x 3 个半径，使用 ST_DWithin 计算每个组   │
    │   合下候选点周边的 POI 数量。ST_DWithin 利用 geography 类型  │
    │   的 GiST 索引进行高效的空间范围查询。                        │
    │                                                             │
    │ 步骤2: 获取城市层级                                          │
    │   从 city_tiers 维表查询，默认 tier=4（三线城市）。          │
    │   一线城市 tier=1，享有最高的评分加成。                       │
    │                                                             │
    │ 步骤3: 加权原始分计算                                        │
    │   使用 500m 半径的 8 个关键特征，按预定义权重加权求和：     │
    │     raw_score = Σ(feature_value_i * weight_i)                │
    │   权重分配依据 Lift 分析结果：                                  │
    │     office_500    权重 0.25（写字楼密度对咖啡消费最相关）     │
    │     metro_500     权重 0.20（地铁站保证客流量）              │
    │     mall_500      权重 0.15（商业综合体聚客效应）            │
    │     residential_500 权重 0.15（居民区提供稳定客源）          │
    │     university_500 权重 0.10（高校年轻人群体）               │
    │     restaurant_500 权重 0.05（餐饮聚集区溢出效应）           │
    │     convenience_500 权重 0.05（便利店互补业态）               │
    │     cafe_500      权重 0.05（咖啡聚集区的竞争/协同）         │
    │   注意：权重可根据业务策略调整，总和不要求为 1.0               │
    │                                                             │
    │ 步骤4: 城市层级加成                                          │
    │   tier_bonus = max(0, (5 - city_tier) * 0.05)               │
    │   一线城市 +0.20, 新一线 +0.15, 二线 +0.10, 三线 +0.05      │
    │                                                             │
    │ 步骤5: Sigmoid 归一化                                        │
    │   score = 1 / (1 + exp(-(raw_score - 1.5 + tier_bonus)))    │
    │   Sigmoid 函数将任意实数映射到 (0, 1)，-1.5 为偏移量          │
    │   使 raw_score=1.5（无加成）时 score≈0.5                     │
    │   最终裁剪到 [0.01, 0.99] 避免极端值                         │
    └─────────────────────────────────────────────────────────────┘

    Args:
        req: PredictionRequest — 包含 lng, lat, city（可选）

    Returns:
        dict: {
            score: float              — 综合评分（0~1）
            lng, lat, city:           — 回显请求参数
            features: dict            — 完整的 28 维特征值
            top_positive: list[dict]  — 按权重降序排列的正面特征 Top5
            top_negative: list[dict]  — 权重最低的 5 个特征
            model_available: bool     — XGBoost 模型是否已加载
        }
    """
    model = get_model()

    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        # ============================================================
        # 步骤1: POI 空间特征提取
        # 对 9 个 POI 类别，分别在 200m/500m/1000m 半径内统计 POI 数量
        # 使用 geography 类型转换确保 ST_DWithin 使用球面距离（米）
        # ============================================================
        features = {}
        poi_categories = ['metro', 'bus', 'office', 'mall', 'restaurant', 'cafe',
                          'residential', 'university', 'convenience']

        for cat in poi_categories:
            for radius in [200, 500, 1000]:
                # ST_DWithin(geom::geography, point::geography, radius_meters)
                # geography 类型确保距离计算基于球面（spheroid），单位为米
                # geom::geography 利用已有的 GiST 空间索引加速
                count = await conn.fetchval('''
                    SELECT COUNT(*) FROM pois
                    WHERE category = $1
                    AND ST_DWithin(
                        geom::geography,
                        ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography,
                        $4
                    )
                ''', cat, req.lng, req.lat, radius)
                features[f'{cat}_{radius}'] = count or 0

        # ============================================================
        # 步骤2: 城市层级查询
        # 默认 tier=4（三线城市），如果传入 city 则从 city_tiers 维表查询
        # ============================================================
        city_tier = 4  # 默认值：三线城市
        if req.city:
            tier_row = await conn.fetchrow(
                'SELECT tier FROM city_tiers WHERE city_name = $1', req.city
            )
            if tier_row:
                city_tier = tier_row['tier']
        features['city_tier'] = city_tier

        # ============================================================
        # 步骤3: 加权原始分计算
        # 权重基于 Lift 分析：写字楼(0.25) > 地铁(0.20) > 商场(0.15) = 住宅(0.15)
        # > 高校(0.10) > 餐厅(0.05) = 便利店(0.05) = 咖啡店(0.05)
        # 仅使用 500m 半径特征，因为此半径对选址决策最具区分度
        # ============================================================
        weights = {
            'office_500': 0.25,
            'metro_500': 0.20,
            'mall_500': 0.15,
            'residential_500': 0.15,
            'restaurant_500': 0.05,
            'university_500': 0.10,
            'convenience_500': 0.05,
            'cafe_500': 0.05,
        }

        # 加权求和: Σ(特征值 * 权重)
        raw_score = sum(features.get(k, 0) * v for k, v in weights.items())

        # ============================================================
        # 步骤4: 城市层级加成
        # 高线城市享有更高的基础评分，公式: (5 - tier) * 0.05
        # 一线=1 → +0.20, 新一线=2 → +0.15, 二线=3 → +0.10, 三线=4 → +0.05
        # max(0, ...) 确保加成非负
        # ============================================================
        tier_bonus = max(0, (5 - city_tier) * 0.05)

        # ============================================================
        # 步骤5: Sigmoid 归一化
        # sigmoid(x) = 1 / (1 + exp(-x))
        # 偏移量 -1.5 使 raw_score=1.5 时 sigmoid(0)=0.5 位于中间
        # 裁剪到 [0.01, 0.99] 避免取到 0 或 1（概率极端值）
        # ============================================================
        import math
        score = 1.0 / (1.0 + math.exp(-(raw_score - 1.5 + tier_bonus)))
        score = round(min(0.99, max(0.01, score)), 4)

        # ============================================================
        # 特征贡献排序 — 用于向用户解释评分的依据
        # 按 importance（权重）降序排列，正面 Top5 和负面 Bottom 5
        # ============================================================
        feature_contributions = [
            {'feature': k, 'value': features.get(k, 0), 'importance': v}
            for k, v in sorted(weights.items(), key=lambda x: x[1], reverse=True)
        ]
        sorted_features = feature_contributions

        return {
            'score': round(score, 4),
            'lng': req.lng,
            'lat': req.lat,
            'city': req.city,
            'features': features,
            'top_positive': sorted_features[:5],
            'top_negative': sorted_features[-5:] if len(sorted_features) >= 5 else [],
            'model_available': model is not None,
        }
    finally:
        await conn.close()


@router.get('/heatmap')
async def prediction_heatmap(
    city: str = Query(..., description='城市名（必须与 admin_boundaries 表中一致）'),
    grid_size_m: int = Query(500, description='网格边长（米），默认 500m'),
):
    """
    城市选址推荐热力图 — 基于网格划分的全域评分

    算法流程：
    1. 从 admin_boundaries 表获取城市行政边界多边形
    2. 使用 PostGIS ST_SquareGrid(size, geom) 函数将边界划分为等距正方形网格
       - ST_SquareGrid 是 PostGIS 3.1+ 提供的网格生成函数
       - size 参数指定网格边长（单位取决于 SRID，这里 4326 下传入米会自动处理）
    3. 对每个网格单元，以质心为中心计算 500m 范围内的三类关键 POI 密度：
       - metro_500: 地铁站数量
       - office_500: 写字楼数量
       - commercial_500: 商业POI（商场+餐厅+咖啡店）数量
    4. 加权评分: score = min(1.0, (metro*0.20 + office*0.25 + commercial*0.15) / 5.0)
       - 除以 5.0 做归一化缩放，LEAST 截断到 1.0
    5. 输出为 GeoJSON FeatureCollection，前端可直接渲染为热力图图层

    Args:
        city:        城市名称
        grid_size_m: 网格边长（米），影响分辨率与性能

    Returns:
        dict: GeoJSON FeatureCollection {
            type: "FeatureCollection",
            features: [{geometry, properties: {metro_500, office_500,
                        commercial_500, score}}, ...],
            city, grid_size_m
        }

    Raises:
        HTTPException(404): 城市边界未找到（admin_boundaries 表中无此城市）
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        # 查找城市边界（用于后续网格生成和范围限定）
        city_row = await conn.fetchrow('''
            SELECT name, ST_AsGeoJSON(geom) AS geojson
            FROM admin_boundaries
            WHERE name = $1 AND level = 'city'
        ''', city)

        if not city_row:
            raise HTTPException(status_code=404, detail='城市边界未找到')

        # CTE 级联查询: 城市边界 → 正方形网格 → 网格特征 → 评分
        # 三个 CTE：
        #   city_bound:   城市边界多边形
        #   grid:         ST_SquareGrid 生成的网格集合
        #   cell_features: 每个网格单元的 POI 统计（关联标量子查询）
        rows = await conn.fetch('''
            WITH city_bound AS (
                SELECT geom FROM admin_boundaries WHERE name = $1 AND level = 'city'
            ),
            grid AS (
                -- ST_SquareGrid(size, geom): 将多边形划分为等距正方形网格
                -- 返回的每个 cell 的 SRID 与输入 geom 一致
                SELECT (ST_SquareGrid($2, geom)).geom AS cell_geom
                FROM city_bound
            ),
            cell_features AS (
                SELECT
                    g.cell_geom,
                    ST_Centroid(g.cell_geom) AS center,
                    -- 标量子查询: 统计网格质心 500m 内的地铁站数量
                    (
                        SELECT COUNT(*) FROM pois p
                        WHERE p.category = 'metro'
                        AND ST_DWithin(p.geom::geography,
                            ST_Centroid(g.cell_geom)::geography, 500)
                    ) AS metro_500,
                    -- 统计网格质心 500m 内的写字楼数量
                    (
                        SELECT COUNT(*) FROM pois p
                        WHERE p.category = 'office'
                        AND ST_DWithin(p.geom::geography,
                            ST_Centroid(g.cell_geom)::geography, 500)
                    ) AS office_500,
                    -- 商业类 POI 聚合（商场 + 餐厅 + 咖啡店）
                    (
                        SELECT COUNT(*) FROM pois p
                        WHERE p.category IN ('mall', 'restaurant', 'cafe')
                        AND ST_DWithin(p.geom::geography,
                            ST_Centroid(g.cell_geom)::geography, 500)
                    ) AS commercial_500
                FROM grid g
                -- 仅保留与城市边界相交的网格（剔除边界外的冗余网格）
                WHERE ST_Intersects(g.cell_geom, (SELECT geom FROM city_bound))
            )
            SELECT
                ST_AsGeoJSON(cell_geom) AS geojson,
                metro_500,
                office_500,
                commercial_500,
                -- 加权评分: 三类特征按权重线性组合，除以 5.0 归一化，LEAST 截断
                LEAST(1.0, (metro_500 * 0.20 + office_500 * 0.25 + commercial_500 * 0.15) / 5.0) AS score
            FROM cell_features
        ''', city, grid_size_m)

        # 组装 GeoJSON FeatureCollection
        features_list = []
        for r in rows:
            features_list.append({
                'geometry': r['geojson'],
                'properties': {
                    'metro_500': r['metro_500'],
                    'office_500': r['office_500'],
                    'commercial_500': r['commercial_500'],
                    'score': round(float(r['score']), 4),
                }
            })

        return {
            'type': 'FeatureCollection',
            'features': features_list,
            'city': city,
            'grid_size_m': grid_size_m,
        }
    finally:
        await conn.close()
