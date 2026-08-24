import os
from typing import List, Dict
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
load_dotenv()
load_dotenv('.env.local', override=True)

# 1. 预先计算环境前缀，后面直接用，省去重复代码
DEPLOY_ENV = os.getenv('DEPLOY_ENV', 'test').lower()
PREFIX = f"APP_{DEPLOY_ENV.upper()}"  # 结果如: APP_TEST 或 APP_PRD


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    # ==========================
    # 基础配置
    # ==========================
    DEPLOY_ENV: str = DEPLOY_ENV

    # 直接根据环境判断 OSS 前缀
    OSS_BASE_PREFIX: str = (
        'DemoAssets/clausemind-demo' if DEPLOY_ENV == 'prd'
        else 'DemoAssets/clausemind-demo-test'
    )

    # ==========================
    # 业务接口 (直接拼装环境变量名)
    # ==========================
    TAG_URL: str = os.getenv(f'{PREFIX}_TAG_URL', "")
    GENERAL_TAG_URL: str = os.getenv(f'{PREFIX}_GENERAL_TAG_URL', "")

    GATEWAY_URL: str = os.getenv(f'{PREFIX}_GATEWAY_URL', "")
    GATEWAY_KEY: str = os.getenv(f'{PREFIX}_GATEWAY_KEY', "")
    GATEWAY_CHANNEL: str = os.getenv(f'{PREFIX}_GATEWAY_CHANNEL', "")

    # 蒸馏网关 (复用上面的逻辑)
    DISTILL_GATEWAY_URL: str = GATEWAY_URL
    DISTILL_GATEWAY_KEY: str = GATEWAY_KEY
    DISTILL_GATEWAY_CHANNEL: str = GATEWAY_CHANNEL

    GATEWAY_EMB_URL: str = os.getenv(f'{PREFIX}_GATEWAY_EMB_URL', "")

    # OpenAI-compatible gateway (LiteLLM, OpenAI, or compatible proxies)
    OPENAI_API_BASE: str = os.getenv('OPENAI_API_BASE', '').rstrip('/')
    OPENAI_API_KEY: str = os.getenv('OPENAI_API_KEY', '')
    OPENAI_CHAT_MODEL: str = os.getenv('OPENAI_CHAT_MODEL', 'openai/deepseek-v4-flash')
    OPENAI_EMBEDDING_MODEL: str = os.getenv('OPENAI_EMBEDDING_MODEL', 'text-embedding-v4')

    # ==========================
    # Optional local persistence databases
    # ==========================
    APP_DB: str = os.getenv(f'{PREFIX}_DB_NAME', "")
    APP_HOST: str = os.getenv(f'{PREFIX}_DB_HOST', "")
    APP_PORT: str = os.getenv(f'{PREFIX}_DB_PORT', "")
    APP_USER: str = os.getenv(f'{PREFIX}_DB_USER', "")
    APP_PASSWORD: str = os.getenv(f'{PREFIX}_DB_PASSWORD', "")

    KB_DB: str = os.getenv(f'{PREFIX}_KB_DB', "")
    KB_HOST: str = os.getenv(f'{PREFIX}_KB_HOST', "")
    KB_PORT: str = os.getenv(f'{PREFIX}_KB_PORT', "")
    KB_USER: str = os.getenv(f'{PREFIX}_KB_USER', "")
    KB_PASSWORD: str = os.getenv(f'{PREFIX}_KB_PASSWORD', "")

    # ==========================
    # Redis 配置 (支持回退逻辑)
    # ==========================
    # 逻辑：优先取 APP_TEST_REDIS_HOST，取不到则取 APP_REDIS_HOST
    REDIS_HOST: str = os.getenv(f'{PREFIX}_REDIS_HOST', '')
    REDIS_PORT: int = int(os.getenv(f'{PREFIX}_REDIS_PORT', '6379'))
    REDIS_USER: str = os.getenv(f'{PREFIX}_REDIS_USER', '')
    REDIS_PASSWORD: str = os.getenv(f'{PREFIX}_REDIS_PASSWORD', '')
    REDIS_DB: int = int(os.getenv(f'{PREFIX}_REDIS_DB', '0'))

    # ==========================
    # 静态/逻辑配置
    # ==========================
    APP_CALLBACK_URL: str = os.getenv(f'{PREFIX}_CALLBACK_URL', "")
    APP_DECONSTRUCTION_CALLBACK_URL: str = os.getenv(f'{PREFIX}_DECONSTRUCTION_CALLBACK_URL', "")
    @property
    def should_save_to_database(self) -> bool:
        explicit_setting = os.getenv('SAVE_TO_DATABASE')
        if explicit_setting is not None:
            return explicit_setting.lower() in {'1', 'true', 'yes', 'on'}
        local_demo_mode = os.getenv('LOCAL_DEMO_MODE', 'false').lower() == 'true'
        return self.DEPLOY_ENV == "test" and not local_demo_mode

    # OSS Key
    OSS_REGION: str = os.getenv('APP_OSS_REGION', '')
    OSS_ACCESS_KEY_ID: str = os.getenv('APP_OSS_ACCESS_KEY_ID', '')
    OSS_ACCESS_KEY_SECRET: str = os.getenv('APP_OSS_ACCESS_KEY_SECRET', '')
    OSS_BUCKET_NAME: str = os.getenv('APP_OSS_BUCKET_NAME', '')
    OSS_ENDPOINT: str = os.getenv('APP_OSS_ENDPOINT', '')

    # 敏感标签列表
    SENSITIVE_TAGS: List[str] = [
        "先天性疾病", "染色体异常", "遗传性疾病", "毒品", "艾滋病",
        "精神性疾病", "性功能障碍治疗", "包皮过长、包茎和包茎嵌顿",
        "怀孕（含宫外孕）", "精神病鉴定", "狭义性病", "广义精神病",
        "眼部疾病及视力障碍", "体检"
    ]

    # 映射字典
    KBTYPE_ID2NAME_MAPPING: Dict[str, str] = {
        "1": "药品非责标签", "2": "诊疗非责标签", "3": "材料非责标签",
        "4": "诊断非责标签", "5": "手术非责标签"
    }

    # 限流配置（与网关 RPM 1000 匹配）
    MAX_CONCURRENT_REQUESTS: int = 200  # 从 200 降低到 50，避免线程爆炸
    MAX_CALLS_PER_SECOND: int = 200  # 1000/10，保守估算
    MAX_CALLS_PER_MINUTE: int = 2000  # 网关实际 RPM

    # 线程池配置
    THREAD_POOL_LLM_MAX_WORKERS: int = int(os.getenv("THREAD_POOL_LLM_MAX_WORKERS", "500"))
    THREAD_POOL_IO_MAX_WORKERS: int = int(os.getenv("THREAD_POOL_IO_MAX_WORKERS", "200"))

    # Cache配置
    EMBEDDING_CACHE_MAX_SIZE: int = int(os.getenv("EMBEDDING_CACHE_MAX_SIZE", "10000"))

    # 检索结果缓存配置
    SEARCH_RESULT_CACHE_TTL: int = int(os.getenv("SEARCH_RESULT_CACHE_TTL", "300"))  # 5分钟
    SEARCH_RESULT_CACHE_MAX_SIZE: int = int(os.getenv("SEARCH_RESULT_CACHE_MAX_SIZE", "1000"))  # 最大条目数

    # BM25检索器配置
    BM25_K1: float = float(os.getenv("BM25_K1", "1.5"))  # 词项饱和度参数
    BM25_B: float = float(os.getenv("BM25_B", "0.75"))   # 长度归一化参数

    # 混合检索配置 (Hybrid Search)
    RRF_K: int = int(os.getenv("RRF_K", "60"))  # RRF常数，默认60

    # VectorStore配置
    # 长文本摘要阈值（超过此长度的文本会进行LLM摘要）
    LONG_TEXT_SUMMARY_THRESHOLD: int = int(os.getenv("LONG_TEXT_SUMMARY_THRESHOLD", "8000"))

    # 并行LLM摘要的最大worker数
    MAX_SUMMARY_WORKERS: int = int(os.getenv("MAX_SUMMARY_WORKERS", "5"))

    # 最小摘要长度（低于此长度会记录警告）
    MIN_SUMMARY_LENGTH: int = int(os.getenv("MIN_SUMMARY_LENGTH", "100"))

    # 批处理大小（用于向量化）
    EMBEDDING_BATCH_SIZE: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "100"))

    # 缓存文件最小大小（字节）
    CACHE_FILE_MIN_SIZE: int = int(os.getenv("CACHE_FILE_MIN_SIZE", "100"))

    # Embedding模型版本（用于缓存兼容性检查）
    EMBEDDING_VERSION: str = os.getenv("EMBEDDING_VERSION", "v4")


