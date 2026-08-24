"""
Date: 2025-12-29 10:49:59
LastEditTime: 2026-01-20 16:36:15
Description: 向量库构建模块 - 直接从 chunks 构建，避免重复处理
"""
import json
import os
import pickle
from typing import List, Dict, Any

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from repositories.oss_repository import oss_upload_dir
from repositories.remote_embedding_model import RemoteEmbeddings
from utils import logger

# Embedding 模型最大 token 限制为 8192，中文约 1.5-2 字符/token
# 设置安全阈值为 6000 字符（约 3000-4000 tokens），留出足够余量
MAX_CHUNK_CHARS = 6000


def _split_oversized_chunks(chunk_records: List[Dict[str, Any]], max_chars: int = MAX_CHUNK_CHARS) -> List[Dict[str, Any]]:
    """
    遍历所有 chunks，检测超长内容
    使用 RecursiveCharacterTextSplitter 按 \n\n → \n → 。 → ； → ， 顺序递归分割
    保留 200 字符重叠以保持上下文连贯
    子 chunk 继承原始 metadata，并添加标记：
        is_split_chunk: True
        split_part: 1, 2, 3...
        split_total: 总子块数
        original_chunk_index: 原始索引
    日志记录被分割的 chunks 信息
    处理超长 chunks，将超过长度限制的 chunk 递归分割成多个子 chunk

    Args:
        chunk_records: 原始 chunks 列表
        max_chars: 单个 chunk 的最大字符数

    Returns:
        处理后的 chunks 列表（超长的已被分割）
    """
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_chars,
        chunk_overlap=200,  # 保留一定重叠以保持上下文连贯
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
        length_function=len,
    )

    processed_chunks = []
    oversized_count = 0

    for record in chunk_records:
        content = record.get("page_content", "")
        content_len = len(content)

        if content_len <= max_chars:
            # 正常长度，直接保留
            processed_chunks.append(record)
        else:
            # 超长 chunk，需要分割
            oversized_count += 1
            original_metadata = record.get("metadata", {}).copy()
            original_index = record.get("index", 0)

            logger.warning(
                f"[VectorstoreBuilder] Chunk #{original_index} 超长 ({content_len} chars > {max_chars})，"
                f"路径: {original_metadata.get('structure_path', 'N/A')}，进行分割处理"
            )

            # 使用 RecursiveCharacterTextSplitter 分割
            sub_texts = text_splitter.split_text(content)

            for sub_idx, sub_text in enumerate(sub_texts):
                sub_metadata = original_metadata.copy()
                sub_metadata["is_split_chunk"] = True
                sub_metadata["split_part"] = sub_idx + 1
                sub_metadata["split_total"] = len(sub_texts)
                sub_metadata["original_chunk_index"] = original_index

                processed_chunks.append({
                    "index": f"{original_index}_{sub_idx}",
                    "page_content": sub_text,
                    "metadata": sub_metadata
                })

            logger.info(f"[VectorstoreBuilder] Chunk #{original_index} 已分割为 {len(sub_texts)} 个子块")

    if oversized_count > 0:
        logger.info(
            f"[VectorstoreBuilder] 超长 chunk 处理完成: {oversized_count} 个超长块 -> "
            f"总计 {len(processed_chunks)} 个块 (原始 {len(chunk_records)} 个)"
        )

    return processed_chunks


def build_vectorstore_from_chunks(
    policy_no: str,
    chunk_records: List[Dict[str, Any]],
    local_vs_path,
    remote_vs_prefix,
    upload_to_oss: bool = True
):
    """
    直接从已解析的 chunks 构建向量库

    Args:
        policy_no: 保单号
        chunk_records: markdown_parser 返回的 chunks 列表
        upload_to_oss: 是否上传到 OSS
    """

    if not chunk_records:
        raise ValueError(f"Policy {policy_no} has no chunks to build vectorstore.")

    logger.info(f"[VectorstoreBuilder] 开始构建向量库, policy_no={policy_no}, chunks数量={len(chunk_records)}")

    # # 2. 检查本地缓存
    # if is_valid_local_cache(local_vs_path):
    #     logger.info(f"[VectorstoreBuilder] 本地缓存已存在: {local_vs_path}")
    #     return local_vs_path, remote_vs_prefix

    # 2.5 处理超长 chunks，防止 embedding 模型报错
    chunk_records = _split_oversized_chunks(chunk_records)

    # 3. 转换为 LangChain Document 对象
    docs = []
    for record in chunk_records:
        doc = Document(
            page_content=record["page_content"],
            metadata=record.get("metadata", {})
        )
        docs.append(doc)

    # 4. 向量化
    logger.info(f"[VectorstoreBuilder] 开始向量化, chunks数量={len(docs)}")
    embeddings = RemoteEmbeddings()
    vectorstore = FAISS.from_documents(docs, embeddings)
    logger.info("[VectorstoreBuilder] 向量化完成")

    # 5. 保存到本地
    os.makedirs(local_vs_path, exist_ok=True)
    vectorstore.save_local(local_vs_path)
    logger.info(f"[VectorstoreBuilder] FAISS索引保存完成: {local_vs_path}")

    # 6. 保存 documents.pkl (用于 BM25)
    docs_path = os.path.join(local_vs_path, "documents.pkl")
    with open(docs_path, "wb") as f:
        pickle.dump(docs, f)
    logger.info(f"[VectorstoreBuilder] documents.pkl保存完成")

    # 7. 保存 chunks.json (可读格式)
    chunks_json_path = os.path.join(local_vs_path, "chunks.json")
    with open(chunks_json_path, "w", encoding="utf-8") as f:
        json.dump(chunk_records, f, ensure_ascii=False, indent=2)
    logger.info(f"[VectorstoreBuilder] chunks.json保存完成")

    # 8. 上传到 OSS
    if upload_to_oss:
        logger.info(f"[VectorstoreBuilder] 开始上传到OSS: {remote_vs_prefix}")
        oss_upload_dir(local_vs_path, remote_vs_prefix)
        logger.info("[VectorstoreBuilder] 上传完成")



