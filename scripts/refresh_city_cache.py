"""
单城市特征与缓存刷新脚本
=========================
背景：项目里 store_poi_stats / poi_lift_cache / poi_correlation_cache /
      brand_compare_cache 都没有生成脚本（compute_features.py 的
      compute_lift_scores 只打印不写表，部署说明书里提到的 precompute_cache.py
      正文被省略、实际不在仓库中）。本脚本按后端 analysis.py 读取的语义重建这四张表。

用法：
    python scripts/refresh_city_cache.py <城市名> validate   # 只计算并比对，不写库
    python scripts/refresh_city_cache.py <城市名> apply      # 计算并写库

validate 模式会用未改动过的城市（如 上海市）与库中已存值比对，
用来验证本脚本的 SQL 与原生成器语义一致。

刷新范围仅限指定城市，不触碰 __ALL__（全国汇总）行。
"""
import asyncio
import io
import sys
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent))
from config import DB_CONFIG

RADII = [200, 500, 1000]
BRANDS = ['luckin', 'starbucks']

# 与 store_poi_stats 中实际存在的类别保持一致
CATEGORIES = ['bank', 'bus', 'cafe', 'convenience', 'hospital', 'mall', 'metro',
              'office', 'other', 'residential', 'restaurant', 'school',
              'supermarket', 'university']

OUT = io.open(Path(__file__).parent.parent / 'data' / '_cache_report.txt', 'w',
              encoding='utf-8')


def log(*a):
    line = ' '.join(str(x) for x in a)
    print(line, flush=True)
    print(line, file=OUT)


# ────────────────────────────────────────────────────────────
#  SQL：与后端读取语义对齐
# ────────────────────────────────────────────────────────────
SQL_CORRELATION = '''
SELECT sps.poi_category,
       AVG(sps.poi_count)                     AS avg_count,
       COALESCE(STDDEV_POP(sps.poi_count), 0) AS stddev,
       MIN(sps.poi_count)                     AS min_count,
       MAX(sps.poi_count)                     AS max_count,
       COUNT(DISTINCT sps.store_id)           AS store_count
FROM store_poi_stats sps
JOIN stores s ON s.id = sps.store_id
WHERE sps.radius = $1 AND s.brand = $2 AND s.city = $3
GROUP BY sps.poi_category
'''

# avg_random = (与该城市随机点距离 R 内的 POI 记录数) / (该城市随机点总数)
SQL_LIFT = '''
SELECT sps.poi_category,
       AVG(sps.poi_count) AS avg_near_store,
       COALESCE((
           SELECT COUNT(*)::float / NULLIF(
                      (SELECT COUNT(*) FROM random_points WHERE city = $3), 0)
           FROM pois p
           CROSS JOIN random_points rp
           WHERE rp.city = $3
             AND p.category = sps.poi_category
             AND ST_DWithin(p.geom::geography, rp.geom::geography, $1)
       ), 0.0001) AS avg_random
FROM store_poi_stats sps
JOIN stores s ON s.id = sps.store_id
WHERE sps.radius = $1 AND s.brand = $2 AND s.city = $3
GROUP BY sps.poi_category
'''

SQL_BRAND_COMPARE = '''
WITH lz AS (SELECT id, geom FROM stores WHERE brand = 'luckin'    AND city = $1),
     sb AS (SELECT geom FROM stores WHERE brand = 'starbucks' AND city = $1)
SELECT
    (SELECT count(*) FROM lz) AS luckin_count,
    (SELECT count(*) FROM sb) AS starbucks_count,
    (SELECT ROUND(AVG(d)::numeric, 1) FROM (
        SELECT (SELECT MIN(ST_Distance(l.geom::geography, s2.geom::geography))
                FROM sb s2) AS d
        FROM lz l) t) AS avg_nearest_sb_m,
    (SELECT count(*) FROM lz l WHERE EXISTS (
        SELECT 1 FROM sb s2
        WHERE ST_DWithin(l.geom::geography, s2.geom::geography, 200)
    )) AS co_located_200m
'''


async def recompute_store_poi_stats(conn, city: str) -> int:
    """重算该城市所有门店在 各半径 × 各类别 下的 POI 计数。

    等价于 compute_features.compute_store_poi_stats，但加了城市过滤，
    且因为该城市 POI 有增补，需要整城重算（不只是新门店）。
    """
    total = 0
    for radius in RADII:
        for cat in CATEGORIES:
            res = await conn.execute('''
                INSERT INTO store_poi_stats (store_id, radius, poi_category, poi_count)
                SELECT s.id, $1::int, $2::text,
                       (SELECT COUNT(*) FROM pois p
                        WHERE ST_DWithin(s.geom::geography, p.geom::geography,
                                         $3::float8)
                          AND p.category = $2)
                FROM stores s
                WHERE s.city = $4
                ON CONFLICT (store_id, radius, poi_category)
                DO UPDATE SET poi_count = EXCLUDED.poi_count, last_updated = NOW()
            ''', radius, cat, float(radius), city)
            total += int(res.split()[-1]) if 'INSERT' in res else 0
    return total


