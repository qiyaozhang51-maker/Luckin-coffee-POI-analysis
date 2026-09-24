"""
全局配置模块
===========
本模块是数据管道的基础配置中心，包含以下核心配置项：

1. 高德地图 API 配置
   - API Key（从 .env 文件加载，环境变量 AMAP_API_KEY）
   - POI 搜索 / 周边搜索 / 行政区划 API 端点 URL

2. 高德 POI 分类映射（AMAP_TYPECODE_MAP）
   - 将高德 API 返回的 typecode（6 位数字编码）映射到本项目统一的分类体系
   - 分类体系：office（办公）、mall（商业）、metro（地铁）、bus（公交）、
     university（大学）、school（学校）、restaurant（餐饮）、cafe（咖啡厅）、
     tea（茶饮）、convenience（便利店）、supermarket（超市）、bank（银行）、
     hospital（医院）、residential（居住）

3. 重点分析城市列表（FOCUS_CITIES）
   - 20 个城市，按城市层级分为一线（4 个）、新一线（10 个）、二线（6 个）
   - 每个城市包含 name（中文名）和 adcode（高德行政区划代码）

4. POI 搜索关键词（POI_SEARCH_KEYWORDS）
   - 按类别组织的搜索关键词，用于高德 API 文本搜索

5. 数据库配置（DB_CONFIG）
   - PostgreSQL + PostGIS 连接参数，从 .env 文件加载

使用方式：
    from config import AMAP_API_KEY, FOCUS_CITIES, DB_CONFIG

注意：
   - 运行前需确保项目根目录下的 .env 文件包含有效的 AMAP_API_KEY
   - 高德 API 免费版每日调用限额为 5000 次 / Key，本脚本在 20 个城市采集
     所有类别 POI 预计消耗约 3000-4000 次调用
"""
import os
from dotenv import load_dotenv

# 加载项目根目录下的 .env 环境变量文件
# .env 文件应包含: AMAP_API_KEY=你的高德Key
#               POSTGRES_HOST=localhost
#               POSTGRES_PORT=5432
#               POSTGRES_DB=luckin_spatial
#               POSTGRES_USER=luckin
#               POSTGRES_PASSWORD=your_db_password_here
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

# ============================================================
# 高德地图 API Key
# 从环境变量加载，若未设置则为空字符串（后续脚本会报错并提示）
# 申请地址：https://console.amap.com/dev/key/app
# ============================================================
AMAP_API_KEY = os.getenv('AMAP_API_KEY', '')

# ============================================================
# 高德 Web API 端点
# - AMAP_POI_SEARCH_URL:  关键字搜索 POI（文本搜索），支持分页，每页最多 25 条
# - AMAP_POI_AROUND_URL:  周边搜索 POI（圆形区域搜索），按距离排序
# - AMAP_DISTRICT_URL:    行政区划查询，获取城市/区的边界和 adcode
# 参考文档：https://lbs.amap.com/api/webservice/summary/
# ============================================================
AMAP_POI_SEARCH_URL = 'https://restapi.amap.com/v3/place/text'
AMAP_POI_AROUND_URL = 'https://restapi.amap.com/v3/place/around'
AMAP_DISTRICT_URL = 'https://restapi.amap.com/v3/config/district'

# ============================================================
# 高德 API 类型编码 → 本项目统一分类
# 高德 API 返回的 typecode 为 6 位数字编码，编码规则：
#   - 前 2 位：大类（如 05=餐饮, 06=购物, 12=商务住宅, 15=交通）
#   - 中间 2 位：中类（如 0501=中餐厅, 0503=咖啡厅）
#   - 后 2 位：小类（如 050301=星巴克咖啡, 050302=COSTA咖啡）
# 映射后简化为本项目的粗分类，便于后续空间特征聚合
# ============================================================
AMAP_TYPECODE_MAP = {
    # ========== 办公类 ==========
    '120200': 'office',   # 写字楼
    '120201': 'office',   # 商务写字楼
    '120202': 'office',   # 工业写字楼
    '120203': 'office',   # 产业园

    # ========== 商业类 ==========
    '060100': 'mall',     # 商场
    '060101': 'mall',     # 购物中心
    '060102': 'mall',     # 百货商场
    '060400': 'mall',     # 商业街

    # ========== 交通类 ==========
    '150500': 'metro',    # 地铁站
    '150700': 'bus',      # 公交车站
    '150800': 'bus',      # 公交线路

    # ========== 教育类 ==========
    '141200': 'university', # 大学
    '141201': 'university', # 高等院校
    '141000': 'school',     # 学校
    '141100': 'school',     # 中学
    '141400': 'school',     # 小学

    # ========== 餐饮类 ==========
    '050000': 'restaurant', # 餐饮（综合）
    '050100': 'restaurant', # 中餐厅
    '050200': 'restaurant', # 外国餐厅
    '050300': 'cafe',      # 咖啡厅
    '050400': 'tea',       # 茶饮
    '050500': 'restaurant', # 快餐厅

    # ========== 生活服务类 ==========
    '060200': 'convenience', # 便利店
    '060300': 'supermarket', # 超市
    '070100': 'bank',        # 银行
    '090100': 'hospital',    # 医院

    # ========== 居住类 ==========
    '120300': 'residential', # 住宅小区
    '120301': 'residential', # 住宅区
    '120302': 'residential', # 别墅
    '120304': 'residential', # 社区中心
}