# 初始化
settings = Settings()

# ==========================
# 模块级变量导出（供其他模块直接导入）
# ==========================
# OSS 配置
OSS_REGION = settings.OSS_REGION
OSS_ACCESS_KEY_ID = settings.OSS_ACCESS_KEY_ID
OSS_ACCESS_KEY_SECRET = settings.OSS_ACCESS_KEY_SECRET
OSS_BUCKET_NAME = settings.OSS_BUCKET_NAME
OSS_ENDPOINT = settings.OSS_ENDPOINT
OSS_BASE_PREFIX = settings.OSS_BASE_PREFIX

# 数据库配置
APP_DB = settings.APP_DB
APP_HOST = settings.APP_HOST
APP_PORT = settings.APP_PORT
APP_USER = settings.APP_USER
APP_PASSWORD = settings.APP_PASSWORD

KB_DB = settings.KB_DB
KB_HOST = settings.KB_HOST
KB_PORT = settings.KB_PORT
KB_USER = settings.KB_USER
KB_PASSWORD = settings.KB_PASSWORD

# Redis 配置
REDIS_HOST = settings.REDIS_HOST
REDIS_PORT = settings.REDIS_PORT
REDIS_USER = settings.REDIS_USER
REDIS_PASSWORD = settings.REDIS_PASSWORD
REDIS_DB = settings.REDIS_DB

