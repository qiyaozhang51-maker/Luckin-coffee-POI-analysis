-- ============================================================
-- 分析查询 - 时空演变 / 城市对比 / 空间聚类
-- ============================================================
-- 用途：提供 8 组核心分析查询，覆盖时间、空间、城市维度的分析需求
-- 执行顺序：在数据采集和特征计算全部完成后执行
-- 数据依赖：stores 表（门店）、pois 表（兴趣点）、city_tiers 表（城市层级）
--           store_poi_stats 表（门店-POI 统计，部分查询需要）
--           mv_city_stats 物化视图（城市级统计）
--           mv_store_clusters 物化视图（门店聚类结果）
--
-- 查询速览：
--   1. 按城市+品牌统计门店数（柱状图排名）
--   2. 月度新增门店趋势（时序折线图）
--   3. 城市层级渗透率（不同线级城市门店分布）
--   4. 标准差椭圆 SDE（逐年空间分布中心与方向偏移）
--   5. 空间聚类分析 DBSCAN（识别门店聚集区）
--   6. 城市扩张速度排名（同比增长率）
--   7. 门店密度 vs GDP/人口（社会经济相关性）
--   8. 物化视图刷新
-- ============================================================

-- ============================================================
-- 1. 按城市 + 品牌统计门店数量（用于柱状图排名）
-- ============================================================
-- 业务含义：
--   - 对比各城市的瑞幸门店规模，制作城市排名柱状图
--   - avg_lat/avg_lng 为城市内门店的地理中心，用于地图标注
--   - first_open/last_open 反映该城市门店扩张的时间跨度
--
-- 输出字段：
--   city:       城市名称
--   brand:      品牌标识
--   cnt:        门店数量
--   first_open: 首个门店开业日期
--   last_open:  最晚门店开业日期
--   avg_lat/lng: 门店空间中心点坐标
--
-- 可视化建议：
--   - 柱状图：TOP 20 城市门店数降序排列
--   - 地图散点：各城市中心点坐标标注
-- ============================================================
SELECT
    city,
    brand,
    COUNT(*) AS cnt,
    MIN(open_date) AS first_open,
    MAX(open_date) AS last_open,
    ROUND(AVG(ST_Y(geom))::NUMERIC, 4) AS avg_lat,
    ROUND(AVG(ST_X(geom))::NUMERIC, 4) AS avg_lng
FROM stores
WHERE city IS NOT NULL AND brand = 'luckin'
GROUP BY city, brand
ORDER BY cnt DESC
LIMIT 30;

-- ============================================================
-- 2. 按时间聚合：每月新增门店数与累计门店数
-- ============================================================
-- 业务含义：
--   - 追踪瑞幸和星巴克的月度扩张节奏
--   - new_stores: 当月新开门店数（反映扩张速度）
--   - cumulative_stores: 累计门店数（反映总规模趋势）
--   - 窗口函数 SUM() OVER (ORDER BY month) 实现累计求和
--
-- 算法说明：
--   DATE_TRUNC('month', open_date): 将日期截断到月初，按月聚合
--   SUM(COUNT(*)) OVER (PARTITION BY brand ORDER BY month): 按品牌分别累计
--   PARTITION BY brand 确保两个品牌独立累计
--
-- 可视化建议：
--   - 双轴折线图：左轴=新增数（柱状），右轴=累计数（折线）
--   - 不同颜色区分 luckin 和 starbucks
-- ============================================================
SELECT
    DATE_TRUNC('month', open_date) AS month,
    brand,
    COUNT(*) AS new_stores,
    SUM(COUNT(*)) OVER (PARTITION BY brand ORDER BY DATE_TRUNC('month', open_date)) AS cumulative_stores
FROM stores
WHERE open_date IS NOT NULL
GROUP BY DATE_TRUNC('month', open_date), brand
ORDER BY month;

-- ============================================================
-- 3. 城市层级渗透率分析
-- ============================================================
-- 业务含义：
--   - 分析不同城市层级（一线/新一线/二线）的门店分布
--   - cities_entered: 该层级中已进入的城市数
--   - total_stores: 该层级中的门店总数
--   - 计算"城市渗透率"= cities_entered / 该层级总城市数
--
-- 商业洞察：
--   - 一线城市门店密度是否饱和？
--   - 新一线城市是否为主要扩张方向？
--   - 二线城市渗透率低是否意味着下沉市场机会？
--
-- 依赖：city_tiers 表需已填充数据
-- ============================================================
SELECT
    ct.tier_label,
    ct.tier,
    s.brand,
    COUNT(DISTINCT s.city) AS cities_entered,
    COUNT(*) AS total_stores
