-- ============================================================
-- 空间关联查询 - 门店-POI 缓冲区统计与空间分析函数
-- ============================================================
-- 用途：提供空间查询的封装函数和分析查询模板
-- 执行顺序：在 schema.sql 建表后 + 数据采集完成后执行
-- 使用场景：
--   1. 在 compute_features.py 中调用 refresh_store_poi_stats() 批量计算
--   2. 在数据分析中单独查询某门店周边的 POI 分布
--   3. 竞品空间关系分析（瑞幸 vs 星巴克最近距离）
--   4. 共址分析与 Lift 值计算
--
-- 主要内容：
--   A. compute_store_poi_stats(p_store_id, p_radius) — 单门店 POI 统计函数
--   B. refresh_store_poi_stats() — 批量全量更新缓存表
--   C. 门店周边 Top-N POI 类型查询
--   D. 最近邻距离查询（瑞幸 vs 最近星巴克）
--   E. 共址分析（200m 内的瑞幸+星巴克）
--   F. random_points 表创建 + Lift 值计算公式
-- ============================================================

-- ============================================================
-- A. 单门店 POI 缓冲区统计函数
-- ============================================================
-- 功能：为指定门店计算指定半径内各类 POI 的数量
--
-- 参数：
--   p_store_id: 门店 ID
--   p_radius:   缓冲区半径（米），默认 500
--
-- 返回：TABLE(poi_category VARCHAR, poi_count BIGINT)
--   每行包含一类 POI 的类别和数量
--
-- 算法：
--   ST_DWithin(s.geom, p.geom, p_radius) — 判断 POI 是否在门店的半径范围内
--   GROUP BY p.category — 按 POI 类别聚合计数
--
-- 使用示例：
--   SELECT * FROM compute_store_poi_stats(1, 500);
-- ============================================================
CREATE OR REPLACE FUNCTION compute_store_poi_stats(
    p_store_id INT,
    p_radius INT DEFAULT 500
) RETURNS TABLE(poi_category VARCHAR, poi_count BIGINT) AS $$
BEGIN
    RETURN QUERY
    SELECT p.category, COUNT(*)::BIGINT
    FROM pois p
    JOIN stores s ON s.id = p_store_id
    WHERE ST_DWithin(s.geom, p.geom, p_radius)
    GROUP BY p.category;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- B. 批量全量更新 store_poi_stats 缓存表
-- ============================================================
-- 功能：清空 store_poi_stats 表并重新计算所有门店的所有半径所有类别的 POI 计数
--
-- 算法：
--   1. TRUNCATE store_poi_stats — 清空旧缓存
--   2. 外层循环遍历三个半径 (200, 500, 1000) — FOR r IN ARRAY
--   3. 内层循环遍历所有 POI 类别 — FOR cat IN SELECT DISTINCT
--   4. 内层子查询：对每个门店，统计半径内该类 POI 数量
--
-- 时间复杂度：O(n_stores x n_categories x n_radii)
--   对于 15,000+ 门店 x ~15 类别 x 3 半径，计算量较大
--   受益于 GiST 索引加速，预计执行时间 30 秒-2 分钟
--
-- 使用场景：
--   - 数据采集完成后首次计算
--   - POI 数据更新后重新计算
--   - 直接在 psql 中手动触发：SELECT refresh_store_poi_stats();
-- ============================================================
CREATE OR REPLACE FUNCTION refresh_store_poi_stats() RETURNS VOID AS $$
DECLARE
    r INT;
    cat VARCHAR;
BEGIN
    -- 清空旧缓存数据
    TRUNCATE store_poi_stats;

    -- 遍历三个缓冲区半径
    FOR r IN SELECT unnest(ARRAY[200, 500, 1000]) LOOP
        -- 遍历所有 POI 类别
        FOR cat IN SELECT DISTINCT category FROM pois LOOP
            INSERT INTO store_poi_stats (store_id, radius, poi_category, poi_count)
            SELECT
                s.id,
                r,
                cat,
                (SELECT COUNT(*) FROM pois p
                 WHERE ST_DWithin(s.geom, p.geom, r) AND p.category = cat)
            FROM stores s;
        END LOOP;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- ============================================================
-- C. 门店周边 Top-N POI 类型查询
-- ============================================================
-- 功能：查询某门店周边 500m 内数量最多的前 10 类 POI
--
-- 业务含义：
--   了解一个门店周边"有什么"，是最基础的环境画像分析
--   某类 POI 的数量占比反映了门店选址的环境特征
--   例如：若 office 占比 >50%，说明该门店位于办公区
--
-- 输出字段：
--   poi_category: POI 类别
--   poi_count:    该半径内数量
--   pct:          占所有 POI 的百分比
--
-- 注意：
--   - store_id=1 是示例值，实际使用需替换
--   - SUM OVER() 窗口函数计算全体总和，用于计算百分比
-- ============================================================
SELECT
    sps.poi_category,
    sps.poi_count,
    ROUND(sps.poi_count::NUMERIC / SUM(sps.poi_count) OVER() * 100, 1) AS pct
FROM store_poi_stats sps
WHERE sps.store_id = 1 AND sps.radius = 500
ORDER BY sps.poi_count DESC
LIMIT 10;

