"""检索结果缓存管理器
Author: AI Assistant
Date: 2026-02-24
Description: 线程安全的检索结果内存缓存，用于减少重复的混合检索调用
"""
import threading
import time
from typing import Dict, Tuple, List, Any, Optional
from dataclasses import dataclass
from config import SEARCH_RESULT_CACHE_TTL, SEARCH_RESULT_CACHE_MAX_SIZE
from utils.logger import logger


@dataclass
class CacheEntry:
    """缓存条目"""
    results: List[Dict[str, Any]]
    timestamp: float
    hit_count: int = 0


class SearchResultCache:
    """
    检索结果内存缓存

    设计原则：
    1. 线程安全：使用双检锁模式
    2. TTL 过期：自动清理过期条目
    3. LRU 淘汰：达到最大容量时清理最少使用条目
    4. 统计监控：支持命中率统计
    """

    def __init__(self, ttl: int = SEARCH_RESULT_CACHE_TTL, max_size: int = SEARCH_RESULT_CACHE_MAX_SIZE):
        self._cache: Dict[Tuple[str, str, str], CacheEntry] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._max_size = max_size
        self._stats = {"hits": 0, "misses": 0}

    def _make_key(self, local_vs_path: str, query: str, query_type: str) -> Tuple[str, str, str]:
        """生成缓存键"""
        return (local_vs_path, query, query_type)

    def get(self, local_vs_path: str, query: str, query_type: str) -> Optional[List[Dict[str, Any]]]:
        """获取缓存结果"""
        key = self._make_key(local_vs_path, query, query_type)

        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._stats["misses"] += 1
                return None

            # 检查是否过期
            if time.time() - entry.timestamp > self._ttl:
                del self._cache[key]
                self._stats["misses"] += 1
                return None

            # 更新命中统计
            entry.hit_count += 1
            self._stats["hits"] += 1
            return entry.results

    def put(self, local_vs_path: str, query: str, query_type: str, results: List[Dict[str, Any]]):
        """存入缓存"""
        key = self._make_key(local_vs_path, query, query_type)

        with self._lock:
            # 检查容量，执行 LRU 淘汰
            if len(self._cache) >= self._max_size:
                self._evict_lru()

            self._cache[key] = CacheEntry(
                results=results,
                timestamp=time.time()
            )

    def _evict_lru(self):
        """LRU 淘汰策略"""
        if not self._cache:
            return

        # 找出命中次数最少的条目
        min_key = min(self._cache.keys(), key=lambda k: self._cache[k].hit_count)
        del self._cache[min_key]

    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache.clear()
            self._stats = {"hits": 0, "misses": 0}

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = self._stats["hits"] / total if total > 0 else 0
            return {
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "hit_rate": f"{hit_rate:.2%}",
                "size": len(self._cache),
                "max_size": self._max_size,
                "ttl": self._ttl
            }


# 全局缓存实例
_search_result_cache: Optional[SearchResultCache] = None
_search_cache_lock = threading.Lock()


def get_search_cache() -> SearchResultCache:
    """获取全局缓存实例（惰性初始化）"""
    global _search_result_cache
    if _search_result_cache is None:
        with _search_cache_lock:
            if _search_result_cache is None:
                _search_result_cache = SearchResultCache()
    return _search_result_cache


def clear_search_cache():
    """清空全局缓存"""
    cache = get_search_cache()
    cache.clear()
    logger.info("[SearchCache] 缓存已清空")
