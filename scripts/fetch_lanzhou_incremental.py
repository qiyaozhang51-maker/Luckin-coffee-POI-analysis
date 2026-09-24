"""
兰州增量补采脚本
=================
用途：把兰州市的门店和 POI 补齐到高德当前可用的最新状态，
      同时**不破坏**库中已有的兰州数据。

与 fetch_stores.py / fetch_pois.py 的区别：
  1. 原脚本遍历 config.FOCUS_CITIES 的 20 个城市（不含兰州），且用
     ON CONFLICT DO NOTHING；而 stores/pois 表只有 id 主键、没有业务唯一约束，
     重跑会产生重复行。本脚本只针对兰州，并以"同品牌 + 30 米内"（门店）或
     "同类别同名 + 30 米内"（POI）判定同一实体，只插入真正新增的记录。
  2. 原脚本的翻页函数遇错直接 break，会静默返回残缺数据（实测被高德限流时
     140 条只拿到 75 条）。本脚本自建翻页，带指数退避重试，并把
     "高德声称的总数"与"实际取到的条数"都打印出来，残缺可见。

已知限制：
  高德 API 不返回门店开业日期，新插入的门店 open_date 为 NULL。
  受影响的只有依赖 open_date 的功能（时空演变时间轴 / KDE 热力图，二者均有
  `WHERE open_date IS NOT NULL` 过滤，属干净过滤，不会产生脏数据）。
  门店列表、城市计数、选址评分特征均不受影响。

执行方式：
  cd <项目根目录> && python scripts/fetch_lanzhou_incremental.py
"""
import asyncio
import io
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import aiohttp
import asyncpg

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_CONFIG, POI_SEARCH_KEYWORDS, AMAP_TYPECODE_MAP
from fetch_stores import search_city_poi          # 单页原始请求（带 citylimit）
from fetch_pois import _infer_category            # typecode 兜底分类

# ─── 配置 ───────────────────────────────────────────────────
CITY = '兰州'            # 高德 API 的 city 参数
CITY_FULL = '兰州市'      # 数据库 city 字段（全库统一带"市"）
BRANDS = {'luckin': '瑞幸咖啡', 'starbucks': '星巴克'}

STORE_TOL_M = 30.0       # 门店同一性判定距离容差（米）
POI_TOL_M = 30.0         # POI 同一性判定距离容差（米）

PAGE_SIZE = 25
MAX_PAGES = 40           # 高德硬限制：单关键词最多 40 页 / 1000 条
PAGE_DELAY = 0.6         # 翻页间隔（秒）——限流保护
RETRY = 5                # 单页失败重试次数

ROOT = Path(__file__).parent.parent
RAW_DIR = ROOT / 'data' / 'raw'
REPORT = io.open(ROOT / 'data' / '_lanzhou_report.txt', 'w', encoding='utf-8')


def log(*a):
    line = ' '.join(str(x) for x in a)
    print(line, flush=True)
    print(line, file=REPORT)


def _s(v) -> str:
    """高德对空字段返回 []（而非 null），统一转成字符串。"""
    if isinstance(v, list):
        return ''.join(str(x) for x in v)
    return '' if v is None else str(v)


def haversine(lng1, lat1, lng2, lat2) -> float:
    """两点球面距离（米）"""
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def norm_name(s: str) -> str:
    """归一化名称：去掉括号注释与空白，用于 POI 同名判定"""
    s = re.sub(r'[（(][^)）]*[)）]', '', s or '')
    return re.sub(r'\s+', '', s)


async def fetch_pages(session, keywords: str, label: str = '') -> tuple[list, int]:
    """带退避重试的翻页拉取，返回 (pois 列表, 高德声称的总数)。

    与原脚本的关键差异：单页请求失败（网络异常或 status != '1'，
    后者通常是触发了高德 QPS 限流）时会退避后**重试同一页**，
    而不是直接放弃整个关键词。
    """
    out, page, claimed = [], 1, 0
    while page <= MAX_PAGES:
        data = None
        for attempt in range(RETRY):
            try:
                data = await search_city_poi(session, keywords, CITY, page, PAGE_SIZE)
            except Exception as e:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if data.get('status') == '1':
                break
            # status != '1'：多为限流，退避重试
            log(f'      [限流?] {label} page{page} status={data.get("status")} '
                f'info={data.get("info")} 重试{attempt + 1}/{RETRY}')
            await asyncio.sleep(1.5 * (attempt + 1))
        else:
            log(f'      [放弃] {label} page{page} 重试 {RETRY} 次仍失败')
            break

        pois = data.get('pois', [])
        if not pois:
            break
        claimed = int(data.get('count', 0) or 0)
        out.extend(pois)
        page += 1
        await asyncio.sleep(PAGE_DELAY)

    return out, claimed