-- ============================================================
-- D. 最近邻距离查询（瑞幸门店 → 最近星巴克）
-- ============================================================
-- 功能：对每个瑞幸门店，找到距离最近的星巴克门店
--
-- 算法：
--   CROSS JOIN LATERAL + ORDER BY <-> 距离算子 + LIMIT 1
--   - <-> 是 PostGIS 的距离排序算子（KNN 最近邻搜索）
--   - LATERAL 子查询对每行独立执行一次
--   - 自动利用 GiST 索引，查询效率高
--
-- 业务含义：
--   - 分析瑞幸与星巴克的空间竞争/共生关系
--   - 若多数瑞幸门店距星巴克 <200m，可能采取"贴身竞争"策略
--   - 若距离分布均匀，可能更注重独立选址
--
-- 输出字段：
--   luckin_id/luckin_name: 瑞幸门店标识
--   city:                  所在城市
--   nearest_sb_id/name:    最近星巴克门店标识和名称
--   distance_meters:       最短距离（米），使用 geography 球面距离
--
-- 注意事项：
--   - 距离使用 ::geography 转换后的球面距离（考虑地球曲率）
--   - 若某城市无星巴克，该城市的瑞幸门店结果中 nearest_sb_* 为 NULL
-- ============================================================
SELECT
    l.id AS luckin_id,
    l.name AS luckin_name,
    l.city,
    sb.id AS nearest_sb_id,
    sb.name AS nearest_sb_name,
    ROUND(ST_Distance(l.geom::geography, sb.geom::geography)) AS distance_meters
FROM stores l
CROSS JOIN LATERAL (
    SELECT s2.id, s2.name, s2.geom
    FROM stores s2
    WHERE s2.brand = 'starbucks'
    ORDER BY l.geom <-> s2.geom    -- KNN 最近邻排序（利用 GiST 索引）
    LIMIT 1
) sb
WHERE l.brand = 'luckin';

-- ============================================================
-- E. 共址分析（同一商场/建筑物内的门店）
-- ============================================================
-- 功能：按城市统计存在"共址"关系（200m 内同时有瑞幸和星巴克）的门店
--
-- 算法：
--   ST_DWithin(geography, geography, 200) 判断两点球面距离 ≤200m
--   COUNT(DISTINCT CASE WHEN ...) 统计满足共址条件的门店数
--
-- 业务含义：
--   - co_located_200m: 200 米内有星巴克的瑞幸门店数
--   - 该数值除以 luckin_count 得到"共址率"
--   - 共址率高 → 瑞幸倾向与星巴克毗邻开店（竞争/借流策略）
--   - 共址率低 → 瑞幸倾向差异化选址
--
-- 输出字段：
--   city:             城市
--   luckin_count:     该城市瑞幸门店总数
--   starbucks_count:  该城市星巴克门店总数
--   co_located_200m:  200m 内同时有两品牌的门店数
-- ============================================================
SELECT
    l.city,
    COUNT(DISTINCT l.id) AS luckin_count,
    COUNT(DISTINCT sb.id) AS starbucks_count,
    COUNT(DISTINCT CASE
        WHEN ST_DWithin(l.geom::geography, sb.geom::geography, 200)
        THEN l.id END
    ) AS co_located_200m
FROM stores l
LEFT JOIN stores sb ON sb.brand = 'starbucks'
    AND sb.city = l.city
WHERE l.brand = 'luckin'
GROUP BY l.city
ORDER BY luckin_count DESC;

-- ============================================================
-- F. 随机采样点表 + POI Lift 值计算基础
-- ============================================================
-- 功能：为每个有门店的城市创建 1000 个随机采样点
--
-- 算法：
--   1. 获取每个城市在 admin_boundaries 中的多边形边界
--   2. 在边界 bbox 内随机生成经纬度坐标
--      - st_xmin/ymin + random() * (st_xmax - st_xmin)
--   3. 每个城市 CROSS JOIN generate_series(1, 1000) 生成 1000 个点
--
-- 用途：
--   - 作为选址模型的负样本（label=0）
--   - 作为 Lift 值计算的基准分布
--
-- Lift 值定义：
--   Lift(POI类别) = (门店周边该类POI平均数量) / (随机点周边该类POI平均数量)
--
-- 业务含义：
--   - Lift > 1: 该类 POI 在门店周边浓度高于随机水平 → 选址偏好
--   - Lift ≈ 1: 该类 POI 与门店选址无显著关系
--   - Lift < 1: 门店倾向于远离该类 POI
--
-- 注意：
--   - 随机点在 admin_boundaries 的 bbox 内生成，未严格检查是否在行政边界内
--   - 实际使用时需确保 pois 表和 stores 表已填充数据
-- ============================================================
CREATE TABLE IF NOT EXISTS random_points AS
SELECT
    id,
    ST_SetSRID(ST_MakePoint(
        lng_random,
        lat_random
    ), 4326) AS geom,
    city
FROM (
    SELECT
        row_number() OVER() AS id,
        s.city,
        -- 在城市边界 bbox 内随机生成点坐标
        st_xmin(b.geom) + random() * (st_xmax(b.geom) - st_xmin(b.geom)) AS lng_random,
        st_ymin(b.geom) + random() * (st_ymax(b.geom) - st_ymin(b.geom)) AS lat_random
    FROM (SELECT DISTINCT city FROM stores WHERE city IS NOT NULL) s
    LEFT JOIN admin_boundaries b ON b.name = s.city AND b.level = 'city'
    CROSS JOIN generate_series(1, 1000)   -- 每个城市生成 1000 个随机点
) sub;
