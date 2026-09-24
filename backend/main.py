"""
瑞幸咖啡空间分析平台 — FastAPI 后端应用入口

本模块是后端服务的主入口文件，在系统架构中位于最顶层，负责：
1. 创建并配置 FastAPI 应用实例
2. 注册 CORS 中间件，允许前端跨域请求
3. 挂载所有业务路由（stores / analysis / prediction / geo）
4. 提供根路径与健康检查端点

启动方式：
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import stores, analysis, prediction, geo

# ---- FastAPI 应用实例 ----
app = FastAPI(
    title='Luckin Spatial Analysis API',
    description='瑞幸咖啡门店选址与POI时空演变分析平台',
    version='1.0.0',
)

# ---- CORS 中间件配置 ----
# 允许所有来源的跨域请求，开发阶段便于前后端分离调试
# 生产环境应限制 allow_origins 为具体域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# ---- 注册业务路由模块 ----
# 每个路由器在各自模块中定义了 prefix，此处直接挂载
app.include_router(stores.router)
app.include_router(analysis.router)
app.include_router(prediction.router)
app.include_router(geo.router)


@app.get('/')
async def root():
    """
    根路径 — 返回 API 基本信息

    Returns:
        dict: 包含 API 名称、版本号、文档地址
    """
    return {
        'name': 'Luckin Spatial Analysis API',
        'version': '1.0.0',
        'docs': '/docs',
    }


@app.get('/health')
async def health():
    """
    健康检查端点 — 供监控系统/负载均衡器探测服务可用性

    Returns:
        dict: {"status": "ok"} 表示服务正常运行
    """
    return {'status': 'ok'}
