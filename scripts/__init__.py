"""
瑞幸咖啡空间分析 - 数据脚本包
=============================

本包包含数据管道的全部 7 个脚本，按照执行顺序依次为：

  1. config.py          — 全局配置（API Key、城市列表、POI 分类映射、数据库连接）
  2. fetch_stores.py    — 门店数据采集（瑞幸 + 星巴克，高德 POI 搜索 API）
  3. fetch_pois.py      — POI 数据采集（高德 API 精细 POI，覆盖 20 个重点城市）
  4. import_osm.py      — OSM 数据导入（道路网络、地铁站、建筑轮廓，使用 osmnx）
  5. compute_features.py— 空间特征计算（缓冲区统计、KDE 网格、Lift 值、城市层级）
  6. train_model.py     — 选址模型训练（XGBoost 二分类 + SHAP 可解释性分析）

数据管道整体流程：
  [采集] fetch_stores + fetch_pois + import_osm
    → [存储] PostgreSQL + PostGIS（schema.sql）
      → [计算] compute_features（store_poi_stats、kde_grid、lift_scores）
        → [建模] train_model（XGBoost 二分类：门店位置 vs 随机位置）
          → [分析] sql/analytics.sql（时空演变、聚类、渗透率）

注意：
  - 脚本间有依赖关系，必须按顺序执行（采集 → 计算 → 建模）
  - 所有脚本依赖 config.py 中的配置（API Key、DB 连接等）
  - 预计总运行时间（20 个城市）：采集约 2-3 小时，计算约 30 分钟，建模约 5 分钟
"""
