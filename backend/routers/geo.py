"""
逆地理编码 API 路由模块

本模块在系统架构中位于接口层，负责将 WGS84 经纬度坐标转换为
可读的行政区划信息（省/市/区）。这是空间分析平台的基础设施服务，
为前端地图点击、选址评分的城市自动识别等场景提供支撑。

技术方案：
- 调用高德地图（AMap）Web API 的逆地理编码接口
- 使用 aiohttp 进行异步 HTTP 请求，与 FastAPI 异步架构保持一致
- 对直辖市等特殊行政区划做兼容处理（city 字段为空时回退到 province）
"""
from fastapi import APIRouter, Query
import aiohttp

from ..database import settings

router = APIRouter(prefix='/api/geo', tags=['geo'])

# 高德地图 Web API 密钥 —— 从环境变量 AMAP_API_KEY 读取
# （配置项定义见 backend/database.py 的 Settings.amap_api_key）
#
# 安全说明：此密钥此前以明文硬编码在本文件中，已提交到版本库会有泄露风险。
# 现改为环境变量注入：本地开发写在 .env（已在 .gitignore 中），
# 部署时通过环境变量或容器 secrets 传入。
AMAP_KEY = settings.amap_api_key

# 高德逆地理编码 API 端点
# 文档: https://lbs.amap.com/api/webservice/guide/api/georegeo
REVERSE_GEO_URL = 'https://restapi.amap.com/v3/geocode/regeo'


@router.get('/reverse')
async def reverse_geocode(
    lng: float = Query(..., description='经度（WGS84 坐标系，GCJ02 偏差在国内可接受）'),
    lat: float = Query(..., description='纬度（WGS84 坐标系）'),
):
    """
    逆地理编码 — 根据经纬度坐标返回行政区划信息

    调用高德地图逆地理编码 API（/v3/geocode/regeo），将经纬度转换为
    省、市、区三级行政区划名称。

    API 请求参数说明（extensions='base'）：
    - key:      高德 Web API 密钥
    - location: 经纬度坐标，格式为 "经度,纬度"（注意顺序）
    - extensions: 'base' 返回基础信息（省市区），'all' 还会包含周边 POI

    响应解析逻辑：
    1. status != '1' → API 调用失败，返回空字段
    2. 普通城市：addressComponent.city 为城市名（如"北京市"）
    3. 直辖市/省直管县：city 字段为 []（空数组），
       此时使用 province 作为 city（如北京市的 city 为 []，用 province"北京市"替代）

    Args:
        lng: 经度坐标
        lat: 纬度坐标

    Returns:
        dict: {
            city:     str — 城市名（含"市"后缀，与数据库 stores.city 字段一致）
            district: str — 区县名
            province: str — 省级行政区名
        }
        字段值可能为空字符串（API 调用失败或数据缺失时）
    """
    async with aiohttp.ClientSession() as session:
        # 构造请求参数
        params = {
            'key': AMAP_KEY,
            'location': f'{lng},{lat}',  # 高德API要求格式: "经度,纬度"
            'extensions': 'base',          # 仅返回基础行政区划，不包含周边POI
        }
        async with session.get(REVERSE_GEO_URL, params=params) as resp:
            data = await resp.json()

    # 检查API返回状态，status='1' 表示成功
    if data.get('status') != '1':
        return {'city': '', 'district': '', 'province': ''}

    # 提取地址组件: regeocode → addressComponent → {province, city, district, ...}
    comp = data.get('regeocode', {}).get('addressComponent', {})

    # 处理直辖市特殊情况：
    # 北京/上海/天津/重庆的 city 字段在 extensions=base 时可能返回 []
    # 此时用 province 作为 city 的值
    city = comp.get('city')
    if not city or city == []:
        city = comp.get('province', '')

    # 返回结果中的城市名保留"市"后缀（如"北京市"），
    # 这与数据库 stores.city 字段的存储格式一致
    return {
        'city': city if isinstance(city, str) else '',
        'district': comp.get('district', '') if isinstance(comp.get('district'), str) else '',
        'province': comp.get('province', '') if isinstance(comp.get('province'), str) else '',
    }
