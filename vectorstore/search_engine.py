"""
Date: 2025-12-24 18:33:19
LastEditTime: 2026-02-28 18:49:08
Description: 这是默认设置,可以在设置》工具》File Description中进行配置
"""
import os
import pickle
from concurrent.futures import as_completed
from typing import List, Dict

from langchain_community.retrievers import BM25Retriever
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

# LLM 校验函数
from repositories.langfuse_integration import (
    check_fee_scope_info_with_langfuse,
    check_waiting_period_info_with_langfuse,
    check_agreement_with_langfuse,
    check_responsibility_discern_with_langfuse,
)
from repositories.remote_embedding_model import RemoteEmbeddings
from config import MAX_CONCURRENT_REQUESTS, RRF_K, BM25_K1, BM25_B
from utils.logger import logger
from infrastructure.thread_pool_manager import get_thread_pool

# jieba 中文分词支持
try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False


def jieba_tokenizer(text: str) -> List[str]:
    """jieba 中文分词，添加保险行业专业术语"""
    if JIEBA_AVAILABLE:
        # 添加保险行业词典
        jieba.add_word("等待期", freq=1000)
        jieba.add_word("责任免除", freq=1000)
        jieba.add_word("免责条款", freq=1000)
        jieba.add_word("既往症", freq=1000)
        return list(jieba.cut(text))
    # 降级方案：字符级分词
    return list(text)


