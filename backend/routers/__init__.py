"""
routers 包 — FastAPI 路由模块

本包将 API 端点按业务领域拆分为独立的路由模块，在系统架构中负责：
- 定义各业务域的 RESTful 接口（GET/POST 端点）
- 调用数据库连接执行查询，并将结果转换为 Schema 模型返回
- 封装领域特有的业务逻辑（空间查询、Lift 值计算、评分算法等）

子模块：
- stores.py      — 门店 CRUD、热力图、时间轴、城市列表
- analysis.py    — POI 关联分析、Lift 值、品牌对比、城市层级统计
- prediction.py  — 选址评分、城市级别推荐热力图
- geo.py         — 逆地理编码（坐标 -> 城市名）
"""
