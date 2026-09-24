-- ============================================================
-- 瑞幸咖啡空间分析项目 - 数据库 DDL（建库脚本）
-- ============================================================
-- 用途：创建项目所需的全部数据库对象（7 张表 + 索引 + 物化视图）
-- 执行顺序：本文件应作为整个数据管道的第一步执行
-- 环境要求：PostgreSQL 15+ + PostGIS 3.3+ 扩展
-- 执行方式：psql -U luckin -d luckin_spatial -f sql/schema.sql
--
-- 表结构速览：
--   1. admin_boundaries    — 行政边界表（省/市/区）
--   2. stores              — 门店数据表（瑞幸 + 星巴克）
--   3. pois                — POI 数据表（高德 + OSM 来源）
--   4. store_poi_stats     — 门店-POI 空间关联统计缓存表
--   5. city_tiers          — 城市层级表
--   6. kde_grid            — 核密度估计网格缓存表
--   7. prediction_grid     — 选址预测结果缓存表
--   M. mv_city_stats       — 物化视图：城市级统计
--
-- 坐标系说明：
--   - 所有 geom 字段使用 SRID 4326（WGS84 地理坐标系）
--   - WGS84 是 GPS 使用的全球标准经纬度坐标系
--   - 单位：度（经度 -180~180, 纬度 -90~90）
--   - 空间查询时通过 ::geography 转换启用球面距离计算
-- ============================================================

-- ============================================================
-- 扩展创建
-- postgis:         空间数据类型（POINT / POLYGON 等）和空间函数（ST_DWithin 等）
-- postgis_raster:  栅格数据支持（未直接使用但为未来扩展预留）
-- hstore:          OSM 数据导入所需（key-value 对类型）
-- ============================================================
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_raster;
CREATE EXTENSION IF NOT EXISTS hstore;

-- ============================================================
-- 1. 行政边界表 (admin_boundaries)
-- 用途：存储省、市、区三级行政区的多边形边界数据
-- 数据来源：高德地图行政区划 API 或国家地理信息中心
--
-- 字段说明：
--   id:        自增主键
--   name:      行政区名称（如 "北京市"、"朝阳区"）
--   level:     行政级别，限定 province/city/district 三级
--   parent_id: 父级行政区 ID（自引用外键，如区的 parent 为市）
--   adcode:    高德行政区划代码（6 位数字编码）
--   geom:      多边形边界几何数据，类型为 MULTIPOLYGON（部分行政区由多个不连续区域组成）
--
-- 索引说明：
--   idx_admin_geom: GiST 空间索引 — 加速空间范围查询（如判断点是否在区域内）
--   idx_admin_level: B-tree 索引 — 加速按行政级别过滤
--   idx_admin_name:  B-tree 索引 — 加速按名称查找
-- ============================================================
CREATE TABLE IF NOT EXISTS admin_boundaries (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,              -- 行政区名称
    level VARCHAR(20) NOT NULL CHECK (level IN ('province', 'city', 'district')),
    parent_id INT REFERENCES admin_boundaries(id),  -- 上级行政区
    adcode VARCHAR(20),                      -- 高德行政区划代码
    geom GEOMETRY(MULTIPOLYGON, 4326) NOT NULL
);

-- GiST 空间索引（加速原理见下方说明）
CREATE INDEX idx_admin_geom ON admin_boundaries USING GIST(geom);
CREATE INDEX idx_admin_level ON admin_boundaries(level);
CREATE INDEX idx_admin_name ON admin_boundaries(name);