FROM stores s
JOIN city_tiers ct ON s.city = ct.city_name
GROUP BY ct.tier_label, ct.tier, s.brand
ORDER BY ct.tier, s.brand;

-- ============================================================
-- 4. 标准差椭圆（SDE - Standard Deviational Ellipse）
-- ============================================================
-- 业务含义：
--   - 描述门店空间分布的中心位置和方向趋势
--   - center_geojson: 空间分布中心（每年所有门店的地理中心点）
--   - sde_geojson: 定向外包矩形（反映门店扩展的方向和范围）
--     - 椭圆的长轴方向 = 门店扩张的主要方向
--     - 椭圆面积变化 = 扩张速度
--
-- 算法说明：
--   ST_Centroid(ST_Collect(geom)): 计算点集的地理中心
--   ST_OrientedEnvelope(ST_Collect(geom)): 计算最小面积旋转矩形
--     （近似标准差椭圆，精确 SDE 需 ArcGIS/QGIS 等专用工具计算）
--   ST_AsGeoJSON(): 将 PostGIS 几何对象转为 GeoJSON 字符串，方便前端渲染
--
-- 可视化建议：
--   - 在地图上逐年渲染椭圆，观察扩张方向和速度变化
--   - 不同品牌用不同颜色
--
-- 注意：
--   - ST_OrientedEnvelope 是标准差椭圆的近似替代（真正 SDE 需用统计公式）
--   - 当门店数过少时（<3），ST_OrientedEnvelope 可能返回无效几何体
-- ============================================================
SELECT
    EXTRACT(YEAR FROM open_date) AS year,
    brand,
    ST_AsGeoJSON(ST_Centroid(ST_Collect(geom))) AS center_geojson,
    ST_AsGeoJSON(ST_OrientedEnvelope(ST_Collect(geom))) AS sde_geojson,
    COUNT(*) AS store_count
FROM stores
WHERE open_date IS NOT NULL
GROUP BY EXTRACT(YEAR FROM open_date), brand
ORDER BY year, brand;

-- ============================================================
-- 5. 空间聚类分析：DBSCAN 门店聚集区识别
-- ============================================================
-- 业务含义：
--   - 使用 DBSCAN 算法自动识别门店的"聚集区"
--   - 每个 cluster_id 代表一个门店密集区域（如商圈、CBD）
--   - cluster_size 大的聚集区可能代表高潜力区位
--
-- DBSCAN 参数说明：
--   eps := 0.003（度） — 约等于 330m（在纬度 30 度处）
--     含义：两个门店距离 ≤330m 时被视为"邻居"
--     选择依据：步行 3-5 分钟可达，代表同一商圈/街区范围
--   minpoints := 5 — 至少 5 个邻居才能形成一个聚类核心
--     含义：聚集区至少包含 5 家门店
--   PARTITION BY city — 每个城市独立聚类（避免跨城市误聚）
--
-- 注意：
--   - cluster_id IS NULL 的门店为"噪声点"（周边门店密度不足）
--   - 物化视图需要手动 REFRESH 更新
--
-- 可视化建议：
--   - 不同 cluster 用不同颜色标注在地图上
--   - cluster_size 映射为标记大小
--
-- 限定城市：仅分析一线和新一线中部分城市的聚类
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_store_clusters AS
SELECT
    id,
    name,
    city,
    geom,
    ST_ClusterDBSCAN(geom, eps := 0.003, minpoints := 5) OVER (PARTITION BY city) AS cluster_id
FROM stores
WHERE brand = 'luckin' AND city IN ('北京', '上海', '广州', '深圳', '成都', '杭州');

-- ============================================================
-- 各聚类统计（查询上一步生成的聚类结果）
-- ============================================================
-- 业务含义：
--   - 对每个城市的每个聚类，统计包含的门店数和地理中心
--   - cluster_size 反映该聚集区的门店密集程度
--   - cluster_center 可用于在地图上标注聚集区位置
--
-- 可视化建议：
--   - 地图上绘制各城市 Top 10 聚集区
--   - 气泡大小 = cluster_size
-- ============================================================
SELECT
    city,
    cluster_id,
    COUNT(*) AS cluster_size,
    ST_AsGeoJSON(ST_Centroid(ST_Collect(geom))) AS cluster_center
FROM mv_store_clusters
WHERE cluster_id IS NOT NULL
GROUP BY city, cluster_id
ORDER BY cluster_size DESC
LIMIT 50;

