"""
Date: 2025-10-24 12:06:27
LastEditTime: 2026-02-24 17:35:09
Description:
本代码功能：
1、跳过报文中“赔付参数字段”的责任级整理，直接进入因子召回流程
2、针对每个计划先统一进行因子召回，生成 {因子类型: 召回文本} 的召回字典
3、随后遍历每个责任，复用该召回字典进行因子拆解，避免重复召回
4、继续从 payScope 补充治疗类型与费用类型
"""

import hashlib
import json
import re
from concurrent.futures import as_completed
from typing import Any, Optional, Dict, List

import json_repair

from config import OSS_BUCKET_NAME, OSS_BASE_PREFIX
from repositories.langfuse_integration import extract_fee_scope_factor_with_langfuse
from repositories.langfuse_integration import get_langfuse_client
from repositories.oss_repository import oss_upload_retrieval_log_and_get_url
from utils import logger
from infrastructure.thread_pool_manager import get_thread_pool
from vectorstore.hybrid_retrieval import build_retrieve_text_graph, RetrieveTextState
from workflows.policy_disassembly.nodes.fee_scope_professional_knowledge_base import (
    ic_factor_type_to_fee_scope_mapping,
    fee_scope_descriptions, ic_factor_type_professional_knowledge, factor_retrieval_extended_dict,
)
langfuse_client = get_langfuse_client()

def run_retrieve_text_graph(state: RetrieveTextState) -> RetrieveTextState:
    """调用文本召回图"""
    graph: Any = build_retrieve_text_graph()
    # from repositories.langfuse_integration import main_handler
    # if main_handler:
    #     result = graph.invoke(
    #         state,
    #         config={"callbacks": [main_handler],"run_name":state.get("queryType")+"_"+state.get("query")}
    #     )
    # else:
    result = graph.invoke(state)
    return result


def compute_extraction_cache_key(factor_type: str, retrieved_snippets: str, structure_tree_leaf: str) -> str:
    """
    计算因子提取的缓存键

    Args:
        factor_type: 因子类型
        retrieved_snippets: 召回的文本片段
        structure_tree_leaf: 责任结构链

    Returns:
        缓存键的哈希值
    """
    # 组合所有影响提取结果的输入
    cache_input = f"{factor_type}|||{structure_tree_leaf}|||{retrieved_snippets}"
    # 使用 SHA256 生成稳定的哈希值
    return hashlib.sha256(cache_input.encode('utf-8')).hexdigest()


def extract_pay_scope_info(pay_scope_text: str):
    """从 payScope 文本中提取【治疗类型】和【费用类型】"""
    if not pay_scope_text:
        return "未指定", "未指定"

    # 提取所有匹配项
    treatment_matches = re.findall(r"【治疗类型：([^】]+)】", pay_scope_text)
    cost_matches = re.findall(r"【费用类型：([^】]+)】", pay_scope_text)

    # 将提取结果用顿号拆分并去重
    typeof_treatment = (
        "、".join(sorted(set("、".join(treatment_matches).split("、"))))
        if treatment_matches else "未指定"
    )
    compensation_costs = (
        "、".join(sorted(set("、".join(cost_matches).split("、"))))
        if cost_matches else "未指定"
    )

    return typeof_treatment, compensation_costs


def normalize_keywords(keywords) -> list:
    """将 fee_scope_keywords 规范化为字符串列表。
    """
    if not keywords:
        return []

    # 已是列表/元组/集合：直接规范为去空白的字符串列表
    if isinstance(keywords, (list, tuple, set)):
        return [str(x).strip() for x in keywords if str(x).strip()]

    # 字符串：仅按常见分隔符切分（已经做过 AST 解析）
    if isinstance(keywords, str):
        s = keywords.strip()
        s2 = s.strip("[](){}")
        s2 = s2.replace("'", "").replace('"', "")
        parts = re.split(r"[，,、\s]+", s2)
        return [p for p in (x.strip() for x in parts) if p]

    # 其他类型：转为单元素字符串列表
    return [str(keywords).strip()] if str(keywords).strip() else []


