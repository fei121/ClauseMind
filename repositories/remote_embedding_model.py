"""
Date: 2025-09-28 19:11:30
LastEditTime: 2026-01-20 16:36:13
Description: OpenAI-compatible Embedding 模型客户端
"""
import os
import sys
from typing import List, Optional, Union

import numpy as np
import requests
from langchain_core.embeddings import Embeddings
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import (
    GATEWAY_EMB_URL,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENAI_EMBEDDING_MODEL,
)

# 导入HTTP Session管理
try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from infrastructure.http_session import get_session
except ImportError:
    logger.warning("Infrastructure module not found, using default requests.")
    get_session = lambda: requests.Session()


class RemoteEmbeddingModel:
    """
    OpenAI-compatible 远程 Embedding 服务客户端
    对应的 CURL 示例:
    curl --request POST \
      --url http://127.0.0.1:9000/v1/embeddings \
      --data '{
        "engine": "text-embedding-v4",
        "messages": ["text1", "text2"],
        "customer_id": "personal_demo",
        "dimensions": 1024,
        "encoding_format": "float"
    }'
    """

    def __init__(self, service_url: Optional[str] = None, engine: Optional[str] = None, batch_size: int = 10):
        """
        Args:
            service_url (str): 远程网关地址
            engine (str): 模型引擎标识，默认 "text-embedding-v4"
            batch_size (int): 批处理大小
        """
        self.openai_compatible = bool(OPENAI_API_BASE and OPENAI_API_KEY)
        self.service_url = service_url or (
            f"{OPENAI_API_BASE}/embeddings" if self.openai_compatible else GATEWAY_EMB_URL
        )
        self.engine = engine or (
            OPENAI_EMBEDDING_MODEL if self.openai_compatible else "text-embedding-v4"
        )
        self.batch_size = max(1, int(batch_size))
        self.dim = None  # 首次请求成功后自动推断维度

    def get_embeddings(self, texts: Union[str, List[str]], engine: Optional[str] = None) -> np.ndarray:
        """
        获取文本向量的核心方法
        """
        # 统一输入格式
        single_input = isinstance(texts, str)
        texts_list = [texts] if single_input else list(texts)

        if not texts_list:
            return np.array([])

        all_embeddings = []
        target_engine = engine or self.engine

        try:
            # 分批次调用接口
            for i in range(0, len(texts_list), self.batch_size):
                batch_texts = [str(t) for t in texts_list[i : i + self.batch_size]]
                batch_result = self._call_embedding_api(batch_texts, target_engine)
                all_embeddings.extend(batch_result)

            # 转换为 numpy 数组
            arr = np.array(all_embeddings, dtype=float)

            # 自动记录维度
            if self.dim is None and arr.size > 0:
                self.dim = int(arr.shape[-1])

            # 如果输入是单条文本，返回一维数组；否则返回二维数组
            if single_input:
                return arr[0] if arr.size > 0 else np.array([])
            return arr

        except Exception as e:
            logger.error(f"Embedding 处理失败: {e}")
            raise

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((requests.exceptions.RequestException, ValueError, ConnectionError)),
        reraise=True
    )
    def _call_embedding_api(self, batch_texts: List[str], engine: str) -> List[List[float]]:
        """
        执行实际的 API 请求（包含重试机制）
        """
        if self.openai_compatible:
            payload = {
                "model": engine,
                "input": batch_texts,
                "encoding_format": "float",
            }
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            }
        else:
            payload = {
                "engine": engine,
                "messages": batch_texts,
                "customer_id": "personal_demo",
                "dimensions": 1024,
                "encoding_format": "float"
            }
            headers = {"Content-Type": "application/json"}

        session = get_session()
        response = session.post(
            self.service_url,
            json=payload,
            headers=headers,
            timeout=30,
        )

        if response.status_code != 200:
            raise ValueError(f"HTTP Error {response.status_code}: {response.text}")

        result = response.json()

        if not self.openai_compatible and isinstance(result, dict) and result.get("code", 0) != 0:
            raise ValueError(f"Service Error: {result}")

        if self.openai_compatible:
            data = result.get("data", []) if isinstance(result, dict) else []
            embeddings = [item.get("embedding") for item in data]
        else:
            embeddings = result.get("embeddings") if isinstance(result, dict) else None

        if not isinstance(embeddings, list):
            raise ValueError(f"响应数据格式异常，期望 list，实际返回: {type(result)}")

        return embeddings


class RemoteEmbeddings(Embeddings):
    """
    LangChain 兼容的 Embedding 包装器。
    通过 OpenAI-compatible API 对接远程模型。
    """

    def __init__(self):
        # 初始化远程客户端
        self.client = RemoteEmbeddingModel()

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        为文档列表生成嵌入向量
        """
        if not texts:
            return []
        try:
            embeddings_np = self.client.get_embeddings(texts)
            return embeddings_np.tolist()
        except Exception as e:
            # 记录日志并向上抛出，交由上层处理
            logger.error(f"embed_documents 失败: {e}")
            raise

    def embed_query(self, text: str) -> List[float]:
        """
        为单个查询文本生成嵌入向量
        """
        try:
            embedding_np = self.client.get_embeddings(text)
            # 确保返回一维列表
            if isinstance(embedding_np, np.ndarray) and embedding_np.ndim > 1:
                return embedding_np.flatten().tolist()
            return embedding_np.tolist()
        except Exception as e:
            logger.error(f"embed_query 失败: {e}")
            raise


if __name__ == "__main__":
    # 简单测试代码
    try:
        logger.info("开始测试远程 Embedding 服务...")
        model = RemoteEmbeddings()

        test_texts = ["我想知道天空的颜色", "先聊个十块钱的", "个人 Demo"]
        res_docs = model.embed_documents(test_texts)
        logger.info(f"Batch Embedding 成功，返回数量: {len(res_docs)}，维度: {len(res_docs[0]) if res_docs else 0}")

        test_query = "单个查询测试"
        res_query = model.embed_query(test_query)
        logger.info(f"Query Embedding 成功，维度: {len(res_query)}")

    except Exception as e:
        logger.error(f"测试过程发生异常: {e}")