# 业务接口配置
TAG_URL = settings.TAG_URL
GENERAL_TAG_URL = settings.GENERAL_TAG_URL
GATEWAY_URL = settings.GATEWAY_URL
GATEWAY_KEY = settings.GATEWAY_KEY
GATEWAY_CHANNEL = settings.GATEWAY_CHANNEL
DISTILL_GATEWAY_URL = settings.DISTILL_GATEWAY_URL
DISTILL_GATEWAY_KEY = settings.DISTILL_GATEWAY_KEY
DISTILL_GATEWAY_CHANNEL = settings.DISTILL_GATEWAY_CHANNEL
GATEWAY_EMB_URL = settings.GATEWAY_EMB_URL
OPENAI_API_BASE = settings.OPENAI_API_BASE
OPENAI_API_KEY = settings.OPENAI_API_KEY
OPENAI_CHAT_MODEL = settings.OPENAI_CHAT_MODEL
OPENAI_EMBEDDING_MODEL = settings.OPENAI_EMBEDDING_MODEL
APP_CALLBACK_URL = settings.APP_CALLBACK_URL

# 其他配置
KBTYPE_ID2NAME_MAPPING = settings.KBTYPE_ID2NAME_MAPPING
SENSITIVE_TAGS = settings.SENSITIVE_TAGS
MAX_CONCURRENT_REQUESTS = settings.MAX_CONCURRENT_REQUESTS
MAX_CALLS_PER_SECOND = settings.MAX_CALLS_PER_SECOND
MAX_CALLS_PER_MINUTE = settings.MAX_CALLS_PER_MINUTE

# Cache配置
EMBEDDING_CACHE_MAX_SIZE = settings.EMBEDDING_CACHE_MAX_SIZE
SEARCH_RESULT_CACHE_TTL = settings.SEARCH_RESULT_CACHE_TTL
SEARCH_RESULT_CACHE_MAX_SIZE = settings.SEARCH_RESULT_CACHE_MAX_SIZE

# BM25检索器配置
BM25_K1 = settings.BM25_K1
BM25_B = settings.BM25_B

# 混合检索配置 (Hybrid Search)
RRF_K = settings.RRF_K

# VectorStore配置
LONG_TEXT_SUMMARY_THRESHOLD = settings.LONG_TEXT_SUMMARY_THRESHOLD
MAX_SUMMARY_WORKERS = settings.MAX_SUMMARY_WORKERS
MIN_SUMMARY_LENGTH = settings.MIN_SUMMARY_LENGTH
EMBEDDING_BATCH_SIZE = settings.EMBEDDING_BATCH_SIZE
CACHE_FILE_MIN_SIZE = settings.CACHE_FILE_MIN_SIZE
EMBEDDING_VERSION = settings.EMBEDDING_VERSION

# Optional integration mode; the public demo uses the generic default.
APP_ENV = os.getenv('APP_ENV', 'demo')

# Default LLM model
LLM_DEFAULT = os.getenv('LLM_DEFAULT', settings.OPENAI_CHAT_MODEL if settings.OPENAI_API_BASE else 'kimi-k2.5')

# ==========================
# 日志配置
# ==========================
TRACING_METHOD = os.getenv('TRACING_METHOD', 'none').lower()
