"""
空间特征计算脚本
================
数据管道位置：第 2 步（特征计算阶段）
前置依赖：fetch_stores.py + fetch_pois.py + import_osm.py 已完成
输入数据：stores 表（门店）、pois 表（兴趣点）、random_points 表（随机采样点）
输出数据：store_poi_stats 表、kde_grid 表、city_tiers 表、mv_city_stats 物化视图

计算模块（按执行顺序）：
  [1/4] compute_store_poi_stats  — 门店-POI 缓冲区空间关联统计
  [2/4] compute_kde_grid         — 核密度估计网格（KDE）
  [3/4] compute_lift_scores      — POI Lift 值计算
  [4/4] compute_city_tiers       — 城市层级数据导入

=== 模块详解 ===

[1/4] 门店-POI 缓冲区空间关联统计 (compute_store_poi_stats)
  算法：对每个门店，以三个半径（200m / 500m / 1000m）建立圆形缓冲区，
        使用 PostGIS ST_DWithin() 函数统计各类 POI 在缓冲区内的数量。
  ST_DWithin 说明：
    - ST_DWithin(geom1::geography, geom2::geography, radius) 使用地理坐标系
      计算两点之间的球面距离（考虑地球曲率），比平面坐标系更精确
    - 参数 ::geography 将 GEOMETRY 类型转为 GEOGRAPHY，启用球面距离计算
    - 时间复杂度：O(n_stores * n_categories * n_radii)，受益于 GiST 空间索引加速
  三个半径的业务含义：
    - 200m（步行圈）：步行 2-3 分钟可达范围，核心商圈
    - 500m（邻里圈）：步行 5-7 分钟可达范围，社区级覆盖
    - 1000m（辐射圈）：步行 10-15 分钟可达范围，区域级覆盖

[2/4] 核密度估计网格 (compute_kde_grid)
  算法（简化版）：使用 PostGIS ST_SquareGrid() 创建规则方形网格，
                 统计每个网格内各品牌的门店数量作为密度估计。
  注意：这是简化的 KDE，正式 KDE 应使用核函数（如高斯核）进行连续密度估计。
  网格尺寸 cell_size：默认 500m（对应城市街区尺度）

[3/4] POI Lift 值计算 (compute_lift_scores)
  Lift 值定义：
    Lift = (门店周边某类 POI 平均数量) / (随机采样点周边同类型 POI 平均数量)
  业务含义：
    - Lift > 1：该类 POI 在门店周边的密度高于随机分布，说明门店倾向于选址在该类 POI 附近
    - Lift = 1：门店选址与该类 POI 无显著相关性
    - Lift < 1：门店选址倾向于避开该类 POI
  用途示例：
    - 若 "office_500" 的 Lift = 3.5，说明瑞幸门店 500m 范围内的办公楼数量
      是随机点位的 3.5 倍，即瑞幸倾向于开在办公区附近

[4/4] 城市层级数据导入 (compute_city_tiers)
  - 导入 20 个城市的层级、人口（2020 七普）、GDP（2023）数据
  - 层级分类：1=一线、2=新一线、3=二线、4+=其他
  - 数据来源：国家统计局、各城市统计年鉴

预计运行时间：约 10-30 分钟（取决于门店和 POI 数量）

依赖：
  - config.DB_CONFIG
  - sql/schema.sql 中 store_poi_stats、kde_grid、city_tiers 表需先建好
  - sql/spatial_join.sql 中 random_points 表需先生成

执行方式：
  python scripts/compute_features.py
"""
import asyncio
import asyncpg
import numpy as np
import pandas as pd
from pathlib import Path
from config import DB_CONFIG

# ============================================================
# 缓冲区分析半径（单位：米）
# 三个半径对应不同的空间分析尺度：
#   200m  = 步行 2-3 分钟可达（核心商圈）
#   500m  = 步行 5-7 分钟可达（邻里社区）
#   1000m = 步行 10-15 分钟可达（区域辐射）
# ============================================================
RADII = [200, 500, 1000]


