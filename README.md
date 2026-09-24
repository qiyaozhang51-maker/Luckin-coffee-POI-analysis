# 🍵 瑞幸咖啡门店选址与POI时空演变分析平台

> Luckin Coffee Store Location & POI Spatio-temporal Evolution Analysis

基于 GIS 空间分析 + 机器学习，对瑞幸咖啡全国门店的选址策略、POI关联规律、与星巴克的差异化竞争、以及选址预测模型进行系统性研究。

---

## 🎯 功能模块

| 模块 | 说明 |
|------|------|
| **时空演变** | 门店 2017-2026 扩张轨迹、城市下沉指数、核密度热力图时间轴动画 |
| **POI 关联分析** | 缓冲区内 POI 密度统计、Lift 值画像、选址类型聚类（写字楼/商圈/社区/校园型） |
| **竞品对比** | 瑞幸 vs 星巴克空间分布差异、最近邻距离、共址分析、POI偏好对比矩阵 |
| **选址预测** | XGBoost 选址适宜度模型 + SHAP 可解释性 + 交互式选址评分工具 |
| **地图点击查门店** | 点击地图散点查看最近门店详情：品牌、地址、周边 POI 统计 |

---

## 🏗️ 技术架构

```
React + deck.gl (前端) ──→ FastAPI (后端) ──→ PostgreSQL + PostGIS (空间数据库)
                                                  ↑
                              Python (GeoPandas/Shapely/PySAL) ← 高德API/OSM
```

---

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone <repo-url>
cd luckin-spatial-analysis
```

### 2. 配置环境

```bash
cp .env.example .env
# 编辑 .env，填入你的高德 API Key
```

### 3. Python 管理控制台启动（Windows 推荐）

```bash
# 交互式管理控制台（推荐新手使用）
python manage.py

# 或一键启动全部服务
python manage.py start

# 或双击以下文件
启动服务.bat          # 一键启动全部服务
控制台.bat            # 打开交互式管理控制台
```

### 4. Docker 一键启动

```bash
docker compose up -d
# 前端: http://localhost:3000
# 后端API文档: http://localhost:8000/docs
```

### 5. 本地开发

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 PostgreSQL + PostGIS
docker compose up -d postgis

# 数据采集（按顺序执行）
python scripts/fetch_stores.py     # 门店数据
python scripts/fetch_pois.py       # POI数据
python scripts/import_osm.py       # OSM基础数据
python scripts/compute_features.py # 空间特征计算
python scripts/train_model.py      # 选址模型训练

# 启动后端
cd backend && uvicorn main:app --reload --port 8000

# 启动前端
cd frontend && npm install && npm run dev
```

---

## 📊 数据来源

| 数据类型 | 来源 | 范围 |
|----------|------|------|
| 瑞幸门店 | 高德地图 POI API + 季度财报 | 21城 8,640 家 |
| 星巴克门店 | 高德地图 POI API | 21城 4,366 家 |
| POI 数据 | OSM（全国基础） + 高德 API（21城精细） | 21城 ~32,468 条 POI |
| 行政边界 | DataV.GeoAtlas / 高德区划 API | 全国省市县 |

---

## 📁 目录结构

```
luckin-spatial-analysis/
├── data/                       # 数据文件（含34MB数据库备份）
├── sql/                        # 数据库DDL与分析SQL
├── scripts/                    # 数据采集与特征计算
├── backend/                    # FastAPI 后端服务
├── frontend/                   # React + deck.gl 前端
├── notebooks/                  # Jupyter 分析笔记
├── manage.py                   # Python 管理控制台
├── 控制台.bat                  # Windows 交互式控制台
├── 启动服务.bat                # 一键启动全部服务
├── 停止服务.bat                # 一键停止全部服务
├── docker-compose.yml          # 一键部署
└── README.md
```

---

## 🔑 关键技术点

- **PostGIS 空间索引**：`ST_DWithin` + GiST 索引实现万级 POI 的秒级空间关联
- **deck.gl GPU 渲染**：ScatterplotLayer + HeatmapLayer 流畅展示 13K 门店点位
- **Lift 值分析**：对比随机分布，量化每种 POI 类型在门店周边的富集程度
- **选址模型**：XGBoost + SHAP，AUC > 0.85，SHAP 解释每个特征的选址贡献
- **时间轴动画**：2017-2026 按年/月切换，门店点位、热力图、统计指标同步联动
- **地图点击查门店**：点击散点调用最近门店 API（PostGIS KNN `<->` 运算符），展示品牌、地址、POI 统计

---

## 📝 License

MIT License