def retrieve_for_factor(policy_id: str, factor_type: str, max_workers: int = 5):
    """
    针对单一因子类型执行一次并发召回，返回拼接的文本与去重后的chunk列表。
    """
    mapping_value = ic_factor_type_to_fee_scope_mapping.get(factor_type, [])
    if not isinstance(mapping_value, (list, tuple, set)):
        mapping_value = [mapping_value] if mapping_value else []
    factor_retrieval_extended = factor_retrieval_extended_dict.get(factor_type, [])
    query_list = [factor_type] + list(mapping_value) + factor_retrieval_extended
    all_retrieved_chunks = []
    # 并发跑 query_list - 使用全局线程池
    executor = get_thread_pool("llm_cpu")
    future_to_query = {
        executor.submit(
            run_retrieve_text_graph,
            RetrieveTextState(policyId=policy_id, query=query, queryType="理算因子")
        ): query
        for query in query_list
    }
    for future in as_completed(future_to_query):
        result_state = future.result()
        if isinstance(result_state, dict) and result_state.get("relatedChunks"):
            all_retrieved_chunks.extend(result_state["relatedChunks"])

    # 去重
    unique_chunks = []
    seen_texts = set()
    for chunk in all_retrieved_chunks:
        text = chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
        if text not in seen_texts:
            seen_texts.add(text)
            unique_chunks.append(chunk)

    # 拼接召回文本
    if unique_chunks:
        retrieved_snippets = "\n".join([
            f"第{i + 1}段文本: {item.get('metadata', {}).get('structure_path', '')}\n"
            f"{item.get('text', '')}"
            for i, item in enumerate(unique_chunks)
            if isinstance(item, dict)
        ]).strip()
    else:
        retrieved_snippets = ""

    return retrieved_snippets, unique_chunks


def build_plan_factor_recall(policy_id: str, factor_types: list, max_workers: int = 5) -> dict:
    """
    为一个计划构建全局因子召回字典（并发版本）：
    { factor_type: { 'snippets': str, 'chunks': list } }

    使用 ThreadPoolExecutor 并发处理多个因子类型的召回。
    """
    recall_dict = {}

    # 使用全局线程池并发处理每个因子类型
    executor = get_thread_pool("llm_cpu")
    # 提交所有因子类型的召回任务
    future_to_factor_type = {
        executor.submit(retrieve_for_factor, policy_id, ft, max_workers): ft
        for ft in factor_types
    }

    # 收集结果
    for future in as_completed(future_to_factor_type):
        ft = future_to_factor_type[future]
        try:
            snippets, chunks = future.result()
            recall_dict[ft] = {"snippets": snippets, "chunks": chunks}
        except Exception as e:
            logger.error(f"因子类型 {ft} 召回失败: {e}")
            recall_dict[ft] = {"snippets": "", "chunks": []}

    return recall_dict