# ============================================================
# 重点分析城市列表（20 个城市，覆盖一线、新一线、二线）
# 每个城市包含：
#   - name:  中文名称（用于高德 API 搜索）
#   - adcode: 高德行政区划代码（6 位数字编码）
#            省/直辖市为 xxxx00，地级市为 xxxx00 或完整编码
# 选择依据：瑞幸咖啡门店密度最高的前 20 个城市
# 扩展说明：若需增加城市，需同步更新 city_tiers 表数据
# ============================================================
FOCUS_CITIES = [
    # === 一线城市（4 个）===
    {'name': '北京', 'adcode': '110000'},
    {'name': '上海', 'adcode': '310000'},
    {'name': '广州', 'adcode': '440100'},
    {'name': '深圳', 'adcode': '440300'},
    # === 新一线城市（10 个）===
    {'name': '成都', 'adcode': '510100'},
    {'name': '杭州', 'adcode': '330100'},
    {'name': '重庆', 'adcode': '500000'},
    {'name': '武汉', 'adcode': '420100'},
    {'name': '西安', 'adcode': '610100'},
    {'name': '苏州', 'adcode': '320500'},
    {'name': '南京', 'adcode': '320100'},
    {'name': '长沙', 'adcode': '430100'},
    {'name': '郑州', 'adcode': '410100'},
    {'name': '天津', 'adcode': '120000'},
    # === 二线城市（6 个）===
    {'name': '合肥', 'adcode': '340100'},
    {'name': '福州', 'adcode': '350100'},
    {'name': '厦门', 'adcode': '350200'},
    {'name': '昆明', 'adcode': '530100'},
    {'name': '沈阳', 'adcode': '210100'},
    {'name': '青岛', 'adcode': '370200'},
]

# ============================================================
# POI 搜索关键词（按类别组织）
# 用于高德 API 文本搜索（AMAP_POI_SEARCH_URL）的 keywords 参数
# 每类包含多个中文关键词以提升召回率
# 注意：某些大类别（如 restaurant）数据量极大，未单独搜索，
#       而是通过 AMAP_TYPECODE_MAP 中的细分类别间接获取
# ============================================================
POI_SEARCH_KEYWORDS = {
    'office': ['写字楼', '产业园', '商务中心'],
    'mall': ['购物中心', '商场', '百货'],
    'metro': ['地铁站'],
    'university': ['大学', '学院'],
    'residential': ['小区', '公寓'],
}

# ============================================================
# 数据库配置（PostgreSQL + PostGIS）
# 连接参数从 .env 环境变量加载，未设置则使用默认值
# 数据库要求：PostgreSQL 15+ + PostGIS 3.3+ 扩展
# 建库脚本：sql/schema.sql
# ============================================================
DB_CONFIG = {
    # 数据库主机地址（默认 localhost）
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    # 数据库端口（默认 PostgreSQL 标准端口 5432）
    'port': int(os.getenv('POSTGRES_PORT', 5432)),
    # 数据库名称（默认 luckin_spatial）
    'database': os.getenv('POSTGRES_DB', 'luckin_spatial'),
    # 数据库用户（默认 luckin）
    'user': os.getenv('POSTGRES_USER', 'luckin'),
    # 数据库密码（无默认值，必须由环境变量 POSTGRES_PASSWORD 提供）
    'password': os.getenv('POSTGRES_PASSWORD', ''),
}
