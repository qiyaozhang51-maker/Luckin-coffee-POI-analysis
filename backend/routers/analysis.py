"""
分析相关 API 路由模块

本模块在系统架构中位于接口层，负责处理空间分析相关的 HTTP 请求，
提供门店选址特征的多维度洞察，包括：

- POI 关联分析：统计门店周边各类 POI 的平均数量与分布
- Lift 值分析：衡量门店周边 POI 富集程度相对于随机点位的倍数
- 品牌对比分析：对比瑞幸与星巴克在城市层面的布局策略差异
- 城市层级统计：按一线/新一线/二线等城市分级汇总门店渗透情况

所有分析结果均来自预计算缓存表（poi_correlation_cache / poi_lift_cache /
brand_compare_cache），避免实时扫描全表导致慢查询。
"""
from fastapi import APIRouter, Query
from typing import Optional
from ..database import parse_db_url, settings
import asyncpg
import json

router = APIRouter(prefix='/api/analysis', tags=['analysis'])


@router.get('/store-poi-correlation')
async def store_poi_correlation(
    brand: str = Query('luckin', description='品牌：luckin 或 starbucks'),
    radius: int = Query(500, description='统计半径（米）：200 | 500 | 1000'),
    city: Optional[str] = Query(None, description='城市筛选（为空则统计全国）'),
):
    """
    门店-POI 关联分析 — 统计门店周边各类 POI 分布特征

    查询预计算缓存表 poi_correlation_cache，返回指定半径下各类 POI
    在门店周边的统计指标：
    - avg_count: 平均每个门店周边该类别 POI 的数量
    - stddev: 标准差（反映分布的离散程度）
    - min_count / max_count: 极值（最偏远的门店 vs 最密集的门店）
    - store_count: 纳入统计的门店数量

    缓存键约定：
    - city 参数为空时，key 使用 '__ALL__'，表示全国汇总数据
    - city 参数非空时，key 使用具体城市名

    Args:
        brand:  品牌筛选
        radius: 统计半径，必须为 200/500/1000 之一
        city:   城市筛选

    Returns:
        list[dict]: 按 avg_count 降序排列的 POI 类别统计列表
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        # 缓存表中的 city 列：具体城市名 或 '__ALL__'（全国汇总）
        key = city if city else '__ALL__'

        rows = await conn.fetch('''
            SELECT poi_category, avg_count, stddev, min_count, max_count, store_count
            FROM poi_correlation_cache
            WHERE radius = $1 AND brand = $2 AND city = $3
            ORDER BY avg_count DESC
        ''', radius, brand, key)

        return [
            {
                'category': r['poi_category'],
                'radius': radius,
                'avg_count': float(r['avg_count']),
                'stddev': float(r['stddev']) if r['stddev'] else 0,
                'min_count': r['min_count'],
                'max_count': r['max_count'],
                'store_count': r['store_count'],
            }
            for r in rows
        ]
    finally:
        await conn.close()


@router.get('/lift-scores')
async def lift_scores(
    brand: str = Query('luckin', description='品牌：luckin 或 starbucks'),
    radius: int = Query(500, description='统计半径（米）'),
    city: Optional[str] = Query(None, description='城市筛选'),
):
    """
    POI Lift 值分析 — 衡量门店周边 POI 富集程度

    Lift 值定义与计算方式：
        Lift = 门店周边POI平均密度 / 随机点位POI平均密度

    解读：
        Lift > 1.0: 门店周边该类别 POI 密度高于随机点位，
                     说明品牌倾向于在此类 POI 附近选址（正向吸引）
                     e.g. 写字楼 Lift=2.5 表示门店周边写字楼密度是随机点的 2.5 倍
        Lift ≈ 1.0: 门店选址与该类别 POI 无明显关联
        Lift < 1.0: 门店倾向于远离该类别 POI（负向排斥）

    数据来源：
        poi_lift_cache 表中预计算了每个门店周边各类 POI 数量和同样数量
        随机点位的 POI 数量，取比值得到 Lift。

    Args:
        brand:  品牌筛选
        radius: 统计半径（米）
        city:   城市筛选

    Returns:
        list[dict]: 按 lift 降序排列，每个元素包含:
                    category, avg_near_store（门店周边均值）,
                    avg_random（随机点位均值）, lift（富集倍数）
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        key = city if city else '__ALL__'

        rows = await conn.fetch('''
            SELECT poi_category, avg_near_store, avg_random, lift
            FROM poi_lift_cache
            WHERE radius = $1 AND brand = $2 AND city = $3
            ORDER BY lift DESC
        ''', radius, brand, key)

        return [
            {
                'category': r['poi_category'],
                'avg_near_store': float(r['avg_near_store']),
                'avg_random': float(r['avg_random']),
                'lift': float(r['lift']),
            }
            for r in rows
        ]
    finally:
        await conn.close()