async def compute_store_poi_stats(conn):
    """计算每个门店在各半径范围内各类 POI 的数量。

    算法说明：
    1. 获取 pois 表中所有不重复的 category 值
    2. 对每个半径（200/500/1000）和每个分类，执行空间关联查询
    3. 使用 ST_DWithin(geom1::geography, geom2::geography, radius) 进行球面距离判断
    4. 结果写入 store_poi_stats 表（ON CONFLICT 时更新计数）
    5. store_poi_stats 表的主键为 (store_id, radius, poi_category)，保证唯一性

    空间索引加速原理：
    - 查询 planer 会自动利用 idx_stores_geom 和 idx_pois_geom 两个 GiST 索引
    - GiST（Generalized Search Tree）是 PostGIS 的 R-Tree 实现
    - 加速原理：先用 bounding box 快速过滤候选 POI，再精确计算球面距离
    - 比全表扫描快 10-100 倍（取决于数据量）

    Args:
        conn: asyncpg 数据库连接

    Returns:
        int: 写入 store_poi_stats 表的记录总数
    """
    print('\n[1/4] 计算门店-POI空间关联...')

    # 获取数据库中所有 POI 类别（用于遍历）
    categories = await conn.fetch('SELECT DISTINCT category FROM pois')
    cats = [r['category'] for r in categories]
    print(f'  POI类别: {cats}')

    total = 0
    for radius in RADII:
        for cat in cats:
            result = await conn.execute('''
                INSERT INTO store_poi_stats (store_id, radius, poi_category, poi_count)
                SELECT
                    s.id,
                    $1::int,
                    $2::text,
                    (SELECT COUNT(*)
                     FROM pois p
                     WHERE ST_DWithin(s.geom::geography, p.geom::geography, $3::float8)
                       AND p.category = $2)
                FROM stores s
                ON CONFLICT (store_id, radius, poi_category) DO UPDATE
                SET poi_count = EXCLUDED.poi_count, last_updated = NOW()
            ''', radius, cat, float(radius))
            # 从 "INSERT 0 N" 命令标签中提取影响行数（N 可能较大）
            count = int(result.split()[-1]) if 'INSERT' in result else 0
            total += count

    print(f'  空间关联计算完成，共 {total} 条记录')
    return total


async def compute_kde_grid(conn, cell_size: int = 500):
    """计算核密度估计网格（简化版：网格内门店计数）。

    算法步骤（简化 KDE）：
    1. 使用 ST_Extent 获取某品牌门店的地理外包矩形（bounding box）
    2. 使用 ST_Expand 将 bbox 向外扩展 5000m（避免边界效应）
    3. 使用 ST_SquareGrid(cell_size, bbox) 生成规则方形网格
    4. 统计每个网格内各年份的门店数量
    5. 将结果写入 kde_grid 表

    注意：这是简化的 KDE 实现。完整的 KDE 应使用核函数（如高斯核）
    对每个门店点进行连续密度估计，而非简单的网格计数。
    此处使用网格计数作为近似，计算效率更高，适用于大规模数据。

    参数说明：
    - cell_size: 网格边长（米），默认 500m
      选择 500m 的依据：街区尺度，既能反映局部密度差异，又不会产生过多网格

    Args:
        conn:      asyncpg 数据库连接
        cell_size: 网格边长（米），默认 500

    Returns:
        None（结果写入 kde_grid 表）
    """
    print(f'\n[2/4] 计算核密度网格 (cell={cell_size}m)...')

    for brand in ['luckin', 'starbucks']:
        # 创建网格：以门店的外包矩形为边界，向外扩展 5000m
        result = await conn.execute('''
            INSERT INTO kde_grid (cell_geom, brand, year, density, city, cell_size_meters)
            WITH bounds AS (
                -- 获取品牌的扩展外包矩形
                SELECT ST_Expand(ST_Extent(geom), 5000) AS bbox
                FROM stores WHERE brand = $1
            ),
            grid AS (
                -- 在 bbox 内生成规则方形网格
                SELECT
                    (ST_SquareGrid($2, bbox)).geom AS cell_geom
                FROM bounds
            ),
            counts AS (
                -- 统计每个网格内各年份的门店数量
                SELECT
                    g.cell_geom,
                    EXTRACT(YEAR FROM s.open_date)::INT AS yr,
                    COUNT(s.id) AS cnt
                FROM grid g
                LEFT JOIN stores s ON s.brand = $1
                    AND ST_Within(s.geom, g.cell_geom)
                WHERE s.open_date IS NOT NULL
                GROUP BY g.cell_geom, yr
            )
            SELECT cell_geom, $1, yr, cnt, NULL, $2
            FROM counts
            WHERE cnt > 0
        ''', brand, cell_size)

        print(f'  {brand}: KDE网格计算完成')

    print('  KDE网格计算完成')


