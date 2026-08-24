"""
Date: 2025-12-24 18:33:44
LastEditTime: 2026-01-20 16:36:13
Description: 这是默认设置,可以在设置》工具》File Description中进行配置
"""
from typing import TypedDict, List, Any

from langgraph.graph import StateGraph, START, END

from utils import logger
from vectorstore.policy_manager import PolicyVectorStoreManager
from vectorstore.search_engine import HybridSearchEngine
from vectorstore.search_cache import get_search_cache


# 1. 定义 State
class RetrieveTextState(TypedDict):
    # Inputs
    policyId: str
    query: str
    queryType: str

    # Internal Processing
    matchedFiles: List[str]
    localVsPath: str  # 关键：只传路径，不传对象

    # Outputs
    results: List[Any]
    code: str
    message: str


# 2. 实例化服务 (单例模式)
manager = PolicyVectorStoreManager()
engine = HybridSearchEngine()


# 3. 定义节点

def node_list_files(state: RetrieveTextState) -> RetrieveTextState:
    """步骤1: 初始化文件列表（将在准备阶段从metadata读取）"""
    logger.info(f"[VectorStore] 初始化文件列表, policyId={state['policyId']}")
    # 初始化空列表，将在node_prepare_store中从metadata读取
    return {**state, "matchedFiles": [], "code": "200"}



def node_prepare_store(state: RetrieveTextState) -> RetrieveTextState:
    """步骤2: 准备向量库 (下载/构建)"""
    if state["code"] != "200": return state

    try:
        # 先从metadata读取文件签名
        logger.info(f"[VectorStore] 开始准备向量库, policyId={state['policyId']}, files_count={len(state['matchedFiles'])}")
        # 这里处理了缓存检查逻辑
        path = manager.ensure_vectorstore(state["policyId"], state["matchedFiles"])
        logger.info(f"[VectorStore] 向量库准备完成, policyId={state['policyId']}, localVsPath={path}")
        return {**state, "localVsPath": path}
    except Exception as e:
        logger.exception(f"[VectorStore] 向量库准备失败, policyId={state['policyId']}, error={e}")
        return {**state, "code": "500", "message": f"Build failed: {e}"}


# 全局缓存实例
_search_cache = get_search_cache()


def node_search(state: RetrieveTextState) -> RetrieveTextState:
    """步骤3: 执行检索（带缓存）"""
    if state["code"] != "200":
        return state

    local_vs_path = state["localVsPath"]
    query = state["query"]
    query_type = state["queryType"]
    policy_id = state["policyId"]

    # 1. 尝试从缓存获取
    cached_results = _search_cache.get(local_vs_path, query, query_type)
    if cached_results is not None:
        logger.info(f"[VectorStore] 缓存命中, policyId={policy_id}, query={query}, queryType={query_type}, results_count={len(cached_results)}")
        return {**state, "results": cached_results}

    # 2. 执行检索
    logger.info(f"[VectorStore] 缓存未命中，执行检索, policyId={policy_id}, query={query}, queryType={query_type}")
    try:
        results = engine.search(
            local_vs_path=local_vs_path,
            query=query,
            query_type=query_type
        )

        # 3. 存入缓存
        _search_cache.put(local_vs_path, query, query_type, results)
        logger.info(f"[VectorStore] 检索完成并缓存, policyId={policy_id}, results_count={len(results)}")

        return {**state, "results": results}
    except Exception as e:
        logger.error(f"[VectorStore] 检索失败, policyId={policy_id}, error={e}")
        return {**state, "code": "500", "message": f"Search failed: {e}"}


# 4. 构建图
def router(state: RetrieveTextState) -> str:
    return "continue" if state["code"] == "200" else "stop"


def build_retrieve_text_graph():
    sg = StateGraph(RetrieveTextState)

    sg.add_node("list_files", node_list_files)
    sg.add_node("prepare", node_prepare_store)
    sg.add_node("search", node_search)

    sg.add_edge(START, "list_files")

    sg.add_conditional_edges(
        "list_files", router,
        {"continue": "prepare", "stop": END}
    )

    sg.add_conditional_edges(
        "prepare", router,
        {"continue": "search", "stop": END}
    )

    sg.add_edge("search", END)

    return sg.compile()


# --- Entry Point (workflow.py 底部) ---
if __name__ == "__main__":
    # 配置 logging 显示详细报错
    import logging

    logging.basicConfig(level=logging.INFO)

    graph = build_retrieve_text_graph()

    # 输入测试数据
    input_payload = {
        "policyId": "318612000000000241",  # 确保此 Policy ID 在 OSS 上存在且有 .md 文件
        "query": "等待期",
        "queryType": "等待期"
    }

    print(f"--- 开始执行流程: {input_payload['policyId']} ---")

    try:
        output = graph.invoke(input_payload)

        print("\n" + "=" * 30)
        print(f"最终状态 (Code): {output.get('code')}")

        # 【关键】打印具体的错误信息
        if output.get('code') != "200":
            print(f"❌ 错误详情 (Message): {output.get('message')}")
            # 如果是 OSS 列表为空，这里会显示
        else:
            results = output.get('results', [])
            print(f"✅ 执行成功，召回结果数: {len(results)}")
            if results:
                print("-" * 20)
                # 打印第一条结果预览
                first_res = results[0]
                content = first_res.get('text', '')[:100].replace('\n', ' ')
                print(f"Top 1 内容预览: {content}...")
                print(f"来源: {first_res.get('sources')}")

    except Exception as e:
        print(f"❌ 流程外部崩溃: {e}")
        import traceback

        traceback.print_exc()