async def main():
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    conn = await asyncpg.connect(**DB_CONFIG)

    log('=' * 62)
    log(f'兰州增量补采  {datetime.now():%Y-%m-%d %H:%M:%S}')
    log('=' * 62)

    # ══════════════════════════════════════════════════════
    # 0. 备份现有兰州数据（可回滚）
    # ══════════════════════════════════════════════════════
    b_stores = await conn.fetch(
        'SELECT id, name, brand, address, lng, lat, city, district, adcode, '
        'open_date, data_source FROM stores WHERE city = $1', CITY_FULL)
    b_pois = await conn.fetch(
        'SELECT id, name, category, lng, lat, address, city, district, adcode, '
        'amap_typecode, data_source FROM pois WHERE city = $1', CITY_FULL)

    backup = {'backup_time': datetime.now().isoformat(), 'city': CITY_FULL,
              'stores': [dict(r) for r in b_stores],
              'pois': [dict(r) for r in b_pois]}
    for s in backup['stores']:
        if s.get('open_date') is not None:
            s['open_date'] = s['open_date'].isoformat()
    bp = ROOT / 'data' / f'backup_lanzhou_{ts}.json'
    with io.open(bp, 'w', encoding='utf-8') as f:
        json.dump(backup, f, ensure_ascii=False)
    log(f'\n[0] 备份: {bp.name}  (门店 {len(b_stores)} / POI {len(b_pois)})')

    ex_stores = [dict(r) for r in b_stores]
    ex_pois = [dict(r) for r in b_pois]

    async with aiohttp.ClientSession() as session:
        # ══════════════════════════════════════════════════
        # 1. 采集门店
        # ══════════════════════════════════════════════════
        log('\n[1] 采集门店...')
        fetched_stores = {}
        for brand, kw in BRANDS.items():
            raw, claimed = await fetch_pages(session, kw, label=kw)
            fetched_stores[brand] = raw
            log(f'    {kw}: 高德声称 {claimed} 条，实际取到 {len(raw)} 条'
                + ('  ⚠ 可能不完整' if claimed > len(raw) + 20 else ''))
            await asyncio.sleep(1.5)

        # ══════════════════════════════════════════════════
        # 2. 采集 POI
        # ══════════════════════════════════════════════════
        log('\n[2] 采集 POI...')
        fetched_pois = []
        for category, keywords in POI_SEARCH_KEYWORDS.items():
            for kw in keywords:
                raw, claimed = await fetch_pages(session, kw, label=kw)
                for poi in raw:
                    tc = _s(poi.get('typecode', ''))
                    cat = AMAP_TYPECODE_MAP.get(tc) or _infer_category(tc, _s(poi.get('type', '')))
                    loc = _s(poi.get('location', '')).split(',')
                    if len(loc) != 2:
                        continue
                    fetched_pois.append({
                        'name': _s(poi.get('name', '')),
                        'category': cat,
                        'lng': float(loc[0]), 'lat': float(loc[1]),
                        'address': _s(poi.get('address', '')),
                        'city': _s(poi.get('cityname', CITY)) or CITY_FULL,
                        'district': _s(poi.get('adname', '')),
                        'adcode': _s(poi.get('adcode', '')),
                        'amap_typecode': tc,
                    })
                log(f'    [{category}] {kw}: 声称 {claimed}，取到 {len(raw)}')
                await asyncio.sleep(1.2)

    # POI 内部去重（复刻 fetch_pois.py 的 5 位小数 + 名称前 20 字）
    seen, uniq_pois = set(), []
    for p in fetched_pois:
        k = (round(p['lng'], 5), round(p['lat'], 5), p['name'][:20])
        if k not in seen:
            seen.add(k)
            uniq_pois.append(p)
    log(f'    POI 采集 {len(fetched_pois)} 条 -> 去重后 {len(uniq_pois)} 条')

    # 存原始 JSON（沿用项目命名规范）
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for brand, rows in fetched_stores.items():
        fp = RAW_DIR / f'{brand}_stores_{CITY}_{ts}.json'
        with io.open(fp, 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False)
    fp = RAW_DIR / f'pois_{CITY}_{ts}.json'
    with io.open(fp, 'w', encoding='utf-8') as f:
        json.dump(uniq_pois, f, ensure_ascii=False)
    log(f'    原始文件已写入 {RAW_DIR.name}/')

    # ══════════════════════════════════════════════════════
    # 3. 增量插入门店
    # ══════════════════════════════════════════════════════
    log('\n[3] 增量插入门店...')
    new_store_ids, dup_n = [], 0
    for brand, rows in fetched_stores.items():
        for p in rows:
            loc = _s(p.get('location', '')).split(',')
            if len(loc) != 2:
                continue
            lng, lat = float(loc[0]), float(loc[1])
            if any(e['brand'] == brand
                   and haversine(e['lng'], e['lat'], lng, lat) < STORE_TOL_M
                   for e in ex_stores):
                dup_n += 1
                continue
            row = await conn.fetchrow('''
                INSERT INTO stores (name, brand, address, lng, lat, geom, city,
                                    district, adcode, data_source)
                VALUES ($1,$2,$3,$4,$5, ST_SetSRID(ST_MakePoint($4,$5),4326),
                        $6,$7,$8,'amap')
                RETURNING id
            ''', _s(p.get('name', '')), brand, _s(p.get('address', '')),
                lng, lat, CITY_FULL, _s(p.get('adname', '')), _s(p.get('adcode', '')))
            new_store_ids.append(row['id'])
            ex_stores.append({'id': row['id'], 'brand': brand, 'lng': lng, 'lat': lat})
    log(f'    已存在(30米内同品牌) {dup_n} 条')
    log(f'    新增 {len(new_store_ids)} 条  ids={new_store_ids}')

    # ══════════════════════════════════════════════════════
    # 4. 增量插入 POI
    # ══════════════════════════════════════════════════════
    log('\n[4] 增量插入 POI...')
    pool = {}
    for e in ex_pois:
        pool.setdefault((e['category'], norm_name(e['name'])), []).append(e)

    new_poi_ids, dup_p = [], 0
    for p in uniq_pois:
        bucket = pool.setdefault((p['category'], norm_name(p['name'])), [])
        if any(haversine(e['lng'], e['lat'], p['lng'], p['lat']) < POI_TOL_M
               for e in bucket):
            dup_p += 1
            continue
        row = await conn.fetchrow('''
            INSERT INTO pois (name, category, lng, lat, geom, address, city,
                              district, adcode, data_source, amap_typecode)
            VALUES ($1,$2,$3,$4, ST_SetSRID(ST_MakePoint($3,$4),4326),
                    $5,$6,$7,$8,'amap',$9)
            RETURNING id
        ''', p['name'], p['category'], p['lng'], p['lat'], p['address'],
            CITY_FULL, p['district'], p['adcode'], p['amap_typecode'])
        new_poi_ids.append(row['id'])
        bucket.append({'lng': p['lng'], 'lat': p['lat'], 'name': p['name'],
                       'category': p['category'], 'id': row['id']})
    log(f'    已存在(同类别同名30米内) {dup_p} 条')
    log(f'    新增 {len(new_poi_ids)} 条')

    # ══════════════════════════════════════════════════════
    # 5. 汇总
    # ══════════════════════════════════════════════════════
    n_st = await conn.fetchval('SELECT count(*) FROM stores WHERE city=$1', CITY_FULL)
    n_p = await conn.fetchval('SELECT count(*) FROM pois WHERE city=$1', CITY_FULL)
    log('\n[5] 补采后兰州总量')
    log(f'    门店: {len(b_stores)} -> {n_st}')
    log(f'    POI : {len(b_pois)} -> {n_p}')
    log(f'\n新增门店 ID: {new_store_ids}')

    await conn.close()
    REPORT.close()
    print('done')


if __name__ == '__main__':
    asyncio.run(main())
