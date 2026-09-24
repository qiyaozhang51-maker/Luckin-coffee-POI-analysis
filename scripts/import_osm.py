"""
OSM（OpenStreetMap）数据导入脚本
================================
数据管道位置：第 1 步（数据采集阶段）
数据来源：OpenStreetMap 全球开放地图数据（通过 osmnx Python 库下载）
采集范围：config.FOCUS_CITIES 中定义的 20 个重点城市
导入内容：
  1. 地铁站（metro stations）—— 来自 railway=station + station=subway 标签
  2. 建筑轮廓（building footprints）—— 来自 building=* 标签，取质心作为 POI 点
  3. 道路网络（road network）—— 来自 highway=primary/secondary/trunk/motorway 标签

数据导入策略：
  - 使用 osmnx.features_from_place() 获取面状要素（地铁站、建筑）
  - 使用 osmnx.graph_from_place() 获取道路网络图
  - 所有要素取质心（centroid）统一存储为 POI 点（WGS84 / SRID 4326）
  - 建筑数据不做全量导入（每个建筑取质心作为 POI 点，不保留轮廓 polygon）
  - 道路仅保留主干道（primary/secondary/trunk/motorway），以路段中点存储

OSM 标签映射（OSM_TAG_MAP）：
  - 将 OSM 的 key=value 标签体系映射到本项目的统一 POI 分类
  - 标签体系参考：https://wiki.openstreetmap.org/wiki/Map_features
  - amenity（设施）: university/college/school/hospital/bank/cafe/restaurant 等
  - shop（商店）: mall/department_store/supermarket/convenience
  - building（建筑类型）: commercial/office/retail/apartments/residential
  - public_transport（公共交通）: station/stop_position
  - railway（铁路）: station/subway_entrance

预计运行时间（20 个城市）：
  - 每个城市约 5-15 分钟（取决于城市面积和 OSM 数据量）
  - 总计约 2-3 小时（osmnx 在线下载 OSM 数据较慢）

网络要求：
  - 需要稳定的互联网连接（osmnx 实时从 OSM 服务器下载数据）
  - 首次导入建议逐个城市运行以排查网络问题

与高德数据的互补关系：
  - 高德 API（fetch_pois.py）：精度高、更新及时，但有调用配额和每日限额
  - OSM 数据（本脚本）：免费无限制，覆盖全国范围，但数据完整性因城市而异
  - 两者互为补充，数据来源字段标记为 'osm' 区别于 'amap'

依赖：
  - config.FOCUS_CITIES、config.DB_CONFIG
  - osmnx >= 1.5.0（需要网络下载 OSM 数据）
  - geopandas >= 0.12.0
  - sql/schema.sql 中 pois 表需先建好

执行方式：
  python scripts/import_osm.py
"""
import asyncio
import asyncpg
import osmnx as ox
import geopandas as gpd
from pathlib import Path
from config import FOCUS_CITIES, DB_CONFIG

# ============================================================
# OSM 标签 → 本项目统一 POI 分类
# OSM 使用 key=value 的标签体系，本项目将其简化为粗分类
# 参考文档：https://wiki.openstreetmap.org/wiki/Map_features
#
# 映射结构：
#   OSM_TAG_MAP[主键key][值value] = 本项目分类
# 例如：
#   OSM_TAG_MAP['amenity']['university'] = 'university'
#   OSM_TAG_MAP['building']['commercial'] = 'office'
# ============================================================
OSM_TAG_MAP = {
    # ======== amenity（设施/便利设施）========
    'amenity': {
        'university': 'university',  # 大学
        'college': 'university',     # 学院（归入大学类）
        'school': 'school',          # 中小学
        'hospital': 'hospital',      # 医院
        'bank': 'bank',              # 银行
        'cafe': 'cafe',              # 咖啡厅
        'restaurant': 'restaurant',  # 餐厅
        'fast_food': 'restaurant',   # 快餐（归入餐厅类）
        'bar': 'restaurant',         # 酒吧（归入餐厅类）
        'pub': 'restaurant',         # 酒馆（归入餐厅类）
    },
    # ======== shop（商店类型）========
    'shop': {
        'mall': 'mall',               # 购物中心
        'department_store': 'mall',   # 百货商场（归入购物中心类）
        'supermarket': 'supermarket', # 超市
        'convenience': 'convenience', # 便利店
    },
    # ======== building（建筑类型）========
    'building': {
        'commercial': 'office',       # 商业建筑 → 办公类
        'office': 'office',           # 办公楼
        'retail': 'mall',             # 零售建筑 → 商业类
        'apartments': 'residential',  # 公寓 → 居住类
        'residential': 'residential', # 住宅建筑 → 居住类
    },
    # ======== public_transport（公共交通）========
    'public_transport': {
        'station': 'metro',           # 公共交通站点 → 地铁类
        'stop_position': 'bus',       # 公交停靠点
    },
    # ======== railway（铁路）========
    'railway': {
        'station': 'metro',           # 火车站/地铁站 → 地铁类
        'subway_entrance': 'metro',   # 地铁入口
    },
}


