"""
数据库连接管理模块

本模块在系统架构中位于数据访问层，负责：
1. 定义数据库连接配置（通过 pydantic-settings 从环境变量/.env 文件加载）
2. 提供异步数据库连接的依赖注入函数 get_db()
3. 提供 database_url 解析工具函数，将连接字符串拆分为 asyncpg 所需的连接参数

技术选型：
- asyncpg: 高性能异步 PostgreSQL 驱动，支持连接池、预编译语句
- PostGIS: 空间数据库扩展，用于存储与查询门店/POI 的地理位置数据
- pydantic-settings: 类型安全的配置管理，支持 .env 文件和环境变量
"""
import os
import asyncpg
from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    应用配置类 — 所有配置项均可通过环境变量或 .env 文件覆盖

    使用方式：
        settings = Settings()
        settings.database_url  # 读取 DATABASE_URL 环境变量

    安全说明：
        本类不提供任何带真实密码的默认值。数据库口令必须由环境变量
        或 .env 文件提供（.env 已在 .gitignore 中，不会进入版本库）。
    """
    model_config = {
        "extra": "ignore",                # 忽略未定义的环境变量，避免意外注入
        "env_file": ".env",               # 自动加载项目根目录的 .env 文件
        "env_file_encoding": "utf-8",
    }

    # ---- 数据库连接 ----
    # 完整连接 URL（SQLAlchemy/asyncpg 格式）。
    # 若未显式设置 DATABASE_URL，则由下方的 POSTGRES_* 参数拼接而成
    # （见 _build_default_url）。容器部署时通常直接注入 DATABASE_URL。
    database_url: str = ''

    # ---- 高德地图 API ----
    amap_api_key: str = ''

    # ---- 数据库连接参数（用于拼连接串，或 parse_db_url 拆分） ----
    postgres_host: str = 'localhost'
    postgres_port: int = 5432
    postgres_db: str = 'luckin_spatial'
    postgres_user: str = 'luckin'
    postgres_password: str = ''       # 无默认值，必须由环境变量/.env 提供

    @model_validator(mode='after')
    def _build_default_url(self):
        """未显式提供 DATABASE_URL 时，用 POSTGRES_* 参数拼出连接串。

        这样既保留了 DATABASE_URL 的环境变量覆盖能力（容器部署使用），
        又避免在源码中硬编码任何数据库口令。
        """
        if not self.database_url:
            self.database_url = (
                f'postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}'
                f'@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}'
            )
        return self


# 全局单例配置对象
settings = Settings()


async def get_db():
    """
    数据库连接依赖注入函数

    使用 asyncpg 创建数据库连接，通过 FastAPI 依赖注入机制自动管理连接生命周期。
    典型用法：
        @app.get('/items')
        async def get_items(conn = Depends(get_db)):
            ...

    Yields:
        asyncpg.Connection: 一个到 PostgreSQL 的活跃连接

    注意：
        - 连接在请求处理完成后通过 finally 块自动关闭
        - 每次请求创建一个新连接（非连接池模式），适合低并发场景
        - 生产环境建议配合 asyncpg 连接池使用以提高性能
    """
    conn = await asyncpg.connect(settings.database_url)
    try:
        yield conn
    finally:
        await conn.close()


def parse_db_url(url: str) -> dict:
    """
    解析 PostgreSQL 连接 URL 为 asyncpg.connect() 所需的参数字典

    asyncpg.connect() 不接受完整 URL，只接受按关键字传递的连接参数。
    本函数将形如 "postgresql://user:pass@host:port/dbname" 的 URL
    拆分为 {'user', 'password', 'host', 'port', 'database'}。

    Args:
        url: PostgreSQL 连接 URL，支持以下前缀：
             - postgresql://
             - postgresql+asyncpg://

    Returns:
        dict: 包含 user, password, host, port (int), database 键的字典

    Raises:
        ValueError: 如果 URL 格式不符合预期（缺少必要字段）

    示例:
        >>> parse_db_url('postgresql://luckin:your_db_password_here@localhost:5432/luckin_spatial')
        {'user': 'luckin', 'password': 'your_db_password_here', 'host': 'localhost',
         'port': 5432, 'database': 'luckin_spatial'}
    """
    # 移除协议前缀（兼容 SQLAlchemy 和 asyncpg 两种格式）
    # 格式: postgresql://user:pass@host:port/dbname
    # 或:   postgresql+asyncpg://user:pass@host:port/dbname
    url = url.replace('postgresql://', '').replace('postgresql+asyncpg://', '')

    # 拆分认证+主机部分 与 数据库名部分
    # url 此时格式: user:pass@host:port/dbname
    auth_host, dbname = url.split('/')

    # 拆分认证信息与主机端口信息
    auth, host_port = auth_host.split('@')

    # 拆分用户名与密码
    user, password = auth.split(':')

    # 拆分主机与端口（端口可能缺失，默认 5432）
    host, port = host_port.split(':') if ':' in host_port else (host_port, '5432')

    return {
        'user': user,
        'password': password,
        'host': host,
        'port': int(port),
        'database': dbname,
    }
