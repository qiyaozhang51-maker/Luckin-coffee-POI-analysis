"""
Pydantic 数据模型（Schema）定义

本模块在系统架构中位于数据表示层，定义了所有 API 的请求/响应数据结构：
- 用于 FastAPI 自动生成 OpenAPI 文档（Swagger UI / ReDoc）
- 用于请求参数的自动校验与类型转换
- 用于数据库查询结果到 JSON 响应的序列化
- 通过 from_attributes = True 支持 ORM 模式（直接从 asyncpg Record 字典构建）

模型分组：
- Store*:        门店基础信息与列表响应
- POIStats:      POI（兴趣点）统计与 Lift 值
- CityStats:     城市级别门店统计
- Timeline:      时间轴（月度新增/累计门店数）
- KDE/Brand:     热力图与品牌对比
- Prediction*:   选址评分与推荐热力图
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date, datetime


class StoreBase(BaseModel):
    """
    门店基础信息模型 — 用于创建/更新门店时校验输入数据
    """
    name: str = Field(..., description='门店名称')
    brand: str = Field(..., description='品牌标识: luckin 或 starbucks')
    address: Optional[str] = Field(None, description='详细地址')
    lng: float = Field(..., description='经度（WGS84 坐标系）')
    lat: float = Field(..., description='纬度（WGS84 坐标系）')
    city: Optional[str] = Field(None, description='所在城市名称')
    district: Optional[str] = Field(None, description='所在行政区/区县')
    open_date: Optional[date] = Field(None, description='开业日期')
    store_type: Optional[str] = Field(None, description='门店类型（如：旗舰店、快取店、悠享店）')
    status: Optional[str] = Field('营业中', description='经营状态：营业中 或 已关闭')


class StoreResponse(StoreBase):
    """
    门店查询响应模型 — 包含数据库自动生成的元数据字段
    """
    id: int = Field(..., description='门店唯一标识（数据库主键）')
    created_at: Optional[datetime] = Field(None, description='记录创建时间')

    class Config:
        # 允许从 ORM 对象/字典属性直接构建模型实例
        from_attributes = True


class StoreWithPOI(StoreResponse):
    """
    门店 + 周边POI统计响应模型

    扩展了 StoreResponse，附加不同半径范围内各类 POI 的数量统计。
    poi_stats 结构示例：
        {
            "200": {"地铁站": 3, "写字楼": 12, "商场": 5},
            "500": {"地铁站": 8, "写字楼": 45, "商场": 15},
            "1000": {"地铁站": 15, "写字楼": 120, "商场": 30}
        }
    """
    poi_stats: Optional[dict] = Field(None, description='周边POI统计，格式: {半径: {类别: 数量}}')


class StoreListResponse(BaseModel):
    """门店列表分页响应模型"""
    total: int = Field(..., description='符合条件的门店总数')
    stores: list[StoreResponse] = Field(default_factory=list, description='当前页门店列表')


class POIStatsResponse(BaseModel):
    """
    POI 统计响应模型 — 用于 POI 关联分析和 Lift 值分析接口

    lift 字段解释：
        lift > 1: 门店周边该类别 POI 密度高于随机点位，表示门店倾向于选址在该类别附近
        lift < 1: 门店周边该类别 POI 密度低于随机点位，表示门店倾向于远离该类别
        lift ≈ 1: 门店选址与该类别 POI 无明显关联
    """
    category: str = Field(..., description='POI类别名称（如：地铁站、写字楼、商场）')
    radius: int = Field(..., description='统计半径，单位：米')
    avg_count: float = Field(..., description='门店周边该类别POI的平均数量')
    lift: Optional[float] = Field(None, description='Lift值：门店周边富集程度 / 随机点位富集程度')


class CityStatsResponse(BaseModel):
    """城市级别门店统计响应模型"""
    city: str = Field(..., description='城市名称')
    brand: str = Field(..., description='品牌标识')
    store_count: int = Field(..., description='该城市门店总数')
    first_open_date: Optional[date] = Field(None, description='首店开业日期')
    last_open_date: Optional[date] = Field(None, description='最近开业日期')
    tier_label: Optional[str] = Field(None, description='城市层级标签（一线/新一线/二线/三线/四线/五线）')


class TimelineDataPoint(BaseModel):
    """
    时间轴数据点模型 — 用于门店扩张时间序列图

    每个数据点代表一个月的统计：
    - new_stores: 当月新开门店数
    - cumulative_stores: 截至当月的累计门店总数（窗口函数累加）
    """
    month: str = Field(..., description='月份，格式 YYYY-MM')
    brand: str = Field(..., description='品牌标识')
    new_stores: int = Field(..., description='当月新开门店数')
    cumulative_stores: int = Field(..., description='截至当月的累计门店数')


class KDEGridResponse(BaseModel):
    """核密度估计（KDE）热力图 GeoJSON 响应模型"""
    geojson: dict = Field(..., description='GeoJSON 格式的 KDE 热力图数据')


class BrandComparisonResponse(BaseModel):
    """
    品牌对比分析响应模型 — 瑞幸 vs 星巴克

    co_located: 瑞幸门店中，200米范围内存在星巴克门店的数量
    avg_distance_m: 瑞幸门店到最近星巴克门店的平均距离（米）
    """
    city: str = Field(..., description='城市名称')
    luckin_count: int = Field(..., description='瑞幸门店数量')
    starbucks_count: int = Field(..., description='星巴克门店数量')
    co_located: int = Field(..., description='200米内与星巴克共址的瑞幸门店数')
    avg_distance_m: Optional[float] = Field(None, description='瑞幸到最近星巴克的平均距离（米）')


class PredictionRequest(BaseModel):
    """
    选址评分请求模型

    传入待评估的地理位置坐标及可选的城市信息，
    系统将基于周边 POI 密度和城市层级计算综合评分。
    """
    lng: float = Field(..., description='待评分位置的经度')
    lat: float = Field(..., description='待评分位置的纬度')
    city: Optional[str] = Field(None, description='所在城市（用于获取城市层级加成）')


class PredictionResponse(BaseModel):
    """
    选址评分响应模型

    score: 0~1 的综合评分，值越高表示该位置越适合开设新店
    features: 输入的 POI 特征值字典（21维特征）
    top_positive: 对评分贡献最大的正面特征（按权重降序前5）
    top_negative: 对评分贡献最小的特征（当前实现中与正面对称，实际取决于特征取值）
    """
    score: float = Field(..., description='综合评分（0~1），越高越适合开店')
    features: dict = Field(..., description='POI特征值字典')
    top_positive: list[dict] = Field(default_factory=list, description='正面贡献特征 Top 5')
    top_negative: list[dict] = Field(default_factory=list, description='负面贡献特征（权重最低）')


class PredictionHeatmapResponse(BaseModel):
    """选址推荐热力图 GeoJSON 响应模型"""
    geojson: dict = Field(..., description='GeoJSON FeatureCollection 格式的热力图数据')
    city: str = Field(..., description='城市名称')


class HeatmapRequest(BaseModel):
    """
    门店热力图请求模型

    支持按品牌、年份、城市筛选门店数据，生成对应的热力图。
    """
    brand: Optional[str] = Field('luckin', description='品牌筛选: luckin 或 starbucks')
    year: Optional[int] = Field(None, description='年份筛选（仅统计该年及之前开业的门店）')
    city: Optional[str] = Field(None, description='城市筛选')
