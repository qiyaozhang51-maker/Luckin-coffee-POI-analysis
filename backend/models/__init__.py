"""
models 包 — Pydantic 数据模型定义

本包定义了 API 请求/响应使用的数据模型（Schema），在系统架构中负责：
- 声明 API 接口的输入输出数据结构，用于 FastAPI 的自动文档生成与请求校验
- 将数据库查询结果（asyncpg Record）序列化为 JSON 响应
- 通过 Field 描述为前端和 API 文档提供字段说明

子模块：
- schemas.py — 门店、POI 统计、品牌对比、选址预测等全部 Pydantic 模型
"""
