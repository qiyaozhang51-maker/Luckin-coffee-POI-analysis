"""
瑞幸咖啡 + 星巴克 门店数据采集脚本
=================================
数据管道位置：第 1 步（数据采集阶段）
数据来源：高德地图 POI 搜索 API（AMAP_POI_SEARCH_URL）
采集品牌：luckin（瑞幸咖啡）、starbucks（星巴克）
采集范围：config.FOCUS_CITIES 中定义的 20 个重点城市
输出格式：
  - data/raw/{brand}_stores_{timestamp}.json（原始 JSON 备份）
  - PostgreSQL stores 表（通过 asyncpg 异步写入）

API 调用说明：
  - 使用高德 POI 文本搜索 API，参数 citylimit=true 限制在当前城市
  - 每页 25 条（offset=25），自动翻页直到全部获取
  - extensions=all 返回详细信息（包含照片、营业时间等扩展字段）
  - 城市间延迟 1 秒，分页间延迟 0.5 秒（API 限流保护）

预计运行时间（20 个城市）：
  - 瑞幸咖啡：约 30-60 分钟（门店数量多，约 15000+ 条）
  - 星巴克：约 15-30 分钟（门店数量较少，约 5000+ 条）

依赖：
  - config.AMAP_API_KEY（高德 API 密钥）
  - config.DB_CONFIG（数据库连接参数）
  - sql/schema.sql 中 stores 表需先建好

执行方式：
  python scripts/fetch_stores.py
"""
import time
import json
import asyncio
import aiohttp
import asyncpg
from pathlib import Path
from datetime import datetime
from config import AMAP_API_KEY, AMAP_POI_SEARCH_URL, FOCUS_CITIES, DB_CONFIG

# 原始数据存储目录（项目根目录下的 data/raw/）
DATA_DIR = Path(__file__).parent.parent / 'data' / 'raw'

# 品牌搜索关键词映射
# key:   品牌标识（用于文件名和数据库 brand 字段）
# value: 高德 API 搜索关键词（中文品牌名，用于 keywords 参数）
BRAND_SEARCH = {
    'luckin': '瑞幸咖啡',
    'starbucks': '星巴克',
}


async def search_city_poi(
    session: aiohttp.ClientSession,
    keywords: str,
    city: str,
    page: int = 1,
    offset: int = 25,
) -> dict:
    """调用高德 POI 文本搜索 API，获取单页结果。

    高德 API 参数说明：
    - key:        高德 Web API Key（必填）
    - keywords:   搜索关键词，支持品牌名、POI 类型等
    - city:       城市名称（中文），如 "北京"、"上海"
    - citylimit:  true=仅搜索该城市内的结果（避免跨城结果）
    - offset:     每页记录数，最大 25
    - page:       页码，从 1 开始
    - extensions: all=返回详细信息（照片、营业时间、评分等扩展字段）
    - output:     JSON=返回 JSON 格式

    Args:
        session:  aiohttp 异步 HTTP 会话（复用连接池）
        keywords: 搜索关键词（如 "瑞幸咖啡"、"星巴克"）
        city:     城市名称（中文）
        page:     页码，从 1 开始
        offset:   每页记录数，默认 25（最大值）

    Returns:
        dict: 高德 API 返回的 JSON 响应，主要字段：
              - status: "1"=成功, "0"=失败
              - count:  该关键词下总记录数
              - pois:   当前页的 POI 列表，每条包含 name、location、address 等

    Raises:
        aiohttp.ClientError: 网络请求失败时抛出（由调用方 try/except 捕获）
    """
    params = {
        'key': AMAP_API_KEY,
        'keywords': keywords,
        'city': city,
        'citylimit': 'true',   # 限制在当前城市范围内搜索，避免返回相邻城市结果
        'offset': offset,
        'page': page,
        'extensions': 'all',   # 返回扩展信息（照片列表 photos、biz_ext 等）
        'output': 'JSON',
    }
    async with session.get(AMAP_POI_SEARCH_URL, params=params) as resp:
        return await resp.json()


async def search_city_all_pages(
    session: aiohttp.ClientSession,
    keywords: str,
    city: str,
    brand: str,
) -> list[dict]:
    """翻页搜索某城市某品牌的所有门店，直到所有页获取完毕。

    翻页逻辑：
    1. 从 page=1 开始，每页 25 条
    2. 检查 data['status'] 是否为 "1"（成功），不是则终止
    3. 检查 data['pois'] 是否为空，空则终止
    4. 累计已获取数 >= data['count']（总数），终止翻页
    5. 每页间隔 0.5 秒（API 限流保护，QPS≤2）

    数据清洗：
    - 解析 location 字段（格式："lng,lat" → 拆分为 lng 和 lat）
    - 保留原始 JSON（raw 字段）以备后续重新解析
    - 提取品牌、地址、城市、行政区、typecode 等关键字段

    Args:
        session:  aiohttp 异步 HTTP 会话
        keywords: 搜索关键词
        city:     城市名称
        brand:    品牌标识（'luckin' 或 'starbucks'）

    Returns:
        list[dict]: 该城市该品牌的所有门店列表，每条记录包含：
                    name, brand, address, lng, lat, city, district,
                    adcode, typecode, photos, biz_ext, raw
    """
    all_results = []
    page = 1

    while True:
        try:
            data = await search_city_poi(session, keywords, city, page, 25)
        except Exception as e:
            print(f'  [ERROR] {city} page {page}: {e}')
            break

        # API 返回状态检查：status="1" 表示请求成功
        if data.get('status') != '1':
            print(f'  [WARN] {city} API error: {data.get("info")}')
            break

        pois = data.get('pois', [])
        if not pois:
            break

        # 解析每条 POI 记录，提取关键字段
        for poi in pois:
            # location 格式："经度,纬度"（如 "116.397428,39.90923"）
            loc = poi.get('location', '0,0').split(',')
            all_results.append({
                'name': poi.get('name', ''),
                'brand': brand,
                'address': poi.get('address', ''),
                'lng': float(loc[0]),
                'lat': float(loc[1]),
                'city': poi.get('cityname', city),
                'district': poi.get('adname', ''),    # 高德返回的行政区名称
                'adcode': poi.get('adcode', ''),      # 高德行政区划代码
                'typecode': poi.get('typecode', ''),   # 高德 POI 类型编码
                'photos': poi.get('photos', []),       # 照片 URL 列表
                'biz_ext': poi.get('biz_ext', {}),     # 扩展业务信息（评分、均价等）
                'raw': json.dumps(poi, ensure_ascii=False),  # 完整原始数据
            })

        # 翻页进度跟踪
        total = int(data.get('count', 0))
        fetched = page * 25
        print(f'  {city}: page {page} done, {fetched}/{total} fetched')

        # 终止条件：已获取数量 >= 总数
        if fetched >= total:
            break
        page += 1
        # API 限流保护：每次请求间隔 0.5 秒（QPS ≤ 2）
        await asyncio.sleep(0.5)

    return all_results