async def import_metro_stations(conn, city_name: str) -> int:
    """导入城市地铁站数据。

    数据获取策略（两层降级）：
    1. 优先使用 OSM 标签 railway=station + station=subway（最精确）
    2. 若该标签无数据，降级使用 public_transport=station（较宽泛）
    3. 将获取的面状要素的质心坐标作为地铁站点位

    函数逻辑：
    - osmnx.features_from_place() 返回 GeoDataFrame，包含 geometry 列
    - geometry 可能是 Point / Polygon / MultiPolygon 类型
    - 统一取 .centroid 获得质心点坐标
    - OSM 的 name 字段可能是列表（多语言标注），取第一个元素

    Args:
        conn:      asyncpg 数据库连接
        city_name: 城市名称（中文），如 "北京"

    Returns:
        int: 成功导入的地铁站数量（0 表示该城市无 OSM 地铁数据）
    """
    try:
        # 第一层：精确标签 railway=station + station=subway
        tags = {'railway': 'station', 'station': 'subway'}
        gdf = ox.features_from_place(f'{city_name}, China', tags)

        # 第二层（降级）：若精确标签无数据，使用更宽泛的 public_transport=station
        if gdf.empty:
            tags2 = {'public_transport': 'station'}
            gdf = ox.features_from_place(f'{city_name}, China', tags2)

        if gdf.empty:
            print(f'  {city_name}: 未找到地铁站数据')
            return 0

        inserted = 0
        for _, row in gdf.iterrows():
            if row.geometry is None:
                continue
            # 统一取质心：Point 的质心即其自身，Polygon 的质心为中心点
            centroid = row.geometry.centroid
            name = row.get('name', '')
            # OSM 的 name 字段可能为列表（多语言标注），取第一个
            if isinstance(name, list):
                name = name[0] if name else ''

            try:
                await conn.execute('''
                    INSERT INTO pois (name, category, lng, lat, geom, city, data_source)
                    VALUES ($1, 'metro', $2, $3,
                            ST_SetSRID(ST_MakePoint($2, $3), 4326),
                            $4, 'osm')
                    ON CONFLICT DO NOTHING
                ''', str(name), centroid.x, centroid.y, city_name)
                inserted += 1
            except Exception:
                pass

        print(f'  {city_name}: {inserted} 地铁站导入')
        return inserted
    except Exception as e:
        print(f'  {city_name} OSM地铁导入失败: {e}')
        return 0


async def import_buildings(conn, city_name: str) -> int:
    """导入城市建筑轮廓数据（每个建筑取质心作为一个 POI 点）。

    数据处理：
    - 下载所有 building=* 标签的面状要素
    - 仅保留 Polygon 和 MultiPolygon 类型（过滤 Point/LineString 等非面要素）
    - 通过 OSM_TAG_MAP['building'] 将 building 类型映射到本项目分类
    - 不在映射表中的建筑类型（如 building=yes / house / shed）被过滤掉
    - 取每个建筑的质心（centroid）作为 POI 坐标点

    注意：
    - 大城市建筑数据量极大（数十万条），全量导入耗时长
    - 当前策略：全量遍历但仅导入有明确类型的建筑
    - 如需性能优化可增加随机采样或按 building 类型过滤

    Args:
        conn:      asyncpg 数据库连接
        city_name: 城市名称（中文）

    Returns:
        int: 成功导入的建筑 POI 数量
    """
    try:
        tags = {'building': True}
        gdf = ox.features_from_place(f'{city_name}, China', tags)

        if gdf.empty:
            print(f'  {city_name}: 未找到建筑数据')
            return 0

        # 遍历所有建筑物，每个建筑取其质心作为一个 POI 点
        inserted = 0
        for _, row in gdf.iterrows():
            # 仅处理面状要素（Polygon / MultiPolygon）
            if row.geometry is None or row.geometry.geom_type not in ('Polygon', 'MultiPolygon'):
                continue

            # 获取 OSM building 标签值
            building_type = row.get('building', '')
            if isinstance(building_type, list):
                building_type = building_type[0] if building_type else ''
            # 通过映射表获取本项目分类，不在映射表中的跳过
            category = OSM_TAG_MAP.get('building', {}).get(str(building_type), None)
            if category is None:
                continue

            centroid = row.geometry.centroid
            name = row.get('name', '')
            if isinstance(name, list):
                name = name[0] if name else ''

            try:
                await conn.execute('''
                    INSERT INTO pois (name, category, lng, lat, geom, city, data_source)
                    VALUES ($1, $2, $3, $4,
                            ST_SetSRID(ST_MakePoint($3, $4), 4326),
                            $5, 'osm')
                    ON CONFLICT DO NOTHING
                ''', str(name), category, centroid.x, centroid.y, city_name)
                inserted += 1
            except Exception:
                pass

        print(f'  {city_name}: {inserted} 建筑POI导入')
        return inserted
    except Exception as e:
        print(f'  {city_name} OSM建筑导入失败: {e}')
        return 0