def extract_single_factor(
    factor: dict,
    global_recall_dict: dict,
    plan_no: str,
    structure_tree_leaf: str,
    liab_name: str,
    model_name: Optional[str],
    extraction_cache: Optional[Dict[str, tuple]] = None,
    session_id: Optional[str] = None
) -> List[tuple]:
    """
    提取单个因子的值（用于并发执行）

    Args:
        factor: 因子字典
        global_recall_dict: 全局召回字典
        plan_no: 计划编号
        structure_tree_leaf: 责任结构链
        liab_name: 责任名称
        model_name: 模型名称
        extraction_cache: 提取缓存字典（用于避免重复提取）
        session_id: Langfuse session ID

    Returns:
        tuple: (factor_type, record_data, updated_factor_data, error)
    """
    factor_type = factor.get("factorType")
    if not factor_type:
        return [(None, {}, {}, "因子类型为空")]

    retrieved_snippets = global_recall_dict.get(factor_type, {}).get("snippets", "") or ""
    record_key = f"{plan_no}:{structure_tree_leaf}-{factor_type}"
    record_data = {
        "召回chunk引用": f"global/factor:{factor_type}"
    }

    if not retrieved_snippets:
        logger.warning(f"责任 {liab_name} 的因子 {factor_type} 未召回到任何条款片段（使用全局召回），跳过该因子解析")
        record_data["模型输入"] = "未触发"
        record_data["模型输出"] = "未触发"
        return [(factor_type, record_data, {}, "未召回到条款片段")]

    # 计算缓存键
    cache_key = compute_extraction_cache_key(factor_type, retrieved_snippets, structure_tree_leaf)

    # 检查缓存
    if extraction_cache is not None and cache_key in extraction_cache:
        cached_result = extraction_cache[cache_key]
        logger.info(f"因子提取缓存命中: 责任 {liab_name} 的因子 {factor_type} (cache_key: {cache_key[:16]}...)")
        record_data["模型输入"] = "使用缓存结果"
        record_data["模型输出"] = "使用缓存结果"
        record_data["缓存命中"] = True
        # 返回缓存的结果
        return cached_result

    # 准备与该因子相关的费用范围及其说明
    allowed_fee_ranges = ic_factor_type_to_fee_scope_mapping.get(factor_type, [])
    if not isinstance(allowed_fee_ranges, (list, tuple, set)):
        allowed_fee_ranges = [allowed_fee_ranges] if allowed_fee_ranges else []
    filtered_fee_scope_descriptions = {
        k: fee_scope_descriptions[k]
        for k in allowed_fee_ranges
        if isinstance(k, str) and k in fee_scope_descriptions
    }

    factor_explanation = ic_factor_type_professional_knowledge.get(factor_type, "")

    # 使用Langfuse进行因子抽取，每个因子独立一个trace
    try:
        response = extract_fee_scope_factor_with_langfuse(
            retrieved_snippets=retrieved_snippets,
            factor_type=factor_type,
            structure_tree_leaf=structure_tree_leaf,
            fee_scope_descriptions=json.dumps(filtered_fee_scope_descriptions, ensure_ascii=False),
            factor_type_to_fee_scope_mapping=json.dumps(allowed_fee_ranges, ensure_ascii=False),
            factor_type_to_fee_scope_default=json.dumps(allowed_fee_ranges[0], ensure_ascii=False),
            factor_explanation=factor_explanation,
            model_name=model_name,
            session_id=session_id or plan_no,
            plan_id=plan_no
        )
        record_data["模型输入"] = f"Langfuse调用 - 因子类型: {factor_type}, planId: {plan_no}, structure_tree: {structure_tree_leaf}"
    except Exception as e:
        logger.error(f"责任 {liab_name} 的因子 {factor_type} 模型调用失败: {e}")
        record_data["模型输入"] = "Langfuse调用"
        record_data["模型输出"] = f"调用失败: {e}"
        return [(factor_type, record_data, {}, f"模型调用失败: {e}")]

    record_data["模型输出"] = response
    record_data["缓存命中"] = False

    try:
        fee_scope_result = json_repair.loads(response)
    except Exception as e:
        logger.warning(f"责任 {liab_name} 的因子 {factor_type} 响应解析失败: {e}; 原始响应: {response}")
        return [(factor_type, record_data, {}, f"响应解析失败: {e}")]

    # 处理响应结果 - 支持列表和字典格式
    results = []

    if isinstance(fee_scope_result, list):
        # 处理列表响应（新格式）
        if len(fee_scope_result) == 0:
            # 空列表表示没有找到匹配的责任
            logger.info(f"责任 {liab_name} 的因子 {factor_type} 未找到匹配的责任")
            return [(factor_type, record_data, {}, None)]

        # 处理列表中的每个结果 - 同一因子的多个结果保持相同factorType
        for result_dict in fee_scope_result:
            if isinstance(result_dict, dict):
                updated_factor_data = {"factorValue": result_dict.get("因子值", ""),
                                       "feeRange": result_dict.get("费用范围", ""),
                                       "extraDescription": result_dict.get("额外描述", [])}

                results.append((factor_type, record_data.copy(), updated_factor_data, None))
            else:
                logger.warning(f"责任 {liab_name} 的因子 {factor_type} 列表中的元素不是字典: {response}")
                continue

    elif isinstance(fee_scope_result, dict):
        # 处理字典响应（向后兼容）
        updated_factor_data = {"factorValue": fee_scope_result.get("因子值", ""),
                               "feeRange": fee_scope_result.get("费用范围", ""),
                               "extraDescription": fee_scope_result.get("额外描述", [])}
        results.append((factor_type, record_data, updated_factor_data, None))

    else:
        logger.warning(f"责任 {liab_name} 的因子 {factor_type} 响应格式异常: {response}")
        return [(factor_type, record_data, {}, f"响应格式异常")]

    # 如果没有有效的结果
    if not results:
        return [(factor_type, record_data, {}, f"未解析到有效结果")]

    # 存入缓存 - 以列表形式存储
    if extraction_cache is not None:
        extraction_cache[cache_key] = results

    # 在函数返回前立即获取 ID（此时本函数 observation 仍活跃）
    # Only attempt to get trace/observation IDs when there's an active span context
    try:
        from opentelemetry import trace as otel_trace
        current_span = otel_trace.get_current_span()
        # Only proceed if there's an active recording span
        if langfuse_client and current_span.is_recording():
            trace_id = langfuse_client.get_current_trace_id()
            observation_id = langfuse_client.get_current_observation_id()
            # 为所有结果添加 ID
            for _, _, updated_factor_data, _ in results:
                if trace_id:
                    updated_factor_data["traceId"] = trace_id
                if observation_id:
                    updated_factor_data["observationId"] = observation_id
    except Exception as e:
        logger.warning(f"无法获取 Langfuse trace/observation ID: {e}")

    return results

