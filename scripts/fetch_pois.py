"""
POI（兴趣点）数据采集脚本
=========================
数据管道位置：第 1 步（数据采集阶段）
数据来源：高德地图 POI 搜索 API + OSM 地图数据
采集范围：config.FOCUS_CITIES 中定义的 20 个重点城市
采集策略：
  - 全国范围：OSM 数据（道路、地铁、建筑基础）—— 由 import_osm.py 负责
  - 重点城市 TOP20：高德 API 精细 POI 数据 —— 由本脚本负责

数据采集流程：
  1. 遍历 20 个城市
  2. 对每个城市，遍历所有 POI 类别（office/mall/metro/university/residential）
  3. 对每个类别，使用多个中文关键词搜索（提升召回率）
  4. 对每个关键词，翻页获取所有结果（最多 40 页 = 1000 条）
  5. 按 (lng, lat, name) 三元组去重（同一 POI 可能被多个关键词命中）
  6. 保存为 JSON 文件 + 写入 PostgreSQL

高德 API 调用：
  - 使用 AMAP_POI_SEARCH_URL（文本搜索）
  - citylimit=true 限制在当前城市
  - extensions=all 返回详细信息
  - 每页 25 条，关键词间延迟 1 秒，分页间延迟 0.3 秒

POI 分类策略：
  - 优先使用 AMAP_TYPECODE_MAP（config.py 中定义）精确映射
  - 若 typecode 不在映射表中，使用 _infer_category() 兜底推断
  - 兜底逻辑基于高德 typecode 的前 2 位大类编码

预计运行时间（20 个城市，5 个类别，共约 15 个关键词）：
  - 约 1-2 小时（取决于 API 调用量和城市 POI 密度）

去重说明：
  - 去重 key：(经度保留 5 位小数, 纬度保留 5 位小数, 名称前 20 字)
  - 5 位小数的经纬度精度约 1 米，足以区分不同 POI
  - 名称取前 20 字可避免同一点位由不同来源产生的名称差异

依赖：
  - config.AMAP_API_KEY、config.AMAP_TYPECODE_MAP
  - config.POI_SEARCH_KEYWORDS、config.FOCUS_CITIES
  - config.DB_CONFIG
  - sql/schema.sql 中 pois 表需先建好

执行方式：
  python scripts/fetch_pois.py
"""
import json
import asyncio
import aiohttp
import asyncpg
from pathlib import Path
from datetime import datetime
from config import (
    AMAP_API_KEY, AMAP_POI_SEARCH_URL,
    FOCUS_CITIES, POI_SEARCH_KEYWORDS, DB_CONFIG, AMAP_TYPECODE_MAP
)

# 原始数据存储目录
DATA_DIR = Path(__file__).parent.parent / 'data' / 'raw'


async def search_city_poi_by_keyword(
    session: aiohttp.ClientSession,
    city: str,
    keyword: str,
    max_pages: int = 40,
) -> list[dict]:
    """按关键词搜索城市 POI，自动翻页获取所有结果。

    高德 API 限制说明：
    - 单次请求最多返回 25 条（offset=25）
    - 单个关键词最多返回 1000 条（40 页 x 25 条）
    - 超出 1000 条的结果无法通过翻页获取（API 硬限制）

    分类策略：
    - 优先匹配 AMAP_TYPECODE_MAP（config.py 中的精确映射）
    - 若 typecode 不在映射表中，调用 _infer_category() 兜底推断
    - category 字段统一使用本项目的简化分类体系

    Args:
        session:   aiohttp 异步 HTTP 会话（复用连接池）
        city:      城市名称（中文），如 "北京"
        keyword:   搜索关键词，如 "写字楼"、"地铁站"
        max_pages: 最大翻页数，默认 40（即最多 1000 条）

    Returns:
        list[dict]: POI 列表，每条包含：
                    name, category, lng, lat, address, city,
                    district, adcode, amap_typecode
    """
    results = []
    for page in range(1, max_pages + 1):
        params = {
            'key': AMAP_API_KEY,
            'keywords': keyword,
            'city': city,
            'citylimit': 'true',   # 仅搜索当前城市，避免跨城结果干扰
            'offset': 25,           # 每页 25 条（高数 API 单页最大值）
            'page': page,
            'extensions': 'all',    # 返回扩展信息（照片、评分等）
            'output': 'JSON',
        }
        try:
            async with session.get(AMAP_POI_SEARCH_URL, params=params) as resp:
                data = await resp.json()
        except Exception as e:
            print(f'  [ERROR] {keyword} page {page}: {e}')
            break

        # API 响应状态检查
        if data.get('status') != '1':
            break

        pois = data.get('pois', [])
        if not pois:
            break

        # 解析 POI 并映射分类
        for poi in pois:
            typecode = poi.get('typecode', '')
            # 一级分类：从 AMAP_TYPECODE_MAP 精确映射
            category = AMAP_TYPECODE_MAP.get(typecode)
            # 二级兜底：映射表中没有则基于大类编码推断
            if category is None:
                category = _infer_category(typecode, poi.get('type', ''))

            results.append({
                'name': poi.get('name', ''),
                'category': category,
                'lng': float(poi.get('location', '0,0').split(',')[0]),
                'lat': float(poi.get('location', '0,0').split(',')[1]),
                'address': poi.get('address', ''),
                'city': poi.get('cityname', city),
                'district': poi.get('adname', ''),
                'adcode': poi.get('adcode', ''),
                'amap_typecode': typecode,    # 保留原始编码以备查证
            })

        # API 限流：每页间隔 0.3 秒
        await asyncio.sleep(0.3)

    return results