@router.get('/brand-comparison')
async def brand_comparison(city: Optional[str] = Query(None, description='城市筛选')):
    """
    瑞幸 vs 星巴克品牌对比分析

    对比维度：
    1. 品牌门店数量：各品牌在目标区域的店铺总数
    2. 共址分析 (Co-location)：统计瑞幸门店中，200米范围内存在
       星巴克门店的数量和占比。co_location_rate = within_200m / total_luckin
    3. 平均最近距离：每个瑞幸门店到最近星巴克门店的距离的平均值

    业务洞察：
    - 高共址率 (>30%) 说明瑞幸采取"贴身战术"，跟随星巴克选址
    - 低共址率 (<10%) 说明瑞幸差异化选址，覆盖星巴克未触及的区域
    - avg_nearest_starbucks_m 反映两个品牌的地理距离竞争格局

    Args:
        city: 城市筛选（为空返回全国汇总）

    Returns:
        dict: {
            brand_counts: [{brand, count}, ...],
            avg_nearest_starbucks_m: float | None,
            co_location: {total_luckin, within_200m_of_starbucks, co_location_rate} | None
        }
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        key = city if city else '__ALL__'

        row = await conn.fetchrow('''
            SELECT luckin_count, starbucks_count, avg_nearest_sb_m, co_located_200m, co_location_rate
            FROM brand_compare_cache WHERE city = $1
        ''', key)

        # 缓存表中无数据时返回空结构
        if not row:
            return {
                'brand_counts': [],
                'avg_nearest_starbucks_m': None,
                'co_location': None,
            }

        return {
            'brand_counts': [
                {'brand': 'luckin', 'count': row['luckin_count']},
                {'brand': 'starbucks', 'count': row['starbucks_count']},
            ],
            'avg_nearest_starbucks_m': float(row['avg_nearest_sb_m']) if row['avg_nearest_sb_m'] else None,
            'co_location': {
                'total_luckin': row['luckin_count'],
                'within_200m_of_starbucks': row['co_located_200m'],
                # 共址率 = 200米内存在星巴克的门店数 / 瑞幸总门店数
                'co_location_rate': float(row['co_location_rate']) if row['co_location_rate'] else 0,
            },
        }
    finally:
        await conn.close()


@router.get('/city-tiers-stats')
async def city_tiers_stats():
    """
    按城市层级统计门店渗透情况

    SQL 逻辑：
    1. FROM stores INNER JOIN city_tiers — 关联门店表与城市层级维表
    2. GROUP BY tier_label, tier, brand — 按层级+品牌聚合
    3. COUNT(DISTINCT city) — 该层级中该品牌已进入的城市数量
    4. COUNT(*) — 该层级中该品牌的总门店数

    城市层级体系（依据第一财经新一线城市研究所分类）：
    - tier=1: 一线城市（北上广深）
    - tier=2: 新一线城市（成都、杭州、武汉等15城）
    - tier=3: 二线城市
    - tier=4: 三线城市
    - tier=5: 四线城市及以下

    Returns:
        list[dict]: 按 tier 升序、brand 字母序排列，每项包含:
                    tier_label, tier, brand, cities_entered, total_stores
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        rows = await conn.fetch('''
            SELECT
                ct.tier_label,
                ct.tier,
                s.brand,
                COUNT(DISTINCT s.city) AS cities_entered,
                COUNT(*) AS total_stores
            FROM stores s
            JOIN city_tiers ct ON s.city = ct.city_name
            GROUP BY ct.tier_label, ct.tier, s.brand
            ORDER BY ct.tier, s.brand
        ''')

        return [
            {
                'tier_label': r['tier_label'],
                'tier': r['tier'],
                'brand': r['brand'],
                'cities_entered': r['cities_entered'],
                'total_stores': r['total_stores'],
            }
            for r in rows
        ]
    finally:
        await conn.close()