def generate_fee_scope(message, policy_id: str, fee_scope_keywords: list, max_workers: int = 5, model_name: Optional[str] = None, session_id: Optional[str] = None):
    """
    直接进行新的因子召回流程（优化版）：
    1. 全局先统一召回所有因子类型，生成召回字典；
    2. 再遍历每个计划与责任，为每个因子创建独立的trace；
    3. 保留记录与 payScope 补充逻辑。

    注意：此函数不再使用chain类型，因为每个因子都有独立的trace。
    此函数作为coordinator（协调器）来组织整体流程。

    Args:
        message: 包含解构结果的消息， Pydantic 模型
        policy_id: 保单ID
        fee_scope_keywords: 费用范围关键词列表
        max_workers: 最大并发工作线程数
        model_name: 可选的模型名称，例如 'Moonshot-Kimi-K2-Instruct'，默认使用环境配置的模型
        session_id: Langfuse session ID，用于追踪
    """
    # 支持 Pydantic 模型，自动转换为字典
    record = {}
    normalized_keywords = normalize_keywords(fee_scope_keywords)

    deconstruct_result_list = message.deconstructResultList
    if not deconstruct_result_list:
        logger.warning("generate_fee_scope: message.deconstructResultList 为空，未处理任何计划")
        return message

    # 注意：不再为整个generate_fee_scope设置全局sessionId，
    # 因为每个因子会在extract_fee_scope_factor_with_langfuse中创建独立的trace
    # 使用各自的sessionId（基于planId）

    # === 1. 全局级：统一执行因子召回 ===
    factor_types = list(dict.fromkeys(normalized_keywords))  # 去重保序
    if not factor_types:
        logger.warning("generate_fee_scope: 未提供任何因子关键词，跳过全部因子解析")
        return message

    logger.info(f"全局因子召回开始，共 {len(factor_types)} 个因子类型")
    global_recall_dict = build_plan_factor_recall(policy_id, factor_types, max_workers=max_workers)

    # 记录召回结果（仅标记有无召回）
    # record["global_factor_recall"] = {
    #     ft: ("有召回" if (global_recall_dict.get(ft, {}).get("snippets") or "") else "无召回")
    #     for ft in factor_types
    # }
    # 新逻辑（直接展示召回结果，与 factorRecallDict 一致）
    record["global_factor_recall"] = {
        ft: global_recall_dict.get(ft, {}).get("snippets", "")
        for ft in factor_types
    }

    # === 2. 遍历每个计划与责任，复用全局召回 ===
    for plan_index, plan_entry in enumerate(deconstruct_result_list):
        deconstruct_info = plan_entry.get("deconstructInfo", {}) or {}
        liab_info_list = deconstruct_info.get("liabInfoList", []) or []
        plan_no = plan_entry.get("planNo", f"plan_{plan_index}")

        # 写入全局召回字典的轻量引用
        plan_entry["factorRecallDict"] = {
            ft: global_recall_dict.get(ft, {}).get("snippets", "") for ft in factor_types
        }

        # 为每个计划创建因子提取缓存，避免同一计划内的重复提取
        extraction_cache = {}
        cache_stats = {"hits": 0, "misses": 0}

        for liab_info in liab_info_list:
            structure_tree_leaf = liab_info.get("structure_tree_leaf", "未提供责任结构链")
            liab_name = liab_info.get("liabName", "未知责任名称")

            # Set up tags for propagation to child observations
            tags = []
            if policy_id:
                tags.append(f"policy_no:{policy_id}")
            if plan_no:
                tags.append(f"plan_no:{plan_no}")
            if structure_tree_leaf:
                tags.append(f"plan_clause_liability_keyword:{structure_tree_leaf}")

            # # Apply tags to all child observations using propagate_attributes
            # with propagate_attributes(tags=tags):
            #     # 初始化 feeScope 列表
            #     fee_scope = [
            #         {"factorType": ft, "factorValue": "", "typeofTreatment": "未指定", "compensationCosts": "未指定"}
            #         for ft in factor_types
            #     ]
            fee_scope = [
                {"factorType": ft, "factorValue": "", "typeofTreatment": "未指定", "compensationCosts": "未指定"}
                for ft in factor_types
            ]

            # 使用全局线程池并发处理所有因子的提取
            executor = get_thread_pool("llm_cpu")
            # 提交所有因子的提取任务
            future_to_factor_index = {
                executor.submit(
                    extract_single_factor,
                    factor,
                    global_recall_dict,
                    plan_no,
                    structure_tree_leaf,
                    liab_name,
                    model_name,
                    extraction_cache,  # 传递缓存字典
                    session_id  # 传递 session_id
                ): idx
                for idx, factor in enumerate(fee_scope)
            }

            # 收集结果并更新 fee_scope
            for future in as_completed(future_to_factor_index):
                idx = future_to_factor_index[future]
                try:
                    results = future.result()  # 现在返回的是 List[tuple]

                    # 处理同一因子的多个结果
                    for result_idx, (factor_type, record_data, updated_factor_data, error) in enumerate(results):
                        if factor_type:
                            # 更新 record
                            record_key = f"{plan_no}:{structure_tree_leaf}-{factor_type}"
                            record[record_key] = record_data

                            # 统计缓存命中情况
                            if record_data.get("缓存命中"):
                                cache_stats["hits"] += 1
                            else:
                                cache_stats["misses"] += 1

                            # 更新 factor 数据
                            if updated_factor_data:
                                if result_idx == 0:
                                    # 第一个结果更新原始位置
                                    fee_scope[idx].update(updated_factor_data)
                                else:
                                    # 后续结果作为新因子添加到fee_scope
                                    new_factor = {
                                        "factorType": factor_type,
                                        "factorValue": updated_factor_data.get("factorValue", ""),
                                        "feeRange": updated_factor_data.get("feeRange", ""),
                                        "extraDescription": updated_factor_data.get("extraDescription", []),
                                        "typeofTreatment": "未指定",
                                        "compensationCosts": "未指定"
                                    }
                                    # 添加trace信息
                                    # if "traceId" in updated_factor_data:
                                    #     new_factor["traceId"] = updated_factor_data["traceId"]
                                    # if "observationId" in updated_factor_data:
                                    #     new_factor["observationId"] = updated_factor_data["observationId"]
                                    fee_scope.append(new_factor)
                except Exception as e:
                    logger.error(f"处理因子提取结果时发生异常: {e}")

                # 写回责任 feeScope
                liab_info["feeScope"] = fee_scope

        # 记录计划级别的缓存统计
        total_extractions = cache_stats["hits"] + cache_stats["misses"]
        if total_extractions > 0:
            cache_hit_rate = cache_stats["hits"] / total_extractions * 100
            logger.info(f"计划 {plan_no} 因子提取统计: 总提取 {total_extractions} 次, "
                       f"缓存命中 {cache_stats['hits']} 次 ({cache_hit_rate:.1f}%), "
                       f"实际调用 {cache_stats['misses']} 次")
            # 如果缓存命中率大于0，说明存在重复提取
            if cache_stats["hits"] > 0:
                logger.warning(f"检测到冗余因子提取: 计划 {plan_no} 有 {cache_stats['hits']} 次重复提取被优化")

        # === 3. 从 payScope 补充治疗类型与费用类型 ===
        for liab_info in liab_info_list:
            pay_scope_text = liab_info.get("payScope", "")
            typeof_treatment, compensation_costs = extract_pay_scope_info(pay_scope_text)
            for factor in liab_info.get("feeScope", []) or []:
                if not factor.get("typeofTreatment") or factor["typeofTreatment"] == "未指定":
                    factor["typeofTreatment"] = typeof_treatment
                if not factor.get("compensationCosts") or factor["compensationCosts"] == "未指定":
                    factor["compensationCosts"] = compensation_costs

        deconstruct_info["liabInfoList"] = liab_info_list
        plan_entry["deconstructInfo"] = deconstruct_info
        message["deconstructResultList"][plan_index] = plan_entry

    logger.info(f"generate_fee_scope 完成: policyId={policy_id}, 全局召回后共处理计划 {len(deconstruct_result_list)} 个")

    # === 4. 将 record 上传到 OSS ===
    try:
        key, url = oss_upload_retrieval_log_and_get_url(policy_id, record, folder=f'{OSS_BASE_PREFIX}/retrieval_logs', expires_seconds=7200)
        logger.info(f"因子检索、上下文输入、模型返回日志已上传到 OSS: oss://{OSS_BUCKET_NAME}/{key}，临时URL: {url}")
    except Exception as e:
        logger.error(f"上传 record 到 OSS 失败: {e}")

    # # === 5. 清空嵌入向量缓存，避免内存占用过大 ===
    # try:
    #     clear_embedding_cache()
    # except Exception as e:
    #     logger.warning(f"清空嵌入向量缓存失败: {e}")

    return message

if __name__ == "__main__":
    response = """{
  "因子类型": "赔付比例",
  "因子值": "100%",
  "费用范围": "医保范围内",
  "额外描述": [
    "门诊、急诊医疗须先行在社保分割后再到保险公司理赔，如未分割则不予理赔（北京社保员工）；异地社保员工按当地医保政策决定是否强制分割",
    "仅计划2开放社保内乙类药品先行自负费用、特殊诊疗先行自负费用及自费费用"
  ]
}"""


    fee_scope_result = json_repair.loads(response)
    # 安全更新当前因子结果
    if isinstance(fee_scope_result, dict):
        print(fee_scope_result)
    else:
        print(type(fee_scope_result))