-- ============================================================
-- 2. 门店数据表 (stores)
-- 用途：存储瑞幸咖啡和星巴克的门店信息
-- 数据来源：高德地图 POI 搜索 API（fetch_stores.py）
--
-- 字段说明：
--   id:          自增主键
--   name:        门店名称（如 "瑞幸咖啡(望京SOHO店)"）
--   brand:       品牌标识，限定 luckin 或 starbucks
--   address:     门店详细地址
--   lng/lat:     经纬度（WGS84 坐标系），冗余存储便于非空间查询
--   geom:        POINT 几何字段，SRID 4326（WGS84 坐标系）
--   city:        所在城市
--   district:    所在行政区（区/县）
--   province:    所在省份
--   adcode:      高德行政区划代码
--   open_date:   开业日期（数据来源：高德 API 扩展信息，可能不完整）
--   store_type:  门店类型：快取店/悠享店/外卖厨房/旗舰店/unknown
--   status:      经营状态：营业中/已关闭/暂停营业/unknown
--   confidence:  开业日期置信度（0-1），1.0=确认，<1.0=推测
--   data_source: 数据来源标识（amap=高德地图, osm=OpenStreetMap）
--   created_at:  记录创建时间
--   updated_at:  记录最后更新时间
--
-- 数据类型选择原因：
--   DOUBLE PRECISION: 经纬度需高精度（小数点后 6 位 = 0.11m 精度）
--   GEOMETRY(POINT, 4326): PostGIS 原生空间类型，支持空间索引和空间函数
--   VARCHAR 长度依据：门店名通常 <200 字，地址通常 <500 字
--
-- 索引说明：
--   idx_stores_geom:       GiST 空间索引 — 加速空间查询
--   idx_stores_brand:      品牌过滤
--   idx_stores_city:       城市过滤
--   idx_stores_open_date:  时间范围查询（按开业时间分析）
--   idx_stores_brand_city: 品牌+城市联合查询（最常用组合）
--   idx_stores_brand_date: 品牌+时间联合查询（扩张时序分析）
-- ============================================================
CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    brand VARCHAR(50) NOT NULL CHECK (brand IN ('luckin', 'starbucks')),
    address VARCHAR(500),
    lng DOUBLE PRECISION NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(POINT, 4326) NOT NULL,
    city VARCHAR(100),
    district VARCHAR(100),
    province VARCHAR(100),
    adcode VARCHAR(20),
    open_date DATE,
    store_type VARCHAR(50) CHECK (store_type IN ('快取店', '悠享店', '外卖厨房', '旗舰店', 'unknown')),
    status VARCHAR(20) DEFAULT '营业中' CHECK (status IN ('营业中', '已关闭', '暂停营业', 'unknown')),
    confidence DOUBLE PRECISION DEFAULT 1.0,  -- 开业日期置信度 0-1
    data_source VARCHAR(50) DEFAULT 'amap',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_stores_geom ON stores USING GIST(geom);
CREATE INDEX idx_stores_brand ON stores(brand);
CREATE INDEX idx_stores_city ON stores(city);
CREATE INDEX idx_stores_open_date ON stores(open_date);
CREATE INDEX idx_stores_brand_city ON stores(brand, city);
CREATE INDEX idx_stores_brand_date ON stores(brand, open_date);