-- ============================================================
-- 6. 城市扩张速度排名
-- ============================================================
-- 业务含义：
--   - growth_rate_pct: 同比增长率 = (今年累计 - 去年累计) / 去年累计 * 100%
--   - 负数表示门店减少（关闭多于新开）
--   - 高增长率城市可能是当前扩张重点
--
-- 算法说明：
--   WITH yearly:  按城市和年份统计每年新增门店数
--   WITH ranked:
--     - SUM() OVER (PARTITION BY city ORDER BY yr) = 累计门店数
--     - LAG(cum_sum) OVER (...) = 上一年的累计门店数
--   growth_rate_pct = (cum_stores - prev_cum) / prev_cum * 100
--
-- 注意事项：
--   - 首年（prev_cum IS NULL）的增长率为 NULL（没有基数可比较）
--   - NULLIF(prev_cum, 0) 防止除零错误
--   - 限定 yr >= 2020 仅分析近 5 年趋势
--
-- 可视化建议：
--   - 横向条形图：按最新年份增长率排序
--   - 多城市折线图：每年增长率变化趋势
-- ============================================================
WITH yearly AS (
    SELECT
        city,
        EXTRACT(YEAR FROM open_date) AS yr,
        COUNT(*) AS new_stores
    FROM stores
    WHERE brand = 'luckin' AND open_date IS NOT NULL AND city IS NOT NULL
    GROUP BY city, EXTRACT(YEAR FROM open_date)
),
ranked AS (
    SELECT
        city,
        yr,
        new_stores,
        SUM(new_stores) OVER (PARTITION BY city ORDER BY yr) AS cum_stores,
        LAG(SUM(new_stores) OVER (PARTITION BY city ORDER BY yr)) OVER (PARTITION BY city ORDER BY yr) AS prev_cum
    FROM yearly
)
SELECT
    city,
    yr,
    new_stores,
    cum_stores,
    ROUND((cum_stores - COALESCE(prev_cum, 0))::NUMERIC / NULLIF(prev_cum, 0) * 100, 1) AS growth_rate_pct
FROM ranked
WHERE yr >= 2020
ORDER BY city, yr;

-- ============================================================
-- 7. 门店密度 vs 城市 GDP / 人口（社会经济相关性分析）
-- ============================================================
-- 业务含义：
--   - stores_per_100k_pop: 每 10 万人口拥有的门店数（门店渗透率指标）
--   - 对比不同城市的门店渗透率，判断是否已达到饱和
--
-- 商业洞察：
--   - 渗透率高的城市（如 > 10 / 10万人）→ 市场可能接近饱和
--   - 渗透率低但 GDP 高的城市 → 可能是有潜力的待开发市场
--   - 渗透率与 GDP/人口的相关系数 → 选址是否受经济/人口因素驱动
--
-- 算法：
--   stores_per_100k_pop = store_count / population * 100,000
--
-- 依赖：city_tiers 表需已填充数据
--
-- 可视化建议：
--   - 散点图：X轴=人口/GDP, Y轴=门店数，每个点一个城市
--   - 气泡图：气泡大小=stores_per_100k_pop
-- ============================================================
SELECT
    s.city,
    ct.tier_label,
    ct.population_2020,
    ct.gdp_2023_billion,
    COUNT(*) AS store_count,
    ROUND(COUNT(*)::NUMERIC / NULLIF(ct.population_2020, 0) * 100000, 2) AS stores_per_100k_pop
FROM stores s
JOIN city_tiers ct ON s.city = ct.city_name
WHERE s.brand = 'luckin'
GROUP BY s.city, ct.tier_label, ct.population_2020, ct.gdp_2023_billion
ORDER BY stores_per_100k_pop DESC;

-- ============================================================
-- 8. 刷新物化视图
-- ============================================================
-- 作用：手动更新 mv_city_stats 物化视图（城市级门店统计）
--
-- 刷新策略说明：
--   - 物化视图存储查询结果的快照，不会随基础表更新而自动更新
--   - 本次刷新使用 REFRESH MATERIALIZED VIEW（非 CONCURRENTLY 模式）
--   - 普通 REFRESH：执行期间锁表（阻塞读），但速度快
--   - CONCURRENTLY REFRESH：不锁表（允许并发读），但需要唯一索引且速度慢
--
-- 刷新时机：
--   - compute_features.py 执行完成后自动刷新
--   - 手动导入新数据后执行
--   - 建议每天夜间低峰期定时刷新
-- ============================================================
REFRESH MATERIALIZED VIEW mv_city_stats;