def _infer_category(typecode: str, type_name: str) -> str:
    """基于高德 typecode 的前 2 位大类编码推断 POI 分类（兜底逻辑）。

    高德 typecode 编码规则（6 位数字）：
    - 前 2 位：大类编码
        05=餐饮服务  06=购物服务  07=金融保险  08=住宿服务  09=医疗保健
        10=风景名胜  12=商务住宅  14=科教文化  15=交通设施
    - 中间 2 位：中类编码
    - 后 2 位：小类编码

    此函数仅在 AMAP_TYPECODE_MAP 精确映射失败时使用，
    通过大类编码做粗略推断，确保所有 POI 都有有效分类。
    细分类别进一步通过前 4 位（startswith）判断（如 1202=写字楼 vs 1203=住宅）。

    Args:
        typecode: 高德 API 返回的 6 位类型编码（如 "120201"）
        type_name: 高德 API 返回的类型名称（备用参数，当前未使用）

    Returns:
        str: 本项目的统一 POI 分类

    Example:
        >>> _infer_category('120201', '商务写字楼')
        'office'
        >>> _infer_category('', '')
        'other'
    """
    if not typecode:
        return 'other'
    # 取前两位作为大类编码
    major = typecode[:2]
    cat_map = {
        '05': 'restaurant',
        '06': 'mall',
        '07': 'bank',
        '08': 'other',    # 住宿服务（酒店/宾馆/招待所）
        '09': 'hospital',
        '10': 'other',    # 风景名胜（公园/景区/广场）
        '12': 'office' if typecode.startswith('1202') else 'residential',
        # 1202xx = 写字楼/产业园/商务中心 → office
        # 1203xx = 住宅小区/别墅/社区中心 → residential
        '14': 'university' if typecode.startswith('1412') else 'school',
        # 1412xx = 大学/高等院校 → university
        # 1410/1411/1414 = 学校/中学/小学 → school
        '15': 'metro' if typecode.startswith('1505') else 'bus',
        # 1505xx = 地铁站 → metro
        # 1507/1508 = 公交站/公交线路 → bus
    }
    return cat_map.get(major, 'other')


async def fetch_city(session: aiohttp.ClientSession, city_info: dict) -> list[dict]:
    """采集单个城市的所有类别 POI 数据。

    采集流程：
    1. 遍历 POI_SEARCH_KEYWORDS 中的所有类别（office/mall/metro/university/residential）
    2. 对每个类别的每个关键词，调用 search_city_poi_by_keyword 翻页获取
    3. 关键词间延迟 1 秒（API 限流保护）
    4. 去重：按 (lng保留5位小数, lat保留5位小数, name[:20]) 三元组去重

    去重原因：
    - 同一 POI 可能被不同关键词命中（如 "写字楼" 和 "商务中心" 都会搜到同个写字楼）
    - 使用经纬度（5 位小数 ~ 1m 精度）+ 名称前缀作为唯一标识

    Args:
        session:   aiohttp 异步 HTTP 会话
        city_info: 城市信息字典，格式 {'name': '北京', 'adcode': '110000'}

    Returns:
        list[dict]: 去重后的 POI 列表
    """
    city_name = city_info['name']
    print(f'\n{"="*40}')
    print(f'采集 {city_name} POI...')

    all_pois = []
    # 遍历所有类别和对应的搜索关键词
    for category, keywords in POI_SEARCH_KEYWORDS.items():
        for kw in keywords:
            print(f'  [{category}] 搜索: {kw}')
            pois = await search_city_poi_by_keyword(session, city_name, kw)
            all_pois.extend(pois)
            # 关键词间延迟 1 秒，避免触发高德 API 频率限制
            await asyncio.sleep(1)

    # ===== 去重逻辑 =====
    # 去重 key: (经度保留5位小数, 纬度保留5位小数, 名称前20字)
    # 经纬度 5 位小数 ≈ 1.11m 精度（赤道附近），足以区分相邻 POI
    # 名称取前 20 字可规避同点位不同来源的微小命名差异
    seen = set()
    unique = []
    for p in all_pois:
        key = (round(p['lng'], 5), round(p['lat'], 5), p['name'][:20])
        if key not in seen:
            seen.add(key)
            unique.append(p)

    print(f'  {city_name}: {len(all_pois)} raw -> {len(unique)} unique')
    return unique