-- ============================================================
-- 3. POI 数据表 (pois)
-- 用途：存储兴趣点数据，包括高德 API 和 OSM 两个来源
-- 数据来源：fetch_pois.py（高德）+ import_osm.py（OSM）
--
-- 字段说明：
--   id:            自增主键
--   name:          POI 名称
--   category:      POI 大类（本项目统一分类：office/mall/metro/bus/restaurant/
--                  cafe/tea/residential/university/school/convenience/supermarket/
--                  bank/hospital/road/other）
--   sub_category:  POI 子类（更精细分类，如 "中餐厅"、"写字楼"）
--   lng/lat:       经纬度坐标
--   geom:          POINT 几何字段
--   address:       详细地址
--   city/district: 所在城市/行政区
--   adcode:        行政区划代码
--   data_source:   数据来源（amap=高德地图, osm=OpenStreetMap）
--   amap_typecode: 高德原始类型编码（仅高德数据，6 位数字编码）
--   created_at:    记录创建时间
--
-- 索引说明：
--   idx_pois_geom:         GiST 空间索引 — 加速空间范围查询（ST_DWithin 等）
--   idx_pois_category:     按类别过滤
--   idx_pois_city:         按城市过滤
--   idx_pois_category_city: 类别+城市联合查询（最常用组合）
--
-- GiST 空间索引加速原理：
--   GiST（Generalized Search Tree）是 PostGIS 的默认空间索引实现。
--   它使用 R-Tree 数据结构将二维空间组织为层层嵌套的矩形框（bounding box）。
--   查询时先通过 bounding box 快速排除不可能满足条件的候选对象，
--   再对剩余少量候选对象进行精确的空间运算（如球面距离计算）。
--   典型加速比：10x - 100x（数据量越大加速效果越明显）。
-- ============================================================
CREATE TABLE IF NOT EXISTS pois (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    category VARCHAR(50) NOT NULL,
    sub_category VARCHAR(100),
    lng DOUBLE PRECISION NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    geom GEOMETRY(POINT, 4326) NOT NULL,
    address VARCHAR(500),
    city VARCHAR(100),
    district VARCHAR(100),
    adcode VARCHAR(20),
    data_source VARCHAR(50) DEFAULT 'amap',
    amap_typecode VARCHAR(20),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_pois_geom ON pois USING GIST(geom);
CREATE INDEX idx_pois_category ON pois(category);
CREATE INDEX idx_pois_city ON pois(city);
CREATE INDEX idx_pois_category_city ON pois(category, city);

-- ============================================================
-- 4. 门店-POI 空间关联统计缓存表 (store_poi_stats)
-- 用途：缓存每个门店在每个半径内各类 POI 的数量，避免重复计算
-- 计算方式：compute_features.py 中的 compute_store_poi_stats()
--
-- 字段说明：
--   store_id:     门店 ID（外键关联 stores，级联删除）
--   radius:       缓冲区半径（米），限定 200/500/1000 三个值
--   poi_category:  POI 类别
--   poi_count:     该半径内该类 POI 的数量
--   last_updated:  最后更新时间
--
-- 主键设计：(store_id, radius, poi_category)
--   含义：每个门店在每个半径下，每类 POI 只有一个计数值
--
-- 三个半径的业务含义：
--   200m  = 步行 2-3 分钟可达（核心商圈/门店直接邻居）
--   500m  = 步行 5-7 分钟可达（邻里社区范围）
--   1000m = 步行 10-15 分钟可达（区域辐射范围）
-- ============================================================
CREATE TABLE IF NOT EXISTS store_poi_stats (
    store_id INT REFERENCES stores(id) ON DELETE CASCADE,
    radius INT NOT NULL CHECK (radius IN (200, 500, 1000)),
    poi_category VARCHAR(50) NOT NULL,
    poi_count INT NOT NULL DEFAULT 0,
    last_updated TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (store_id, radius, poi_category)
);

CREATE INDEX idx_sps_store ON store_poi_stats(store_id);

-- ============================================================
-- 5. 城市层级表 (city_tiers)
-- 用途：存储 20 个重点城市的层级分类和社会经济数据
-- 数据来源：compute_features.py 中的 compute_city_tiers()
--
-- 字段说明：
--   id:                  自增主键
--   city_name:           城市名称（唯一约束，与 stores.city 关联）
--   tier:                城市层级编号：1=一线, 2=新一线, 3=二线, 4=三线, 5=其他
--   tier_label:          层级标签（中文）
--   province:            所属省份/直辖市
--   population_2020:     2020 年常住人口（七普数据，单位：人）
--   gdp_2023_billion:    2023 年 GDP（单位：亿元人民币）
-- ============================================================
CREATE TABLE IF NOT EXISTS city_tiers (
    id SERIAL PRIMARY KEY,
    city_name VARCHAR(100) NOT NULL UNIQUE,
    tier INT NOT NULL CHECK (tier BETWEEN 1 AND 5),
    tier_label VARCHAR(20) NOT NULL,
    province VARCHAR(100),
    population_2020 BIGINT,
    gdp_2023_billion DOUBLE PRECISION
);

-- ============================================================
-- 6. 核密度估计网格缓存表 (kde_grid)
-- 用途：缓存门店空间分布的核密度网格（用于热力图可视化）
-- 计算方式：compute_features.py 中的 compute_kde_grid()
--
-- 字段说明：
--   id:              自增主键
--   cell_geom:       网格多边形几何（POLYGON 类型）
--   brand:           品牌标识
--   year:            年份（按开业年份分组统计密度）
--   density:         该网格内的门店数量（密度值）
--   city:            所在城市（NULL 表示跨城市网格）
--   cell_size_meters: 网格边长（米），默认 500
--
-- 索引说明：
--   idx_kde_grid_geom:      空间索引
--   idx_kde_grid_brand_year: 品牌+年份联合查询（时序密度变化分析）
-- ============================================================
CREATE TABLE IF NOT EXISTS kde_grid (
    id SERIAL PRIMARY KEY,
    cell_geom GEOMETRY(POLYGON, 4326) NOT NULL,
    brand VARCHAR(50) NOT NULL,
    year INT NOT NULL,
    density DOUBLE PRECISION NOT NULL,
    city VARCHAR(100),
    cell_size_meters INT DEFAULT 500
);

CREATE INDEX idx_kde_grid_geom ON kde_grid USING GIST(cell_geom);
CREATE INDEX idx_kde_grid_brand_year ON kde_grid(brand, year);

-- ============================================================
-- 7. 选址预测结果缓存表 (prediction_grid)
-- 用途：缓存 XGBoost 模型对城市网格的选址评分结果
-- 计算方式：train_model.py 训练后对城市网格进行预测
--
-- 字段说明：
--   id:              自增主键
--   cell_geom:       网格多边形几何
--   city:            所在城市
--   score:           选址适宜度评分（0-1，越高越适合开店）
--   features:        该网格的特征值（JSONB 格式，用于可视化展示）
--   cell_size_meters: 网格边长（米）
--
-- 索引说明：
--   idx_pred_grid_geom:  空间索引
--   idx_pred_grid_score: 按评分降序索引（快速查找最优候选区域，DESC 排序）
-- ============================================================
CREATE TABLE IF NOT EXISTS prediction_grid (
    id SERIAL PRIMARY KEY,
    cell_geom GEOMETRY(POLYGON, 4326) NOT NULL,
    city VARCHAR(100),
    score DOUBLE PRECISION NOT NULL,
    features JSONB,
    cell_size_meters INT DEFAULT 500
);

CREATE INDEX idx_pred_grid_geom ON prediction_grid USING GIST(cell_geom);
-- 降序索引：加速 ORDER BY score DESC 查询（查找最高分候选区域）
CREATE INDEX idx_pred_grid_score ON prediction_grid(score DESC);

-- ============================================================
-- 物化视图：城市级统计 (mv_city_stats)
-- 用途：缓存每个城市每个品牌的门店数量、首末开业日期、空间中心点
--
-- 字段说明：
--   city:            城市名称
--   brand:           品牌标识
--   store_count:     该城市该品牌的门店总数
--   first_open_date: 该城市首个门店开业日期
--   last_open_date:  该城市最晚门店开业日期
--   centroid:        该城市该品牌门店的空间中心点（ST_Centroid of ST_Collect）
--
-- 刷新策略：
--   - 物化视图不会自动更新，需要手动执行 REFRESH MATERIALIZED VIEW
--   - 刷新时机：每次执行 compute_features.py 末尾自动刷新
--   - 如需实时数据，应改为普通 VIEW（但查询性能会下降）
--
-- 注意：CONCURRENTLY 刷新需要唯一索引支持（本视图未创建，
--       因此使用普通 REFRESH 会在刷新期间锁表）
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_city_stats AS
SELECT
    s.city,
    s.brand,
    COUNT(*) AS store_count,
    MIN(s.open_date) AS first_open_date,
    MAX(s.open_date) AS last_open_date,
    ST_Centroid(ST_Collect(s.geom)) AS centroid
FROM stores s
WHERE s.city IS NOT NULL
GROUP BY s.city, s.brand;

CREATE INDEX idx_mv_city_stats_city ON mv_city_stats(city);