async def main():
    city = sys.argv[1] if len(sys.argv) > 1 else '兰州市'
    mode = sys.argv[2] if len(sys.argv) > 2 else 'validate'
    conn = await asyncpg.connect(**DB_CONFIG)

    log('=' * 62)
    log(f'刷新城市缓存  city={city}  mode={mode}')
    log('=' * 62)

    if mode == 'apply':
        n = await recompute_store_poi_stats(conn, city)
        log(f'\n[1/4] store_poi_stats 重算完成: {n} 行')

    n_store = await conn.fetchval('SELECT count(*) FROM stores WHERE city=$1', city)
    n_poi = await conn.fetchval('SELECT count(*) FROM pois WHERE city=$1', city)
    log(f'\n门店 {n_store} / POI {n_poi}')

    # ── 2. poi_correlation_cache ──────────────────────────
    log('\n[2/4] poi_correlation_cache')
    rows_c, rows_l = [], []
    for brand in BRANDS:
        for radius in RADII:
            for r in await conn.fetch(SQL_CORRELATION, radius, brand, city):
                rows_c.append((r['poi_category'], radius, brand,
                               float(r['avg_count'] or 0), float(r['stddev'] or 0),
                               int(r['min_count'] or 0), int(r['max_count'] or 0),
                               int(r['store_count'])))
            for r in await conn.fetch(SQL_LIFT, radius, brand, city):
                an = float(r['avg_near_store'] or 0)
                ar = float(r['avg_random'] or 0.0001)
                rows_l.append((r['poi_category'], radius, brand, an, ar,
                               round(an / ar, 2) if ar else 0.0))
    log(f'    计算出 correlation {len(rows_c)} 行 / lift {len(rows_l)} 行')

    if mode == 'validate':
        log('\n    ── 与库中已存值比对 ──')
        stored_c = {(r['poi_category'], r['radius'], r['brand']):
                    (float(r['avg_count']), int(r['store_count']))
                    for r in await conn.fetch(
                        'SELECT * FROM poi_correlation_cache WHERE city=$1', city)}
        bad = 0
        for cat, radius, brand, avg, sd, mn, mx, sc in rows_c:
            s = stored_c.get((cat, radius, brand))
            if s is None:
                log(f'    [新增] {cat}/{radius}/{brand}')
                continue
            if abs(s[0] - avg) > 0.02 or s[1] != sc:
                log(f'    [差异] {cat}/{radius}/{brand}: '
                    f'avg {s[0]:.4f} -> {avg:.4f}, store_count {s[1]} -> {sc}')
                bad += 1
        log(f'    比对完成，{bad} 处差异 / {len(stored_c)} 条已存')

        stored_l = {(r['poi_category'], r['radius'], r['brand']):
                    (float(r['avg_near_store']), float(r['avg_random']), float(r['lift']))
                    for r in await conn.fetch(
                        'SELECT * FROM poi_lift_cache WHERE city=$1', city)}
        bad = 0
        for cat, radius, brand, an, ar, lift in rows_l:
            s = stored_l.get((cat, radius, brand))
            if s is None:
                continue
            if abs(s[0] - an) > 0.02 or abs(s[2] - lift) > 0.05:
                log(f'    [差异] lift {cat}/{radius}/{brand}: '
                    f'avg {s[0]:.4f}->{an:.4f}, lift {s[2]}->{lift}')
                bad += 1
        log(f'    lift 比对完成，{bad} 处差异 / {len(stored_l)} 条已存')

        r = await conn.fetchrow(SQL_BRAND_COMPARE, city)
        s = await conn.fetchrow('SELECT * FROM brand_compare_cache WHERE city=$1', city)
        log(f'\n    brand_compare 计算值: luckin={r["luckin_count"]} '
            f'sb={r["starbucks_count"]} avgD={r["avg_nearest_sb_m"]} '
            f'co={r["co_located_200m"]}')
        if s:
            log(f'    brand_compare 已存值: luckin={s["luckin_count"]} '
                f'sb={s["starbucks_count"]} avgD={s["avg_nearest_sb_m"]} '
                f'co={s["co_located_200m"]} rate={s["co_location_rate"]}')
        await conn.close()
        OUT.close()
        return

    # ── apply ─────────────────────────────────────────────
    await conn.execute('DELETE FROM poi_correlation_cache WHERE city=$1', city)
    await conn.executemany('''
        INSERT INTO poi_correlation_cache
            (poi_category, radius, brand, city, avg_count, stddev,
             min_count, max_count, store_count)
        VALUES ($1,$2,$3,$7,$4,$5,$6,$8,$9)
    ''', [(c, r, b, a, s, mn, city, mx, sc) for c, r, b, a, s, mn, mx, sc in rows_c])

    await conn.execute('DELETE FROM poi_lift_cache WHERE city=$1', city)
    await conn.executemany('''
        INSERT INTO poi_lift_cache
            (poi_category, radius, brand, city, avg_near_store, avg_random, lift)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
    ''', [(c, r, b, city, a, ar, lf) for c, r, b, a, ar, lf in rows_l])

    r = await conn.fetchrow(SQL_BRAND_COMPARE, city)
    rate = (round(r['co_located_200m'] * 100.0 / r['luckin_count'], 1)
            if r['luckin_count'] else 0.0)
    await conn.execute('DELETE FROM brand_compare_cache WHERE city=$1', city)
    await conn.execute('''
        INSERT INTO brand_compare_cache
            (city, luckin_count, starbucks_count, avg_nearest_sb_m,
             co_located_200m, co_location_rate)
        VALUES ($1,$2,$3,$4,$5,$6)
    ''', city, int(r['luckin_count']), int(r['starbucks_count']),
        r['avg_nearest_sb_m'], int(r['co_located_200m']), rate)

    log(f'\n[3/4] poi_correlation_cache 已写入 {len(rows_c)} 行')
    log(f'[4/4] poi_lift_cache 已写入 {len(rows_l)} 行')
    log(f'      brand_compare_cache: luckin={r["luckin_count"]} '
        f'sb={r["starbucks_count"]} avgD={r["avg_nearest_sb_m"]} '
        f'co={r["co_located_200m"]} rate={rate}')
    log('\n完成（__ALL__ 全国汇总行未改动）')

    await conn.close()
    OUT.close()
    print('done')


if __name__ == '__main__':
    asyncio.run(main())