async def compute_lift_scores(conn, radius: int = 500):
    """计算各类 POI 在门店周边的 Lift 值。

    Lift 值计算公式：
      Lift(category) = avg_poi_near_store(category) / avg_poi_near_random(category)

    其中：
      - avg_poi_near_store:  所有门店在半径 R 内该类 POI 的平均数量
      - avg_poi_near_random: 所有随机采样点在半径 R 内该类 POI 的平均数量

    Lift 值的统计学含义：
      - Lift = 1:  该类 POI 在门店周边和随机点位的密度相同，无明显偏好
      - Lift > 1:  门店周边该类 POI 密度高于随机水平，表明门店倾向于靠近该类 POI
      - Lift < 1:  门店周边该类 POI 密度低于随机水平，表明门店倾向于远离该类 POI
      - Lift 值越大，该 POI 类型与门店选址的相关性越强

    业务应用：
      选址模型的特征重要性排序依据，Lift 值最高的 POI 类别
      代表瑞幸门店选址时最看重的周边环境因素。

    注意：
      - 需要 random_points 表已填充（见 sql/spatial_join.sql）
      - 使用 COALESCE 和 NULLIF 防止除零错误

    Args:
        conn:   asyncpg 数据库连接
        radius: 缓冲区半径（米），默认 500

    Returns:
        list[asyncpg.Record]: Lift 值列表，按降序排列
    """
    print(f'\n[3/4] 计算POI Lift值 (radius={radius}m)...')

    rows = await conn.fetch('''
        WITH store_poi_avg AS (
            -- 门店周边各类 POI 的平均数量
            SELECT
                sps.poi_category,
                AVG(sps.poi_count) AS avg_near_store
            FROM store_poi_stats sps
            WHERE sps.radius = $1
            GROUP BY sps.poi_category
        ),
        random_poi_avg AS (
            -- 随机采样点周边各类 POI 的平均数量
            SELECT
                p.category,
                COUNT(*)::FLOAT / (
                    SELECT COUNT(*) FROM random_points
                ) AS avg_random
            FROM pois p
            CROSS JOIN random_points rp
            WHERE ST_DWithin(p.geom::geography, rp.geom::geography, $1)
            GROUP BY p.category
        )
        SELECT
            spa.poi_category,
            spa.avg_near_store,
            COALESCE(rpa.avg_random, 0.0001) AS avg_random,
            -- Lift = 门店周边均值 / 随机点均值
            ROUND((spa.avg_near_store / NULLIF(COALESCE(rpa.avg_random, 0.0001), 0))::NUMERIC, 2) AS lift
        FROM store_poi_avg spa
        LEFT JOIN random_poi_avg rpa ON spa.poi_category = rpa.category
        ORDER BY lift DESC
    ''', radius)

    # 打印 Lift 值表格
    print(f'  {"POI类别":<15} {"门店周边均值":>12} {"随机均值":>10} {"Lift":>8}')
    print(f'  {"-"*45}')
    for row in rows:
        print(f'  {row["poi_category"]:<15} {row["avg_near_store"]:>12.2f} {row["avg_random"]:>10.4f} {row["lift"]:>8.2f}')

    return rows


