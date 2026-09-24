"""
门店相关 API 路由模块

本模块在系统架构中位于接口层，负责处理所有与门店数据相关的 HTTP 请求，
包括门店列表查询、最近门店搜索、热力图数据生成、时间轴统计以及城市列表。

核心能力：
- 空间最近邻查询（PostGIS <-> 运算符，利用 GiST 索引加速 KNN）
- 动态 SQL 条件拼接与参数化分页
- ST_Collect + ST_AsGeoJSON 聚合门店几何数据为前端热力图
- 门店周边 POI 统计关联（预计算缓存表 store_poi_stats）

所有端点均使用 asyncpg 直连 PostgreSQL/PostGIS 数据库，
在 finally 块中确保每次请求后连接关闭，防止连接泄漏。
"""
from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from ..database import get_db, parse_db_url, settings
from ..models.schemas import StoreResponse, StoreWithPOI, CityStatsResponse, TimelineDataPoint
import asyncpg
import json

router = APIRouter(prefix='/api/stores', tags=['stores'])


def _row_to_store(row) -> dict:
    """
    将 asyncpg 查询结果行转换为前端可消费的字典

    处理三大类数据类型转换以确保 JSON 序列化兼容：
    1. 数值类型统一为 float — asyncpg 可能返回 Decimal 类型
    2. 日期类型转为 ISO 字符串 — JSON 不支持原生 date/datetime
    3. 其他字段保持原样透传

    Args:
        row: asyncpg.Record 对象，数据库查询的单行结果

    Returns:
        dict: 所有字段均可由 FastAPI JSONResponse 直接序列化的字典
    """
    d = dict(row)
    # 确保经纬度为 float（asyncpg 异步驱动可能返回 Decimal 类型）
    d['lng'] = float(d.get('lng', 0)) if d.get('lng') else 0
    d['lat'] = float(d.get('lat', 0)) if d.get('lat') else 0
    # 日期字段转 ISO 字符串，保证 JSON 可序列化
    for key in ('open_date', 'created_at', 'updated_at'):
        if d.get(key):
            d[key] = str(d[key])
    return d


