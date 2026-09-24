"""
backend 包 — 瑞幸咖啡空间分析平台后端服务

本包是 FastAPI 后端应用的核心模块，在系统架构中负责：
- 提供 RESTful API 接口，供前端 SPA 调用
- 连接 PostgreSQL/PostGIS 数据库，执行空间查询与分析
- 封装门店数据、POI 关联分析、选址预测、地理编码等业务逻辑

子模块结构：
- main.py          — FastAPI 应用入口，注册路由与中间件
- database.py      — 数据库连接管理与配置定义
- models/          — Pydantic 数据模型（schemas.py）
- routers/         — API 路由模块（stores, analysis, prediction, geo）
"""
