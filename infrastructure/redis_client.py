# python
"""
Date: 2025-10-13 17:33:31
LastEditTime: 2026-01-20 16:36:13
Description: Redis 客户端（兼容仅密码与 ACL 用户两种认证）
"""
# Third-party imports
import redis

# Local imports
import config
from utils import logger

# 从环境变量获取 Redis 连接信息；未配置时用户名/密码留空，避免误传占位值
# REDIS_HOST = os.environ.get("APP_PRD_REDIS_HOST", "")
# REDIS_PORT = int(os.environ.get("APP_PRD_REDIS_PORT", 6379))
# REDIS_DB = int(os.environ.get("APP_PRD_REDIS_DB", 21))
# REDIS_USER = (os.environ.get("APP_PRD_REDIS_USER", "") or "").strip()
# REDIS_PASSWORD = os.environ.get("APP_PRD_REDIS_PASSWORD", "")

REDIS_HOST = config.REDIS_HOST
REDIS_PORT = config.REDIS_PORT
REDIS_DB = config.REDIS_DB
REDIS_USER = (config.REDIS_USER or "").strip()
REDIS_PASSWORD = config.REDIS_PASSWORD

# 仅在显式需要时传入用户名；默认/空/`default` 不传，兼容仅密码模式
_pool_kwargs = dict(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True,
    max_connections=50,
    socket_keepalive=True,
    socket_timeout=5,
    health_check_interval=30,
)
if REDIS_PASSWORD:
    _pool_kwargs["password"] = REDIS_PASSWORD
if REDIS_USER and REDIS_USER.lower() != "default":
    _pool_kwargs["username"] = REDIS_USER

redis_connection_pool = redis.ConnectionPool(**_pool_kwargs)

_client = None


def get_redis_client() -> redis.Redis:
    """返回共享连接池的 Redis 客户端，并在首次获取时进行 PING 自检。"""
    global _client
    if _client:
        return _client
    try:
        client = redis.Redis(connection_pool=redis_connection_pool)
        client.ping()  # 自检认证/连通性
        logger.info(
            "Redis connected: {}:{}/{} (user={})",
            REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_USER or "<none>",
        )
        _client = client
        return _client
    except redis.exceptions.AuthenticationError as e:
        logger.error("Redis auth failed: {}", e)
        raise RuntimeError(
            "Redis 认证失败。若 Redis 仅配置密码，请将环境变量 `REDIS_USER` 留空或设为 `default`；"
            "若使用 ACL 用户，请确认 `REDIS_USER/REDIS_PASSWORD` 正确。"
        ) from e
    except redis.ResponseError as e:
        if "WRONGPASS" in str(e):
            logger.error("Redis wrong password/username: {}", e)
            raise RuntimeError("Redis 认证失败（WRONGPASS）。检查 `REDIS_USER/REDIS_PASSWORD`。") from e
        raise
    except Exception as e:
        logger.error("Redis connection error: {}", e)
        raise