@router.get('')
async def list_stores(
    brand: Optional[str] = Query(None, description='品牌筛选：luckin 或 starbucks'),
    city: Optional[str] = Query(None, description='城市名称筛选'),
    open_date_from: Optional[str] = Query(None, description='开业日期起始（含），格式 YYYY-MM-DD'),
    open_date_to: Optional[str] = Query(None, description='开业日期截止（含），格式 YYYY-MM-DD'),
    limit: int = Query(100, ge=1, le=5000, description='每页条数，范围 1~5000'),
    offset: int = Query(0, ge=0, description='分页偏移量'),
):
    """
    门店列表查询 — 支持多条件筛选与分页

    查询构建策略（动态 SQL + 参数化）：
    1. 基础模板 SELECT * FROM stores WHERE 1=1（1=1 作为条件占位符）
    2. 根据传入的筛选参数逐个追加 AND 条件
    3. 使用 $N（N 递增）占位符进行参数绑定，杜绝 SQL 注入
    4. 先执行 COUNT(*) 获取总数（前端分页组件需要 total）
    5. 再执行带 LIMIT/OFFSET 的数据查询

    Args:
        brand:          品牌筛选，'luckin' 或 'starbucks'
        city:           城市名称筛选
        open_date_from: 开业日期起始（包含）
        open_date_to:   开业日期截止（包含）
        limit:          每页返回条数，1~5000
        offset:         偏移量，配合 limit 实现分页

    Returns:
        dict: {"total": int, "stores": list[dict]}
              total — 符合条件的门店总数（不论分页）
              stores — 当前页的门店数据列表
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        # 基础查询模板，WHERE 1=1 作为占位方便后续动态拼接
        query = 'SELECT * FROM stores WHERE 1=1'
        params = []
        idx = 1  # PostgreSQL 参数占位符起始编号

        # 逐条件动态拼接，使用 $N 参数化避免 SQL 注入
        if brand:
            query += f' AND brand = ${idx}'; params.append(brand); idx += 1
        if city:
            query += f' AND city = ${idx}'; params.append(city); idx += 1
        if open_date_from:
            query += f' AND open_date >= ${idx}'; params.append(open_date_from); idx += 1
        if open_date_to:
            query += f' AND open_date <= ${idx}'; params.append(open_date_to); idx += 1

        # 先查总数 — 将 SELECT * 替换为 SELECT COUNT(*)，WHERE 条件保持不变
        count_query = query.replace('SELECT *', 'SELECT COUNT(*)')
        total = await conn.fetchval(count_query, *params)

        # 追加排序与分页，参数编号继续递增
        query += f' ORDER BY id LIMIT ${idx} OFFSET ${idx + 1}'
        params.extend([limit, offset])

        rows = await conn.fetch(query, *params)
        stores = [_row_to_store(r) for r in rows]

        return {'total': total, 'stores': stores}
    finally:
        await conn.close()


@router.get('/nearest')
async def nearest_store(
    lng: float = Query(..., description='查询点经度（WGS84 坐标系）'),
    lat: float = Query(..., description='查询点纬度（WGS84 坐标系）'),
    brand: Optional[str] = Query(None, description='品牌筛选：luckin 或 starbucks'),
):
    """
    最近门店查询 — 基于 PostGIS 空间 KNN 检索

    核心算法说明：
    1. PostGIS <-> 运算符：返回两个几何对象 bounding-box 中心的欧氏距离。
       在 geography 类型上使用时返回球面距离（米），在 geometry 类型上返回度数。
       配合 ORDER BY + LIMIT 且目标列有 GiST 索引时，PostgreSQL 走 KNN-GiST
       索引扫描而非全表排序，性能从 O(NlogN) 降至 O(logN)。
    2. ST_SetSRID(ST_MakePoint(lng, lat), 4326)：构造 SRID=4326 (WGS84) 的点
    3. 距离单位转换：<-> 返回的是度数差，乘以 111,000（赤道附近 1 度 ≈ 111km）
       转换为近似米。注意：这是简化近似，高纬度地区经度方向实际距离应乘以 cos(lat)。

    Args:
        lng:   查询点经度
        lat:   查询点纬度
        brand: 品牌筛选（可选）

    Returns:
        dict: 最近门店的完整信息，额外包含：
              - distance_m: 查询点到门店的近似距离（米）
              - poi_stats: 周边 POI 统计 {"200": {"地铁站": N, ...}, ...}

    Raises:
        HTTPException(404): 指定坐标附近未找到任何门店
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        brand_filter = f"AND brand = '{brand}'" if brand else ''
        # <-> 运算符：geometry 类型上的 2D 距离（度数），配合 GiST 索引实现 KNN
        # ORDER BY <-> + LIMIT 1 让优化器选择索引扫描
        row = await conn.fetchrow(f'''
            SELECT *,
                geom <-> ST_SetSRID(ST_MakePoint($1, $2), 4326) AS distance_m
            FROM stores
            WHERE 1=1 {brand_filter}
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint($1, $2), 4326)
            LIMIT 1
        ''', lng, lat)

        if not row:
            raise HTTPException(status_code=404, detail='附近未找到门店')

        store = _row_to_store(row)
        # 度数转米：地球表面纬度方向 1 度 ≈ 111,000 米（简化近似）
        # 经度方向应再乘以 cos(lat)，此处为简化处理统一使用 111000
        store['distance_m'] = round(float(row['distance_m']) * 111000, 1)

        # 关联查询门店周边 POI 统计（预计算缓存表 store_poi_stats）
        poi_rows = await conn.fetch('''
            SELECT poi_category, radius, poi_count
            FROM store_poi_stats
            WHERE store_id = $1
            ORDER BY radius, poi_count DESC
        ''', store['id'])

        # 将扁平行数据组装为嵌套字典: {半径: {POI类别: 数量}}
        poi_stats: dict = {}
        for pr in poi_rows:
            r = str(pr['radius'])
            if r not in poi_stats:
                poi_stats[r] = {}
            poi_stats[r][pr['poi_category']] = pr['poi_count']
        store['poi_stats'] = poi_stats

        return store
    finally:
        await conn.close()


@router.get('/{store_id}')
async def get_store(store_id: int):
    """
    门店详情查询 — 包含周边 POI 统计

    通过主键 ID 查询单店完整信息，并关联 store_poi_stats 缓存表
    获取该门店在 200m/500m/1000m 三个半径范围内的 POI 分布。

    Args:
        store_id: 门店主键 ID（int 类型路径参数）

    Returns:
        dict: 门店完整信息 + poi_stats 字段（嵌套字典，格式同 nearest_store）

    Raises:
        HTTPException(404): 指定 ID 的门店不存在
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        row = await conn.fetchrow('SELECT * FROM stores WHERE id = $1', store_id)
        if not row:
            raise HTTPException(status_code=404, detail='门店不存在')

        store = _row_to_store(row)

        # 查询 POI 统计缓存，按 radius 升序、poi_count 降序
        poi_rows = await conn.fetch('''
            SELECT poi_category, radius, poi_count
            FROM store_poi_stats
            WHERE store_id = $1
            ORDER BY radius, poi_count DESC
        ''', store_id)

        # 组装嵌套字典: {"200": {"地铁站": 3, "写字楼": 12, ...}, "500": {...}, "1000": {...}}
        poi_stats = {}
        for pr in poi_rows:
            r = str(pr['radius'])
            if r not in poi_stats:
                poi_stats[r] = {}
            poi_stats[r][pr['poi_category']] = pr['poi_count']

        store['poi_stats'] = poi_stats
        return store
    finally:
        await conn.close()


@router.get('/heatmap/data')
async def store_heatmap(
    brand: Optional[str] = 'luckin',
    city: Optional[str] = None,
    year: Optional[int] = None,
):
    """
    门店热力图 GeoJSON 数据 — 供前端地图组件渲染热力图层

    使用 PostGIS ST_Collect 将符合条件的门店几何对象收集为一个 MultiPoint
    几何集合，再通过 ST_AsGeoJSON 输出为标准 GeoJSON 格式。

    ST_Collect vs ST_Union：
    - ST_Collect 仅将几何对象收集到一个集合中，不做拓扑合并，性能更高
    - ST_Union 会合并重叠区域并溶解公共边界，计算量大
    对于点数据的可视化聚合场景，ST_Collect 是合适的选择。

    Args:
        brand: 品牌筛选，默认 'luckin'
        city:  城市筛选（可选）
        year:  年份筛选（可选），仅统计该年及之前开业的门店

    Returns:
        dict: {
            "geojson": GeoJSON MultiPoint 对象（已 JSON 反序列化）,
            "count": 门店数量,
            "brand/city/year": 回显查询参数
        }
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        # 构建 WHERE 筛选条件列表
        conditions = [f"brand = '{brand}'"]
        if city:
            conditions.append(f"city = '{city}'")
        if year:
            # EXTRACT(YEAR FROM timestamp) 提取日期的年份部分
            conditions.append(f"EXTRACT(YEAR FROM open_date) <= {year}")

        where = ' AND '.join(conditions)

        rows = await conn.fetch(f'''
            SELECT
                ST_AsGeoJSON(ST_Collect(geom)) AS geojson,
                COUNT(*) AS count
            FROM stores
            WHERE {where}
        ''')

        if rows and rows[0]['geojson']:
            return {
                'geojson': json.loads(rows[0]['geojson']),
                'count': rows[0]['count'],
                'brand': brand,
                'city': city,
                'year': year,
            }
        return {'geojson': None, 'count': 0}
    finally:
        await conn.close()


