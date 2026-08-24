"""
HTTP Session管理模块
提供共享的requests Session，配置适当的连接池以避免连接池耗尽问题
"""
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class SessionManager:
    """管理共享的HTTP Session实例"""

    _session = None
    _oss_session = None

    @classmethod
    def get_session(cls) -> requests.Session:
        """
        获取通用的HTTP Session

        Returns:
            配置好的requests.Session实例
        """
        if cls._session is None:
            cls._session = cls._create_session(
                pool_connections=20,
                pool_maxsize=50,
                max_retries=3
            )
        return cls._session

    @classmethod
    def get_oss_session(cls) -> requests.Session:
        """
        获取专门用于OSS访问的HTTP Session
        配置更大的连接池以支持并发OSS操作

        Returns:
            配置好的requests.Session实例
        """
        if cls._oss_session is None:
            cls._oss_session = cls._create_session(
                pool_connections=50,
                pool_maxsize=100,
                max_retries=3
            )
        return cls._oss_session

    @classmethod
    def _create_session(
        cls,
        pool_connections: int = 20,
        pool_maxsize: int = 50,
        max_retries: int = 3
    ) -> requests.Session:
        """
        创建并配置一个新的Session

        Args:
            pool_connections: 缓存的连接池数量
            pool_maxsize: 连接池中最大连接数
            max_retries: 最大重试次数

        Returns:
            配置好的requests.Session实例
        """
        session = requests.Session()

        # 配置重试策略
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"]
        )

        # 创建适配器并配置连接池
        adapter = HTTPAdapter(
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
            max_retries=retry_strategy
        )

        # 为http和https协议安装适配器
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session

    @classmethod
    def close_all(cls):
        """关闭所有Session"""
        if cls._session is not None:
            cls._session.close()
            cls._session = None
        if cls._oss_session is not None:
            cls._oss_session.close()
            cls._oss_session = None


# 便捷函数
def get_session() -> requests.Session:
    """获取通用HTTP Session"""
    return SessionManager.get_session()


def get_oss_session() -> requests.Session:
    """获取OSS专用HTTP Session"""
    return SessionManager.get_oss_session()
"""
Date: 2025-12-12 13:57:56
LastEditTime: 2025-12-12 13:58:09
Description: 这是默认设置,可以在设置》工具》File Description中进行配置
"""