async def save_to_db(pois: list[dict]):
    """批量保存 POI 数据到 PostgreSQL pois 表。

    写入策略：
    - 批量提交：每 500 条为一个事务（batch_size=500），失败时回滚该批次
    - ON CONFLICT DO NOTHING：跳过重复记录（需要表上有唯一约束）
    - 静默忽略单条插入错误（不打印日志以免淹没终端输出）
    - 几何字段：ST_SetSRID(ST_MakePoint(lng, lat), 4326) → WGS84 坐标系

    Args:
        pois: POI 列表（来自 fetch_city 的去重结果）

    Returns:
        int: 成功插入数据库的记录数
    """
    conn = await asyncpg.connect(**DB_CONFIG)

    inserted = 0
    batch_size = 500  # 每 500 条为一个事务批量提交
    for i in range(0, len(pois), batch_size):
        batch = pois[i:i + batch_size]
        async with conn.transaction():
            for poi in batch:
                try:
                    await conn.execute('''
                        INSERT INTO pois (name, category, lng, lat, geom, address,
                                          city, district, adcode, data_source, amap_typecode)
                        VALUES ($1, $2, $3, $4,
                                ST_SetSRID(ST_MakePoint($3, $4), 4326),
                                $5, $6, $7, $8, 'amap', $9)
                        ON CONFLICT DO NOTHING
                    ''', poi['name'], poi['category'], poi['lng'], poi['lat'],
                        poi.get('address', ''), poi['city'], poi['district'],
                        poi['adcode'], poi['amap_typecode'])
                    inserted += 1
                except Exception as e:
                    # 单条错误不打印，避免日志过多
                    pass
        # 进度日志
        print(f'  DB: {min(i + batch_size, len(pois))}/{len(pois)} ({inserted} inserted)')

    await conn.close()
    return inserted


async def main():
    """主函数：采集 20 个重点城市的全类别 POI 数据。

    执行流程：
    1. 使用单个 aiohttp.ClientSession 复用 TCP 连接（性能优化）
    2. 遍历 FOCUS_CITIES 中的 20 个城市
    3. 对每个城市调用 fetch_city() 采集所有类别 POI
    4. 每个城市单独保存 JSON 文件（便于按城市分析）
    5. 每个城市单独写入数据库（边采集边写，避免内存溢出）
    6. 所有城市采集完成后合并保存一份全量 JSON

    输出：
    - 每个城市独立 JSON：data/raw/pois_{city}_{timestamp}.json
    - 全量合并 JSON：data/raw/pois_all_cities_{timestamp}.json
    - 数据库 pois 表中的新增记录
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    all_city_pois = {}

    async with aiohttp.ClientSession() as session:
        for city_info in FOCUS_CITIES:
            city_name = city_info['name']
            pois = await fetch_city(session, city_info)
            all_city_pois[city_name] = pois

            # 每个城市单独保存一份 JSON（便于后续按城市分析）
            city_path = DATA_DIR / f'pois_{city_name}_{timestamp}.json'
            city_path.parent.mkdir(parents=True, exist_ok=True)
            with open(city_path, 'w', encoding='utf-8') as f:
                json.dump(pois, f, ensure_ascii=False, indent=2)

            # 写入数据库（边采集边写，避免 20 个城市的数据同时积存在内存中）
            n = await save_to_db(pois)
            print(f'  [OK] {city_name}: {n} POIs saved to DB')

    # 合并保存全量数据（用于跨城市对比分析）
    all_flat = [p for city_pois in all_city_pois.values() for p in city_pois]
    all_path = DATA_DIR / f'pois_all_cities_{timestamp}.json'
    with open(all_path, 'w', encoding='utf-8') as f:
        json.dump(all_flat, f, ensure_ascii=False, indent=2)

    print(f'\n========== POI采集完成 ==========')
    print(f'共 {len(all_flat)} 条POI，覆盖 {len(all_city_pois)} 个城市')


if __name__ == '__main__':
    asyncio.run(main())