@router.get('/timeline/data')
async def store_timeline(
    brand: Optional[str] = 'luckin',
    city: Optional[str] = None,
):
    """
    门店扩张时间轴数据 — 按月维度统计新增与累计门店数

    查询逻辑：
    1. DATE_TRUNC('month', open_date) 将开业日期截断到月份粒度
    2. TO_CHAR(..., 'YYYY-MM') 格式化为前端可直接展示的月份字符串
    3. GROUP BY 按月聚合，COUNT(*) 得当月新增
    4. 窗口函数 SUM(COUNT(*)) OVER (ORDER BY ...) 实现逐月累加：
       - 窗口按月份排序，每行的 SUM 作用域为"当前行及之前所有行"
       - 等价于运行累计和（running total），且无需子查询

    Args:
        brand: 品牌筛选，默认 'luckin'
        city:  城市筛选（可选，为空返回全部城市汇总）

    Returns:
        list[dict]: 时间序列数组，每个元素包含:
                    month, new_stores, cumulative_stores, brand
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        city_filter = f"AND city = '{city}'" if city else ''

        rows = await conn.fetch(f'''
            SELECT
                TO_CHAR(DATE_TRUNC('month', open_date), 'YYYY-MM') AS month,
                COUNT(*) AS new_stores,
                -- 窗口函数：按月排序的累计求和，每一行累加当前及之前所有月的 COUNT
                SUM(COUNT(*)) OVER (ORDER BY DATE_TRUNC('month', open_date)) AS cumulative_stores
            FROM stores
            WHERE brand = $1 AND open_date IS NOT NULL {city_filter}
            GROUP BY DATE_TRUNC('month', open_date)
            ORDER BY month
        ''', brand)

        return [
            {
                'month': r['month'],
                'new_stores': r['new_stores'],
                'cumulative_stores': r['cumulative_stores'],
                'brand': brand,
            }
            for r in rows
        ]
    finally:
        await conn.close()


@router.get('/cities/list')
async def list_cities():
    """
    城市列表查询 — 包含门店数量统计与城市层级信息

    数据来源：
    - mv_city_stats：物化视图，预聚合了各城市各品牌的门店统计
    - city_tiers：城市层级维表（一线/新一线/二线/.../五线）

    使用物化视图而非实时聚合的优势：
    - 城市统计数据低频更新（日/周级别刷新即可）
    - 避免每次查询都扫描 stores 全表做 GROUP BY
    - 配合 LEFT JOIN 维表，一次查询即可获得完整结果

    Returns:
        list[dict]: 按 store_count 降序排列的城市列表，每项包含：
                    city, brand, store_count, first_open_date,
                    last_open_date, tier_label（无层级则显示"其他"）
    """
    conn = await asyncpg.connect(**parse_db_url(settings.database_url))
    try:
        rows = await conn.fetch('''
            SELECT
                s.city,
                s.brand,
                s.store_count,
                s.first_open_date,
                s.last_open_date,
                COALESCE(ct.tier_label, '其他') AS tier_label
            FROM mv_city_stats s
            LEFT JOIN city_tiers ct ON s.city = ct.city_name
            ORDER BY s.store_count DESC
        ''')

        return [
            {
                'city': r['city'],
                'brand': r['brand'],
                'store_count': r['store_count'],
                'first_open_date': str(r['first_open_date']) if r['first_open_date'] else None,
                'last_open_date': str(r['last_open_date']) if r['last_open_date'] else None,
                'tier_label': r['tier_label'],
            }
            for r in rows
        ]
    finally:
        await conn.close()