class HybridSearchEngine:
    """负责加载向量库并执行搜索策略"""

    def __init__(self):
        self.embeddings = RemoteEmbeddings()

    def _string_match_search(self, query: str, docs: List[Document]) -> List[Dict]:
        """
        简单的字符串匹配排序：按 query 中关键词在文档中的出现次数排序
        只返回至少包含一个查询词的 chunk
        """
        # 提取查询关键词（2字及以上的词）
        query_chars = list(query)
        query_bigrams = [query[i:i+2] for i in range(len(query)-1)]

        results = []
        for idx, doc in enumerate(docs):
            text = doc.page_content
            # 计算匹配分数：字符匹配 + 二元组匹配
            char_matches = sum(1 for c in query_chars if c in text)
            bigram_matches = sum(1 for bg in query_bigrams if bg in text)
            # 加权：二元组匹配权重更高
            score = char_matches * 1 + bigram_matches * 3

            # 只返回包含查询词的 chunk
            if score > 0:
                results.append({
                    "text": doc.page_content,
                    "metadata": doc.metadata,
                    "score": score,
                    "rank": idx + 1,
                    "source": "string_match",
                    "doc": doc
                })

        # 按分数降序排序并更新 rank
        results.sort(key=lambda x: x["score"], reverse=True)
        for i, r in enumerate(results, 1):
            r["rank"] = i

        return results

    def _deduplicate_by_original_index(
        self,
        vector_results: List[Dict],
        bm25_results: List[Dict],
        string_match_results: List[Dict]
    ) -> List[Dict]:
        """
        通过 original_index 去重合并三种检索结果
        优先级: Vector > BM25 > 字符串匹配
        """
        seen_indices = set()
        merged = []

        # 定义优先级顺序
        all_results = [
            ("vector", vector_results),
            ("bm25", bm25_results),
            ("string_match", string_match_results)
        ]

        for source_name, results in all_results:
            for item in results:
                original_index = item.get("original_index")
                if original_index is None:
                    # 如果没有 original_index，使用 text 的 hash 作为备选
                    original_index = hash(item["text"])

                if original_index not in seen_indices:
                    seen_indices.add(original_index)
                    # 保留来源信息
                    item["sources"] = [source_name]
                    merged.append(item)
                else:
                    # 如果已存在，更新来源信息
                    for existing in merged:
                        if existing.get("original_index") == original_index:
                            if source_name not in existing["sources"]:
                                existing["sources"].append(source_name)
                            break

        logger.info(f"[Search] 去重合并完成, 去重前={len(vector_results)+len(bm25_results)+len(string_match_results)}, "
                    f"去重后={len(merged)}")
        return merged

    def search(self, local_vs_path: str, query: str, query_type: str, top_k: int = 20) -> List[Dict]:
        """
        执行混合检索流程：
        1. 加载 FAISS
        2. 并行执行三种检索：Vector(top30)、BM25-jieba(top50)、字符串匹配(top50)
        3. 通过 original_index 去重
        4. 送入 LLM 重排
        """
        if not os.path.exists(local_vs_path):
            logger.error(f"Vectorstore path not found: {local_vs_path}")
            return []

        vector_results = []
        bm25_results = []
        string_match_results = []
        all_docs = []
        vectorstore = None

        # 1. 加载 VectorStore (FAISS)
        try:
            vectorstore = FAISS.load_local(local_vs_path, self.embeddings, allow_dangerous_deserialization=True)
            # 向量检索 - top30
            vec_docs = vectorstore.similarity_search_with_score(query, k=10)
            for rank, (doc, score) in enumerate(vec_docs, 1):
                # FAISS 返回的是 L2 距离（越小越相似），转换为相似度分数（0-1，越大越相似）
                # 使用 1/(1+distance) 将距离转换为相似度
                similarity_score = 1.0 / (1.0 + float(score))
                vector_results.append({
                    "text": doc.page_content,
                    "metadata": doc.metadata,
                    "vector_score": similarity_score,  # 现在是相似度，不是距离
                    "vector_distance": float(score),   # 保留原始距离用于调试
                    "rank": rank,
                    "source": "vector",
                    "doc": doc,
                    "original_index": doc.metadata.get("original_index")
                })
            logger.info(f"[Search] Vector检索完成, 召回数量={len(vector_results)}")
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")

        # 2. 加载文档用于 BM25 和字符串匹配检索
        try:
            docs_path = os.path.join(local_vs_path, "documents.pkl")

            # 方案 A: 优先从 pickle 文件加载 (最稳健)
            if os.path.exists(docs_path):
                logger.info(f"[Search] 从pickle文件加载文档: {docs_path}")
                with open(docs_path, "rb") as f:
                    all_docs = pickle.load(f)
                logger.info(f"[Search] 加载文档数量={len(all_docs)}")

            # 方案 B: 兼容旧数据 (如果 pickle 不存在，尝试从 FAISS 内部提取)
            elif vectorstore and hasattr(vectorstore, "docstore") and hasattr(vectorstore.docstore, "_dict"):
                logger.info("[Search] 从FAISS内部提取文档(兼容模式)")
                all_docs = list(vectorstore.docstore._dict.values())
                logger.info(f"[Search] 提取文档数量={len(all_docs)}")

        except Exception as e:
            logger.warning(f"[Search] 文档加载失败: {e}")

        # 3. BM25 检索 (jieba分词) - top50
        try:
            if all_docs:
                logger.info(f"[Search] 开始BM25检索, 文档数={len(all_docs)}")
                bm25 = BM25Retriever.from_documents(
                    all_docs,
                    preprocess_func=jieba_tokenizer,
                    k1=BM25_K1,
                    b=BM25_B
                )
                bm25.k = 50  # 只取top50
                bm25_docs = bm25.invoke(query)
                for rank, doc in enumerate(bm25_docs, 1):
                    bm25_results.append({
                        "text": doc.page_content,
                        "metadata": doc.metadata,
                        "rank": rank,
                        "source": "bm25_jieba",
                        "doc": doc,
                        "original_index": doc.metadata.get("original_index")
                    })
                logger.info(f"[Search] BM25检索完成, 召回数量={len(bm25_results)}")
            else:
                logger.warning("[Search] 未找到文档用于BM25初始化")
        except Exception as e:
            logger.warning(f"[Search] BM25检索失败: {e}")

        # 4. 字符串匹配检索 - top50
        try:
            if all_docs:
                logger.info(f"[Search] 开始字符串匹配检索, 文档数={len(all_docs)}")
                string_match_results = self._string_match_search(query, all_docs)[:50]
                # 添加 original_index 字段
                for r in string_match_results:
                    r["original_index"] = r["metadata"].get("original_index")
                logger.info(f"[Search] 字符串匹配检索完成, 召回数量={len(string_match_results)}")
            else:
                logger.warning("[Search] 未找到文档用于字符串匹配检索")
        except Exception as e:
            logger.warning(f"[Search] 字符串匹配检索失败: {e}")

        # 5. 通过 original_index 去重合并
        merged_results = self._deduplicate_by_original_index(
            vector_results, bm25_results, string_match_results
        )

        # 6. LLM 校验（现有逻辑不变）
        return self._llm_refine(merged_results, query, query_type)

    # def _rrf_fusion(self, vector_results: List[Dict], bm25_results: List[Dict], k: int = None) -> List[Dict]:
    #     """
    #     Reciprocal Rank Fusion 融合两种检索结果
    #     在所有召回结果上计算 RRF 分数
    #
    #     Args:
    #         vector_results: Vector检索结果列表，每个元素包含rank
    #         bm25_results: BM25检索结果列表，每个元素包含rank
    #         k: RRF常数，默认使用config.RRF_K (60)
    #
    #     Returns:
    #         按RRF分数排序的合并结果
    #     """
    #     if k is None:
    #         k = RRF_K
    #
    #     rrf_scores = {}
    #     doc_map = {}
    #     sources_map = {}
    #
    #     # 处理Vector结果
    #     for item in vector_results:
    #         rank = item["rank"]
    #         content_hash = hash(item["text"])
    #         rrf_scores[content_hash] = rrf_scores.get(content_hash, 0.0) + 1.0 / (k + rank)
    #         doc_map[content_hash] = item
    #         sources_map[content_hash] = sources_map.get(content_hash, []) + ["vector"]
    #
    #     # 处理BM25结果
    #     for item in bm25_results:
    #         rank = item["rank"]
    #         content_hash = hash(item["text"])
    #         rrf_scores[content_hash] = rrf_scores.get(content_hash, 0.0) + 1.0 / (k + rank)
    #         if content_hash not in doc_map:
    #             doc_map[content_hash] = item
    #         sources_map[content_hash] = sources_map.get(content_hash, []) + ["bm25"]
    #
    #     # 按RRF分数排序
    #     sorted_results = []
    #     for content_hash, score in sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True):
    #         item = {
    #             "text": doc_map[content_hash]["text"],
    #             "metadata": doc_map[content_hash]["metadata"],
    #             "score": float(score),  # RRF分数用于兼容性
    #             "rrf_score": float(score),
    #             "sources": sources_map[content_hash]
    #         }
    #         # 如果存在vector_score，保留它用于调试
    #         if "vector_score" in doc_map[content_hash]:
    #             item["vector_score"] = doc_map[content_hash]["vector_score"]
    #         # 如果存在vector_distance（FAISS原始L2距离），保留它用于调试
    #         if "vector_distance" in doc_map[content_hash]:
    #             item["vector_distance"] = doc_map[content_hash]["vector_distance"]
    #         sorted_results.append(item)
    #
    #     return sorted_results

    def _llm_refine(self, items: List[Dict], query: str, query_type: str) -> List[Dict]:
        """使用 LLM 验证相关性，带速率限制"""
        if not items:
            logger.info("[Search] 无候选项需要LLM校验")
            return []

        # 特殊逻辑
        if query_type == "既往症" or query_type == "特别约定":
            logger.info(f"[Search] 既往症类型跳过LLM校验, 直接返回{len(items)}条结果")
            return items

        # 定义查询类型到校验函数的映射
        query_validators = {
            "理算因子": check_fee_scope_info_with_langfuse,
            "等待期": check_waiting_period_info_with_langfuse,
            # "特别约定": check_agreement_with_langfuse,
            "责任免除": check_responsibility_discern_with_langfuse,
        }

        # 获取对应的校验函数
        validator_func = query_validators.get(query_type)
        if not validator_func:
            logger.warning(f"[Search] 未找到查询类型 '{query_type}' 对应的校验函数，跳过 LLM 校验")
            return items

        # 限制并发数 - 使用全局线程池 + utils.llm 内置限流
        # 所有去重后的候选结果都送入 LLM 校验
        max_workers = min(MAX_CONCURRENT_REQUESTS, len(items))
        logger.info(f"[Search] 开始LLM校验, query_type={query_type}, 候选数={len(items)}, "
                   f"max_concurrent={max_workers}")
        verified_items = []

        # 使用全局线程池
        executor = get_thread_pool("llm_cpu")
        future_to_item = {}
        for item in items:
            # 从 metadata 获取 structure_path，如果不存在则使用 "未知来源"
            structure_path = item.get("metadata", {}).get("structure_path", "未知来源")
            # 拼接文本来源和文本内容
            text_with_source = f"文本来源:{structure_path}\n{item['text']}"

            # 根据不同查询类型调用不同的校验函数，并传递对应的参数
            if query_type == "等待期":
                # 等待期只需要文本参数
                future = executor.submit(validator_func, text_with_source)
            else:
                # 其他类型需要字段名和文本两个参数
                future = executor.submit(validator_func, query, text_with_source)
            future_to_item[future] = item

        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                is_related, abstract = future.result()
                if is_related:
                    item["abstract"] = abstract
                    verified_items.append(item)
            except Exception as e:
                logger.error(f"[Search] LLM校验失败, query_type='{query_type}': {e}")

        logger.info(f"[Search] LLM校验完成, 通过校验数量={len(verified_items)}")
        return verified_items