async def compute_city_tiers(conn):
    """导入 20 个重点城市的层级、人口和 GDP 数据到 city_tiers 表。

    数据项说明：
    - city_name:         城市名称（中文）
    - tier:              城市层级（1=一线, 2=新一线, 3=二线, 4=三线, 5=其他）
    - tier_label:        层级标签（一线 / 新一线 / 二线）
    - province:          所属省份/直辖市
    - population_2020:   常住人口（2020 年第七次全国人口普查数据）
    - gdp_2023_billion:  地区生产总值（2023 年，单位：亿元人民币）

    数据来源：
    - 人口数据：第七次全国人口普查公报（2021 年 5 月发布）
    - GDP 数据：各城市 2023 年国民经济和社会发展统计公报
    - 层级分类：第一财经·新一线城市研究所

    ON CONFLICT 策略：
    - 若 city_name 已存在，更新 tier / population / gdp 字段
    - 保证脚本可重复执行且数据保持最新

    Args:
        conn: asyncpg 数据库连接

    Returns:
        None（数据写入 city_tiers 表）
    """
    print(f'\n[4/4] 导入城市层级数据...')

    tiers = [
        # === 一线城市（4 个）===
        ('北京', 1, '一线', '北京', 21893095, 4376.1),
        ('上海', 1, '一线', '上海', 24870895, 4721.9),
        ('广州', 1, '一线', '广东', 18676605, 3035.6),
        ('深圳', 1, '一线', '广东', 17560000, 3460.6),
        # === 新一线城市（10 个）===
        ('成都', 2, '新一线', '四川', 20937757, 2201.6),
        ('杭州', 2, '新一线', '浙江', 11936010, 2005.9),
        ('重庆', 2, '新一线', '重庆', 32054159, 3014.6),
        ('武汉', 2, '新一线', '湖北', 12326500, 2001.2),
        ('西安', 2, '新一线', '陕西', 12952907, 1201.1),
        ('苏州', 2, '新一线', '江苏', 12748262, 2465.3),
        ('南京', 2, '新一线', '江苏', 9314685, 1742.1),
        ('长沙', 2, '新一线', '湖南', 10047914, 1433.1),
        ('郑州', 2, '新一线', '河南', 12600574, 1361.8),
        ('天津', 2, '新一线', '天津', 13866009, 1673.7),
        # === 二线城市（6 个）===
        ('合肥', 3, '二线', '安徽', 9369881, 1267.3),
        ('福州', 3, '二线', '福建', 8291268, 1136.1),
        ('厦门', 3, '二线', '福建', 5163970, 806.6),
        ('昆明', 3, '二线', '云南', 8460088, 786.4),
        ('沈阳', 3, '二线', '辽宁', 9027781, 812.2),
        ('青岛', 3, '二线', '山东', 10071722, 1576.0),
    ]

    for (city, tier, label, prov, pop, gdp) in tiers:
        await conn.execute('''
            INSERT INTO city_tiers (city_name, tier, tier_label, province, population_2020, gdp_2023_billion)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (city_name) DO UPDATE SET
                tier = EXCLUDED.tier,
                population_2020 = EXCLUDED.population_2020,
                gdp_2023_billion = EXCLUDED.gdp_2023_billion
        ''', city, tier, label, prov, pop, gdp)

    print(f'  {len(tiers)} 个城市层级数据导入完成')


async def main():
    """主函数：依次执行 4 个特征计算模块，最后刷新物化视图。

    执行顺序：
    [1/4] compute_store_poi_stats  → 门店-POI 缓冲区统计
    [2/4] compute_kde_grid         → 核密度估计网格
    [3/4] compute_lift_scores      → POI Lift 值计算
    [4/4] compute_city_tiers       → 城市层级数据导入
    最后：REFRESH MATERIALIZED VIEW mv_city_stats → 更新城市统计视图

    注意：前三步依赖 pois 和 stores 表有数据，请确保采集脚本已执行完毕。
    """
    conn = await asyncpg.connect(**DB_CONFIG)

    # 按顺序执行特征计算
    await compute_store_poi_stats(conn)
    await compute_kde_grid(conn)
    await compute_lift_scores(conn)
    await compute_city_tiers(conn)

    # 刷新物化视图 mv_city_stats（城市级门店统计）
    # 物化视图不会自动更新，需要在数据变更后手动 REFRESH
    await conn.execute('REFRESH MATERIALIZED VIEW mv_city_stats')
    print('\n物化视图已刷新')

    await conn.close()
    print('\n========== 特征计算全部完成 ==========')


if __name__ == '__main__':
    asyncio.run(main())