async def import_road_network(conn, city_name: str) -> int:
    """导入城市道路网络（仅主干道和次干道）。

    道路层级过滤：
    - motorway（高速公路）：连接城市间的高速道路
    - trunk（国道/快速路）：城市间主要干线
    - primary（主干道）：城市内部一级道路
    - secondary（次干道）：城市内部二级道路
    - 过滤掉 tertiary（支路）、residential（小区道路）、unclassified 等低层级道路

    数据处理：
    - osmnx.graph_from_place() 返回 networkx 有向图
    - graph_to_gdfs() 将图拆分为 nodes（节点/交叉口）和 edges（路段）
    - 本函数导入 edges（路段），取每个路段的中点作为 road POI
    - 路段名称格式：{highway_type}_road（如 "primary_road"）

    注意：
    - network_type='drive' 表示获取机动车可通行道路网络
    - 不包括步行道（footway）、自行车道（cycleway）等

    Args:
        conn:      asyncpg 数据库连接
        city_name: 城市名称（中文）

    Returns:
        int: 成功导入的主干道路段数量
    """
    try:
        # network_type='drive' 获取机动车可通行道路网络
        G = ox.graph_from_place(f'{city_name}, China', network_type='drive')
        # 将 networkx 图分解为节点 GeoDataFrame 和边 GeoDataFrame
        nodes, edges = ox.graph_to_gdfs(G)

        if nodes.empty:
            return 0

        # 遍历所有路段，仅保留主干道层级
        inserted = 0
        for _, row in edges.iterrows():
            highway = row.get('highway', '')
            if isinstance(highway, list):
                highway = highway[0] if highway else ''

            # 道路层级过滤：仅保留 motorway / trunk / primary / secondary
            if highway not in ('primary', 'secondary', 'trunk', 'motorway'):
                continue

            if row.geometry is None:
                continue
            # 取路段中点（LineString 的 centroid）作为道路 POI 坐标
            midpoint = row.geometry.centroid

            try:
                await conn.execute('''
                    INSERT INTO pois (name, category, lng, lat, geom, city, data_source)
                    VALUES ($1, 'road', $2, $3,
                            ST_SetSRID(ST_MakePoint($2, $3), 4326),
                            $4, 'osm')
                    ON CONFLICT DO NOTHING
                ''', f'{highway}_road', midpoint.x, midpoint.y, city_name)
                inserted += 1
            except Exception:
                pass

        print(f'  {city_name}: {inserted} 主干道路段导入')
        return inserted
    except Exception as e:
        print(f'  {city_name} OSM道路导入失败: {e}')
        return 0


async def main():
    """主函数：依次导入 20 个城市的 OSM 数据。

    执行流程：
    1. 建立数据库连接
    2. 遍历 20 个重点城市
    3. 对每个城市依次导入：地铁站 → 建筑 → 道路网络
    4. 输出每个城市的导入统计摘要

    输出：
    - 数据库 pois 表中新增记录（data_source='osm'）
    """
    conn = await asyncpg.connect(**DB_CONFIG)

    for city_info in FOCUS_CITIES:
        city_name = city_info['name']
        print(f'\n导入 {city_name} OSM数据...')

        # 依次导入三类 OSM 数据
        n_metro = await import_metro_stations(conn, city_name)
        n_buildings = await import_buildings(conn, city_name)
        n_roads = await import_road_network(conn, city_name)

        # 城市导入摘要
        print(f'  OK {city_name}: metro={n_metro}, buildings={n_buildings}, roads={n_roads}')

    await conn.close()
    print('\n========== OSM数据导入完成 ==========')


if __name__ == '__main__':
    asyncio.run(main())
