"""全局线程池管理器 - 集中管理应用中的所有线程池"""
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional
from utils.logger import logger


class ThreadPoolManager:
    """
    单例模式管理全局线程池

    设计原则：
    1. 按任务类型分组（llm_cpu, io_bound），避免资源竞争
    2. 懒加载：首次使用时创建
    3. 线程安全：双检锁确保单例
    4. 优雅关闭：FastAPI shutdown 时统一关闭
    """

    _instance: Optional['ThreadPoolManager'] = None
    _lock = threading.Lock()

    # 默认线程池配置
    DEFAULT_POOL_CONFIG = {
        "llm_cpu": {
            "max_workers": 500,  # LLM 调用和 CPU 密集型
            "description": "LLM calls and CPU-intensive tasks"
        },
        "io_bound": {
            "max_workers": 200,  # IO 密集型（OSS上传等）
            "description": "IO-bound tasks"
        }
    }

    def __new__(cls) -> 'ThreadPoolManager':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._pools: Dict[str, ThreadPoolExecutor] = {}
        self._pool_locks: Dict[str, threading.Lock] = {}
        self._initialized = True

    def get_pool(self, name: str, max_workers: Optional[int] = None) -> ThreadPoolExecutor:
        """
        获取指定名称的线程池，如果不存在则创建

        Args:
            name: 线程池名称（llm_cpu / io_bound）
            max_workers: 可选，覆盖默认配置

        Returns:
            ThreadPoolExecutor 实例
        """
        if name not in self._pools:
            # 每个池独立加锁，避免不同池之间的锁竞争
            if name not in self._pool_locks:
                self._pool_locks[name] = threading.Lock()

            with self._pool_locks[name]:
                if name not in self._pools:  # 双检锁
                    config = self.DEFAULT_POOL_CONFIG.get(name, {})
                    workers = max_workers or config.get("max_workers", 100)
                    desc = config.get("description", "General purpose")

                    self._pools[name] = ThreadPoolExecutor(
                        max_workers=workers,
                        thread_name_prefix=f"{name}_"
                    )
                    logger.info(f"[ThreadPoolManager] 创建线程池 '{name}': {workers} workers ({desc})")

        return self._pools[name]

    def shutdown_all(self, wait: bool = True):
        """优雅关闭所有线程池"""
        logger.info(f"[ThreadPoolManager] 关闭 {len(self._pools)} 个线程池...")
        for name, pool in self._pools.items():
            try:
                pool.shutdown(wait=wait)
                logger.info(f"[ThreadPoolManager] 线程池 '{name}' 已关闭")
            except Exception as e:
                logger.error(f"[ThreadPoolManager] 关闭线程池 '{name}' 失败: {e}")
        self._pools.clear()

    def get_stats(self) -> Dict:
        """获取线程池统计信息（用于监控）"""
        stats = {}
        for name, pool in self._pools.items():
            # ThreadPoolExecutor 不直接暴露活跃线程数，但可以通过 _threads 获取
            thread_count = len(pool._threads) if hasattr(pool, '_threads') else 'unknown'
            stats[name] = {
                "max_workers": pool._max_workers,
                "active_threads": thread_count
            }
        return stats


# 模块级单例实例（与现有模式一致，如 db_manager、langfuse_client）
thread_pool_manager = ThreadPoolManager()


def get_thread_pool(name: str = "llm_cpu", max_workers: Optional[int] = None) -> ThreadPoolExecutor:
    """
    便捷函数：获取线程池

    Usage:
        from infrastructure.thread_pool_manager import get_thread_pool

        pool = get_thread_pool("llm_cpu")
        future = pool.submit(my_function, arg1, arg2)
    """
    return thread_pool_manager.get_pool(name, max_workers)