async def fetch_brand(brand: str, keywords: str):
    """采集单个品牌的所有城市门店数据（非主流程使用的辅助函数）。

    注意：此函数每次调用都会创建新的 ClientSession，
    但不会正确关闭（已知问题），主流程请使用 main() 中的写法。

    Args:
        brand:    品牌标识（'luckin' 或 'starbucks'）
        keywords: 搜索关键词

    Returns:
        list[dict]: 所有城市的该品牌门店列表
    """
    print(f'\n{"="*50}')
    print(f'开始采集: {brand} ({keywords})')
    print(f'{"="*50}')

    all_stores = []
    for city_info in FOCUS_CITIES:
        city_name = city_info['name']
        print(f'\n搜索 {city_name}...')
        stores = await search_city_all_pages(
            aiohttp.ClientSession(),
            keywords, city_name, brand
        )
        # 不能在这里 close session
        all_stores.extend(stores)

    return all_stores


async def save_to_db(stores: list[dict]):
    """将采集的门店数据批量写入 PostgreSQL stores 表。

    数据库操作：
    - 逐条 INSERT，使用 ST_SetSRID(ST_MakePoint(lng, lat), 4326) 创建几何字段
    - SRID 4326 = WGS84 地理坐标系（GPS 使用的经纬度标准）
    - ON CONFLICT DO NOTHING：遇到重复主键时跳过（幂等性保证）
    - 重复判断依赖 stores 表上的唯一约束（如有）
    - 批量插入后统计成功 / 失败数量

    Args:
        stores: 门店信息列表（来自 search_city_all_pages 的返回值）

    Returns:
        None（直接打印插入统计信息）
    """
    conn = await asyncpg.connect(**DB_CONFIG)

    inserted = 0
    for store in stores:
        try:
            await conn.execute('''
                INSERT INTO stores (name, brand, address, lng, lat, geom, city, district,
                                    adcode, data_source)
                VALUES ($1, $2, $3, $4, $5,
                        ST_SetSRID(ST_MakePoint($4, $5), 4326),
                        $6, $7, $8, 'amap')
                ON CONFLICT DO NOTHING
            ''', store['name'], store['brand'], store['address'],
                store['lng'], store['lat'],
                store['city'], store['district'], store['adcode'])
            inserted += 1
        except Exception as e:
            print(f'  [DB ERROR] {store["name"]}: {e}')

    await conn.close()
    print(f'\n插入数据库: {inserted}/{len(stores)} 条')


async def main():
    """主函数：依次采集瑞幸咖啡和星巴克的门店数据。

    执行流程：
    1. 遍历 BRAND_SEARCH（luckin → starbucks）
    2. 对每个品牌，遍历 FOCUS_CITIES 的 20 个城市
    3. 对每个城市，调用 search_city_all_pages 翻页获取所有门店
    4. 将原始数据保存为 JSON 文件（data/raw/{brand}_stores_{timestamp}.json）
    5. 将数据写入 PostgreSQL stores 表
    6. 城市间延迟 1 秒（API 限流保护）

    输出：
    - JSON 文件：data/raw/luckin_stores_20260722_120000.json 等
    - 数据库：stores 表中的新记录
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    for brand, keywords in BRAND_SEARCH.items():
        # 使用 async with 确保 ClientSession 正确关闭（解决 fetch_brand 的已知问题）
        async with aiohttp.ClientSession() as session:
            all_stores = []
            for city_info in FOCUS_CITIES:
                city_name = city_info['name']
                print(f'\n搜索 {city_name} - {brand}...')
                stores = await search_city_all_pages(
                    session, keywords, city_name, brand
                )
                all_stores.extend(stores)
                # 城市间延迟 1 秒，避免触发高德 API 的频率限制
                await asyncio.sleep(1)

        # 保存原始 JSON（完整数据备份，可用于后续重新处理）
        raw_path = DATA_DIR / f'{brand}_stores_{timestamp}.json'
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        with open(raw_path, 'w', encoding='utf-8') as f:
            json.dump(all_stores, f, ensure_ascii=False, indent=2)
        print(f'原始数据已保存: {raw_path} ({len(all_stores)} 条)')

        # 写入 PostgreSQL 数据库
        await save_to_db(all_stores)

    print(f'\n========== 全部采集完成 ==========')


if __name__ == '__main__':
    # 运行主函数（asyncio 事件循环）
    asyncio.run(main())
