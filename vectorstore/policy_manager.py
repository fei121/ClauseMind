"""
Date: 2025-12-24 18:32:59
LastEditTime: 2026-01-05 16:39:45
Description: 保单向量库生命周期管理 - 仅负责缓存检查和下载，不再重复构建
"""
import os
import shutil
import hashlib
import threading
import json
from typing import List

from repositories.oss_repository import oss_prefix_exists, oss_download_dir, oss_list_subdirectories, oss_download_parsed_md_file
from config import OSS_BASE_PREFIX
from utils.logger import logger

# 本地缓存目录
LOCAL_CACHE_DIR = "./local_cache"

# 全局锁，防止同一保单同时操作
_build_locks = {}
_lock_guard = threading.Lock()


def get_lock(key: str):
    with _lock_guard:
        if key not in _build_locks:
            _build_locks[key] = threading.Lock()
        return _build_locks[key]


def is_valid_local_cache(path: str) -> bool:
    """检查本地缓存是否完整（公共函数）"""
    return (
        os.path.exists(os.path.join(path, "index.faiss")) and
        os.path.exists(os.path.join(path, "index.pkl"))
    )


class PolicyVectorStoreManager:
    """负责保单向量库的生命周期管理：缓存检查 -> 下载 -> 加载"""

    def __init__(self, base_cache_dir: str = LOCAL_CACHE_DIR):
        self.base_cache_dir = base_cache_dir

    def ensure_vectorstore(self, policy_id: str, matched_files: List[str]) -> str:
        """
        确保本地存在可用的向量库。
        流程：计算签名 / 查找最新缓存 -> 检查本地 -> 检查远程并下载 -> 返回本地路径

        注意：不再包含构建逻辑，向量库应由 pipeline.py 在文档处理时构建。
        如果本地和远程都没有，抛出异常。

        Args:
            policy_id: 保单号
            matched_files: 文件签名列表。如果为空，则自动查找最新缓存
        """
        logger.info(f"[PolicyManager] 开始确保向量库, policy_id={policy_id}, files_count={len(matched_files)}")

        # 1. 计算签名或查找最新缓存
        if matched_files:
            # 正常情况：基于文件签名计算
            sorted_files = sorted(matched_files)
            signature = hashlib.md5("|".join(sorted_files).encode("utf-8")).hexdigest()
            local_vs_path = os.path.join(self.base_cache_dir, policy_id, signature)
            remote_vs_prefix = f"{OSS_BASE_PREFIX}/vectorstores/{policy_id}/{signature}/"
            logger.info(f"[PolicyManager] 基于文件签名计算缓存路径, 签名={signature}")

            # 使用签名作为锁键
            lock_key = f"{policy_id}_{signature}"
        else:
            # 特殊情况：matched_files 为空，查找最新缓存
            logger.info(f"[PolicyManager] matched_files为空，将尝试查找最新缓存")

            # 尝试在本地或通过 OSS 查找最新缓存
            local_vs_path = self._find_latest_cache(policy_id)
            if local_vs_path:
                logger.info(f"[PolicyManager] 找到最新缓存: {local_vs_path}")
                return local_vs_path
            else:
                raise FileNotFoundError(
                    f"向量库不存在: policy_id={policy_id}. "
                    f"没有提供文件签名且未找到缓存，请先通过文档处理流程构建向量库。"
                )

        # 2. 并发控制（仅在 matched_files 不为空时）
        with get_lock(lock_key):
            # A. 检查本地缓存是否完整
            if is_valid_local_cache(local_vs_path):
                logger.info(f"[PolicyManager] 本地缓存命中: {local_vs_path}")
                return local_vs_path

            # B. 检查并下载远程缓存
            logger.info(f"[PolicyManager] 本地缓存未命中, 检查远程缓存: {remote_vs_prefix}")
            if oss_prefix_exists(remote_vs_prefix):
                logger.info(f"[PolicyManager] 远程缓存命中, 开始下载到 {local_vs_path}")

                # 清理可能的残余
                if os.path.exists(local_vs_path):
                    shutil.rmtree(local_vs_path)

                oss_download_dir(remote_vs_prefix, local_vs_path)

                if is_valid_local_cache(local_vs_path):
                    logger.info(f"[PolicyManager] 远程缓存下载成功: {local_vs_path}")
                    return local_vs_path
                else:
                    logger.warning("[PolicyManager] 下载的缓存无效")

            # C. 本地和远程都没有，抛出异常
            raise FileNotFoundError(
                f"向量库不存在: policy_id={policy_id}, signature={signature}. "
                f"请先通过文档处理流程构建向量库。"
            )

    def _find_latest_cache(self, policy_id: str) -> str | None:
        """
        查找指定 policy 的最新缓存（先检查本地，再检查 OSS）

        Args:
            policy_id: 保单号

        Returns:
            最新缓存目录的本地路径，如果不存在返回 None
        """
        # 1. 先检查本地是否有缓存
        local_cache = self._find_local_cache(policy_id)
        if local_cache:
            return local_cache

        # 2. 本地没有，检查并下载 OSS 上的最新缓存
        logger.info(f"[PolicyManager] 本地无缓存，检查 OSS 上的缓存: {policy_id}")
        oss_cache = self._find_and_download_oss_cache(policy_id)
        if oss_cache:
            return oss_cache

        return None

    def _get_cache_created_at(self, cache_path: str) -> str:
        """
        从缓存目录的 metadata.json 中读取 created_at 时间戳

        Args:
            cache_path: 缓存目录路径

        Returns:
            created_at 时间戳字符串，如果读取失败返回空字符串
        """
        try:
            metadata_path = os.path.join(cache_path, "metadata.json")
            if os.path.exists(metadata_path):
                with open(metadata_path, 'r') as f:
                    metadata = json.load(f)
                    return metadata.get("created_at", "")
            return ""
        except Exception as e:
            logger.debug(f"[PolicyManager] 无法读取缓存的 created_at: {cache_path}, error: {e}")
            return ""

    def _find_local_cache(self, policy_id: str) -> str:
        """查找本地最新的有效缓存目录（基于 metadata.json 中的 created_at）"""
        policy_dir = os.path.join(self.base_cache_dir, policy_id)
        if not os.path.exists(policy_dir):
            return None

        # 获取所有缓存目录及其 created_at
        cache_dirs = []
        for entry in os.listdir(policy_dir):
            entry_path = os.path.join(policy_dir, entry)
            if os.path.isdir(entry_path) and is_valid_local_cache(entry_path):
                created_at = self._get_cache_created_at(entry_path)
                if created_at:
                    cache_dirs.append((entry_path, created_at))

        if not cache_dirs:
            # 如果没有找到有效的 metadata.json，退回到按文件创建时间
            logger.warning(f"[PolicyManager] 未找到有效的 metadata.json，使用文件创建时间作为回退")
            fallback_dirs = []
            for entry in os.listdir(policy_dir):
                entry_path = os.path.join(policy_dir, entry)
                if os.path.isdir(entry_path) and is_valid_local_cache(entry_path):
                    fallback_dirs.append((entry_path, os.path.getctime(entry_path)))

            if not fallback_dirs:
                return None

            fallback_dirs.sort(key=lambda x: x[1], reverse=True)
            latest_path = fallback_dirs[0][0]
            logger.info(f"[PolicyManager] 找到本地最新缓存（回退模式）: {latest_path}")
            return latest_path

        # 按 created_at 降序排序
        cache_dirs.sort(key=lambda x: x[1], reverse=True)
        latest_path = cache_dirs[0][0]

        logger.info(f"[PolicyManager] 找到本地最新缓存: {latest_path} (created_at: {cache_dirs[0][1]})")
        return latest_path

    def _find_and_download_oss_cache(self, policy_id: str) -> str:
        """查找并下载 OSS 上的最新缓存（基于 metadata.json 中的 created_at）"""
        # 构造 OSS 前缀路径
        oss_policy_prefix = f"{OSS_BASE_PREFIX}/vectorstores/{policy_id}/"

        try:
            # 1. 列出该 policy 下的所有缓存签名目录
            signatures = oss_list_subdirectories(oss_policy_prefix)
            if not signatures:
                logger.info(f"[PolicyManager] OSS上未找到缓存: {oss_policy_prefix}")
                return None

            logger.info(f"[PolicyManager] 开始查找最新缓存，共 {len(signatures)} 个候选")

            # 2. 下载每个缓存的 metadata.json 并解析 created_at
            candidate_caches = []  # (signature, local_path, created_at)
            temp_dir = os.path.join(self.base_cache_dir, policy_id, "temp_metadata")

            for signature in signatures:
                remote_metadata_path = f"{oss_policy_prefix}{signature}/metadata.json"

                # 检查 metadata.json 是否存在
                if not oss_prefix_exists(remote_metadata_path):
                    continue

                # 尝试下载 metadata.json
                metadata_local = os.path.join(temp_dir, f"{signature}_metadata.json")
                os.makedirs(os.path.dirname(metadata_local), exist_ok=True)

                try:
                    # 下载 metadata.json
                    oss_download_parsed_md_file(
                        f"{signature}/metadata.json",
                        metadata_local
                    )

                    # 使用 _get_cache_created_at 读取 created_at
                    if os.path.exists(metadata_local):
                        created_at = self._get_cache_created_at(temp_dir)
                        if created_at:
                            candidate_caches.append((signature, created_at))
                            logger.debug(f"[PolicyManager] 找到缓存: signature={signature}, created_at={created_at}")
                except Exception as e:
                    logger.debug(f"[PolicyManager] 无法获取缓存 {signature} 的 metadata: {e}")

            # 3. 清理临时目录
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)

            # 4. 按 created_at 降序排序
            if not candidate_caches:
                logger.warning(f"[PolicyManager] 未找到任何有效的 metadata.json")
                return None

            # 按 created_at 降序排序
            candidate_caches.sort(key=lambda x: x[1], reverse=True)
            latest_signature = candidate_caches[0][0]
            latest_created_at = candidate_caches[0][1]

            logger.info(f"[PolicyManager] 找到最新缓存: signature={latest_signature}, created_at={latest_created_at}")

            # 5. 下载最新缓存
            remote_prefix = f"{oss_policy_prefix}{latest_signature}/"
            local_path = os.path.join(self.base_cache_dir, policy_id, latest_signature)

            # 如果本地已存在且有效，直接返回
            if is_valid_local_cache(local_path):
                logger.info(f"[PolicyManager] 最新缓存本地已存在: {local_path}")
                return local_path

            # 下载缓存
            logger.info(f"[PolicyManager] 开始下载最新缓存: {remote_prefix} -> {local_path}")
            os.makedirs(os.path.dirname(local_path), exist_ok=True)

            # 确保目录不存在（避免残余文件）
            if os.path.exists(local_path):
                shutil.rmtree(local_path)

            oss_download_dir(remote_prefix, local_path)

            # 验证下载的缓存
            if is_valid_local_cache(local_path):
                logger.info(f"[PolicyManager] 最新缓存下载成功: {local_path}")
                return local_path
            else:
                logger.error(f"[PolicyManager] 最新缓存无效: {latest_signature}")
                if os.path.exists(local_path):
                    shutil.rmtree(local_path)
                return None

        except Exception as e:
            logger.error(f"[PolicyManager] 查找并下载OSS缓存失败: {e}", exc_info=True)
            return None
