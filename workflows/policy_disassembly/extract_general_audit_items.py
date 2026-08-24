"""
Date: 2025-08-29 15:40:21
LastEditTime: 2026-02-28 15:16:09
Description: 这是默认设置,可以在设置》工具》File Description中进行配置
"""
import json
import re
from pathlib import Path
from typing import List, Dict, Any, TypedDict, Optional, Union, Tuple

from json_repair import json_repair
from langgraph.graph import StateGraph, START, END

# Import Langfuse integration functions
from repositories.langfuse_integration import (
    extract_special_agreement_index_with_langfuse,
    extract_liability_index_with_langfuse,
    extract_responsibility_discern_index_with_langfuse,
    extract_waiting_period_scope_with_langfuse,
    extract_past_illness_scope_with_langfuse,
    generate_base_compensation_with_langfuse,
    generate_multi_compensation_with_langfuse,
    generate_waiting_period_with_langfuse,
    generate_past_illness_with_langfuse,
    generate_session_id_with_timestamp,
    evaluate_result_confidence_with_langfuse
)
from utils import logger
from vectorstore.hybrid_retrieval import build_retrieve_text_graph, RetrieveTextState


# ====== Prompt Alias 定义（仅做引用） ======
# CATALOG_RETRIEVER_PROMPT = 特别约定索引查找prompt  # Step1
# LIABILITY_INDEX_PROMPT  = 保险责任索引查找prompt_事故治疗类型  # Step2
# HOSPITAL_SCOPE_PROMPT = 特别约定抽取prompt_医院范围  # Step3
# MULTI_SCENARIO_PROMPT = 特别约定抽取prompt_修改基础情形  # Step4
# BASE_COMPENSATION_PROMPT = 通用赔付范围生成prompt  # Step5
# MULTI_COMPENSATION_PROMPT = 多情形赔付范围生成prompt  # Step6
# WAITING_PERIOD_EXTRACTION_PROMPT = 特别约定抽取prompt_等待期  # 额外：等待期
# PAST_ILLNESS_EXTRACTION_PROMPT = 特别约定抽取prompt_既往症  # 额外：既往症
# WAITING_PERIOD_PROMPT = 等待期生成prompt  # 额外：等待期
# PAST_ILLNESS_PROMPT = 既往症生成prompt  # 额外：既往症

# ================== 基于 langgraph 的流程 ==================
class FlowState(TypedDict, total=False):
    policy_no: str
    session_id: str  # 新增：计划编号，用于Langfuse追踪
    catalog_md_path: str
    markdown_chunks_with_idx_json_path: str
    catalog_content: str
    special_agreement_indexes: List[int]
    markdown_chunks_with_idx_json: List[Dict[str, Any]]
    # selected_contents: List[Dict[str, Any]]  # [{'index':int,'content':str}]
    selected_agreement_merged_text: str  # 确保此字段被正确定义
    past_illness_scope_merged: str  # 额外：拼接后的既往症整段
    text_blocks: List[Dict[str, Any]]  # [{'index':int,'block_id':int,'text':str}]
    parsed_rows: List[Dict[str, Any]]
    liabilities_raw: str  # 新增：模型原始返回
    liabilities_list: List[str]  # 新增：标准化后的列表
    plan_clause_liability_keyword: str
    plan_keyword: str
    plan_indexes: List[int]
    clause_keyword: str
    liability_keyword: str
    liability_indexes: List[int]
    liability_clauses: List[str]
    # 新增：拼接后的责任条款整段
    liability_clauses_joined: str
    hospital_scope_merged: str
    waiting_period_scope_merged: str  # 新增：拼接后的等待期整段
    # multi_scenario_results: List[Dict[str, Any]]  # [{'index':int,'block_id':int,'lines':[...]}]
    # 新增：拼接后的多情形补充文本整段
    # multi_scenario_lines_joined: str
    base_compensation_raw: str
    base_compensation_json: Any
    multi_compensation_raw: str  # 确保此字段被正确定义
    multi_compensation_json: Any
    structure_tree_markdown_table: str  # 新增：多情形赔付范围生成用结构树 JSON 字符串
    past_illness_json: Any  # 额外：既往症结果
    waiting_period_json: Any  # 额外：等待期结果
    past_illness_raw: str  # 额外：既往症原始文本
    # waiting_period_raw: str  # 额外：等待期原始文本

    # ================== Responsibility Discernment Fields ==================
    org_code: str  # Organization code for responsibility_discern
    responsibility_discern_indexes: List[int]  # Extracted indices from Langfuse
    responsibility_discern_text: str  # Merged text from indices + vector search
    responsibility_discern_result: Dict[str, Any]  # Result from responsibility_discern function

    # ================== Confidence Evaluation Fields ==================
    confidence_evaluation_result: Dict[str, Any]  # 完整的字段级置信度评估结果


# ====== 覆盖 build_graph：串联 Step1~Step6 ======
def build_graph():
    graph = StateGraph(FlowState)# type: ignore
    # Step1
    graph.add_node('prepare_states', prepare_states)  # type: ignore # in: catalog_md_path, markdown_chunks_with_idx_json_path, plan_clause_liability_keyword -> out: catalog_content, markdown_chunks_with_idx_json, plan_keyword, clause_keyword, liability_keyword

    # 特别约定正文（整合原 run_special_agreement_index_reader 节点）
    graph.add_node('select_agreement_contents',
                   select_agreement_contents)  # type: ignore # in: catalog_content, markdown_chunks_with_idx_json, catalog_md_path, policy_no, plan_no -> out: selected_agreement_merged_text, special_agreement_indexes

    # 责任条款正文（整合原 run_liability_index_reader 节点）
    graph.add_node('select_liability_clauses',
                   select_liability_clauses)  # type: ignore # in: catalog_content, markdown_chunks_with_idx_json, clause_keyword, liability_keyword -> out: liability_clauses_joined, liability_indexes

    # Step3-Step6（生成节点已整合提取逻辑）
    graph.add_node('generate_base_compensation',
                   generate_base_compensation)  # type: ignore # in: plan_clause_liability_keyword, liability_clauses_joined, policy_no -> out: base_compensation_raw, base_compensation_json
    graph.add_node('generate_multi_compensation',
                   generate_multi_compensation)  # type: ignore # in: plan_clause_liability_keyword, base_compensation_json, structure_tree_markdown_table, selected_agreement_merged_text, policy_no -> out: multi_compensation_raw, multi_compensation_json
    graph.add_node('generate_waiting_period',
                   generate_waiting_period)  # type: ignore # in: selected_agreement_merged_text, catalog_md_path, policy_no, structure_tree_markdown_table, plan_clause_liability_keyword -> out: waiting_period_scope_merged, waiting_period_json
    graph.add_node('generate_past_illness',
                   generate_past_illness)  # type: ignore # in: selected_agreement_merged_text, catalog_md_path, policy_no, structure_tree_markdown_table, plan_clause_liability_keyword -> out: past_illness_scope_merged, past_illness_json

    # Responsibility discernment node（新增：责任免除条款抽取，放在流程最后）
    graph.add_node('generate_responsibility_discern',
                   generate_responsibility_discern)  # type: ignore # in: catalog_content, markdown_chunks_with_idx_json, org_code, policyNo, policy_no, clause_keyword, liability_keyword, catalog_md_path -> out: responsibility_discern_indexes, responsibility_discern_text, responsibility_discern_result

    # Confidence evaluation node（新增：置信度评估节点）
    graph.add_node('evaluate_result_confidence',
                   evaluate_result_confidence)  # type: ignore # in: selected_agreement_merged_text, waiting_period_scope_merged, past_illness_scope_merged, multi_compensation_json, waiting_period_json, past_illness_json, plan_clause_liability_keyword -> out: confidence_evaluation_score, confidence_evaluation_reasoning

    # Edges（数据流串联说明）
    graph.add_edge(START, 'prepare_states')
    graph.add_edge('prepare_states', 'select_agreement_contents')
    graph.add_edge('select_agreement_contents', 'select_liability_clauses')
    graph.add_edge('select_liability_clauses', 'generate_base_compensation')
    graph.add_edge('generate_base_compensation', 'generate_multi_compensation')
    graph.add_edge('generate_multi_compensation', 'generate_waiting_period')
    graph.add_edge('generate_waiting_period', 'generate_past_illness')
    graph.add_edge('generate_past_illness', 'generate_responsibility_discern')
    graph.add_edge('generate_responsibility_discern', 'evaluate_result_confidence')
    graph.add_edge('evaluate_result_confidence', END)
    return graph.compile()


def prepare_states(state: FlowState) -> FlowState:
    # 加载 catalog_content
    path = Path(state['catalog_md_path'])
    state['catalog_content'] = path.read_text(encoding='utf-8')

    # 加载 markdown_chunks_with_idx_json
    if 'markdown_chunks_with_idx_json_path' in state:
        json_path = Path(state['markdown_chunks_with_idx_json_path'])
        raw = json_path.read_text(encoding='utf-8')
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError('markdown_chunks_with_idx_json 必须是 JSON 数组，例如: [{"index":0,"content":"..."}, ...]')
        state['markdown_chunks_with_idx_json'] = data

    # 解析 plan_clause_liability_keyword -> plan / clause / liability
    if not state.get('liability_keyword'):
        pcl = state.get('plan_clause_liability_keyword', '') or ''
        if pcl:
            parts = pcl.split('_')
            if len(parts) >= 3:
                state['plan_keyword'] = parts[0].strip()
                state['clause_keyword'] = parts[1].strip()
                # 责任部分可能再含 '_', 合并回去
                state['liability_keyword'] = '_'.join(parts[2:]).strip()
            else:
                # 回退：整体当作 liability
                state['plan_keyword'] = pcl.strip()
                state['clause_keyword'] = pcl.strip()
                state['liability_keyword'] = pcl.strip()

    return state


def generate_responsibility_discern(state: FlowState) -> FlowState:
    """
    责任免除条款抽取和生成函数
    ---
    - 从目录内容中提取责任免除条款索引
    - 通过索引检索文本块
    - 始终执行向量搜索召回相关内容（并集策略，防止 LLM 漏提索引导致上下文缺失）
    - 将索引提取结果与向量搜索结果合并
    - 调用 responsibility_discern 函数进行责免分析
    """
    # Extract required fields from state
    catalog_content = state.get('catalog_content', '')
    policy_no = state.get('policy_no') or 'unknown'
    org_code = state.get('org_code')
    records: List[Dict[str, Any]] = state.get('markdown_chunks_with_idx_json', [])

    if not catalog_content:
        logger.warning('catalog_content is empty, cannot extract responsibility discernment indexes')
        state['responsibility_discern_indexes'] = []
        state['responsibility_discern_text'] = ''
        state['responsibility_discern_result'] = {'nonResponsibilityList': [], 'healthNoticeList': []}
        return state

    session_id = state.get('session_id')

    # Step 1: Extract indexes using Langfuse
    try:
        clause_keyword = state.get('clause_keyword', '')
        liability_keyword = state.get('liability_keyword', '')
        indexes = extract_responsibility_discern_index_with_langfuse(
            clause=clause_keyword,
            liability=liability_keyword,
            catalog_content=catalog_content,
            session_id=session_id
        )
        state['responsibility_discern_indexes'] = indexes
        logger.info(f"Extracted {len(indexes)} responsibility discernment indexes")
    except Exception as e:
        logger.error(f"Failed to extract responsibility discernment indexes: {e}")
        indexes = []
        state['responsibility_discern_indexes'] = []

    # Step 2: 第一部分 - 通过索引提取内容并合并（返回字典格式）
    catalog_items = []
    if indexes:
        catalog_result = extract_chunks_by_index(indexes, records)
        merged_text_from_indexes = catalog_result.get("text", "")
        catalog_items = catalog_result.get("items", [])
        logger.info(f"Retrieved text from indexes: {len(merged_text_from_indexes)} characters, {len(catalog_items)} items")
    else:
        merged_text_from_indexes = ""

    # Step 3: 第二部分 - 向量检索（始终执行，并集策略）
    vector_items = []
    try:
        vector_result = retrieve_text_by_vector_search(
            policy_id=policy_no,
            query="责任免除",
            query_type="责任免除"
        )
        merged_text_from_vector = vector_result.get("text", "")
        vector_items = vector_result.get("items", [])
        logger.info(f"Retrieved text from vector search: {len(merged_text_from_vector)} characters, {len(vector_items)} items")
    except Exception as e:
        logger.error(f"Vector search failed: {e}")
        merged_text_from_vector = ""

    # Step 4: 基于 chunk index 去重合并（目录检索优先）
    all_items: Dict[int, Dict[str, Any]] = {}

    # 先放入目录检索结果
    for item in catalog_items:
        idx = item.get("index")
        if idx is not None:
            all_items[idx] = item

    # 再放入向量检索结果（只添加不存在的 index）
    for item in vector_items:
        idx = item.get("index")
        if idx is not None and idx not in all_items:
            all_items[idx] = item

    # 按 index 排序并构建最终文本
    sorted_items = sorted(all_items.values(), key=lambda x: x.get("index", 0))
    if sorted_items:
        content_parts = [
            f"- 第{i + 1}段文本："
            f"文本来源：{item.get('structure_path', '')}\n"
            f"{item.get('content', '')}"
            for i, item in enumerate(sorted_items)
            if item.get("content")
        ]
        final_content = "\n".join(content_parts).strip()
        final_text = f"【责任免除合并内容】\n\n{final_content}"
    else:
        final_text = merged_text_from_indexes or merged_text_from_vector

    state['responsibility_discern_text'] = final_text

    # Step 4: Call responsibility_discern function
    try:
        # Import here to avoid circular imports
        from workflows.policy_disassembly.nodes.responsibility_agent import responsibility_discern

        # Note: health_notice_text is not implemented yet, passing empty string
        result = responsibility_discern(
            non_responsibility_text=final_text,
            health_notice_text="",  # TODO: Implement health notice extraction
            policy_no=policy_no,
            org_code=org_code
        )

        state['responsibility_discern_result'] = result
        logger.info("Responsibility discernment completed successfully")
        # logger.debug(f"Result: {result}")

    except Exception as e:
        logger.error(f"Responsibility discernment failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        state['responsibility_discern_result'] = {'nonResponsibilityList': [], 'healthNoticeList': []}

    return state


def _analyze_invoice_scenarios(multi_comp: Union[Dict, List, None]) -> Tuple[List[int], List[Dict]]:
    """
    分析发票情形，确定哪些情形需要评估

    Args:
        multi_comp: multi_compensation_json 数据

    Returns:
        Tuple[List[int], List[Dict]]: (scenario索引列表, 用于评估的情形列表)
    """
    if not multi_comp:
        return [], []

    # 统一转换为列表
    if isinstance(multi_comp, dict):
        items = [multi_comp]
    else:
        items = list(multi_comp) if isinstance(multi_comp, list) else []

    if not items:
        return [], []

    # 检查每个情形的发票是否为空
    non_empty_invoice_indices = []
    for idx, item in enumerate(items):
        if isinstance(item, dict):
            invoice = item.get('发票', '')
            # 判定为空：None, 空字符串, 或仅空白字符
            if invoice is not None and str(invoice).strip():
                non_empty_invoice_indices.append(idx)

    if not non_empty_invoice_indices:
        # 场景 B：所有发票为空，评估所有情形
        return list(range(len(items))), items
    else:
        # 场景 A：有非空发票，仅评估非空情形
        selected_items = [items[i] for i in non_empty_invoice_indices]
        return non_empty_invoice_indices, selected_items


def _build_ai_extraction_result_for_confidence(
    multi_comp: Union[Dict, List, None],
    waiting_period: Optional[Dict],
    past_illness: Optional[Dict]
) -> Tuple[str, Dict[str, List[int]]]:
    """
    构建用于置信度评估的 AI 提取结果文本

    Returns:
        Tuple[str, Dict]: (评估文本, 字段到scenario索引的映射)
    """
    ai_result_parts = []
    scenario_mapping = {}  # 记录每个字段对应的 scenario 索引

    # 处理赔付范围
    if multi_comp:
        # 统一转换为列表
        if isinstance(multi_comp, dict):
            items = [multi_comp]
        else:
            items = list(multi_comp) if isinstance(multi_comp, list) else []

        if items:
            # 医院范围：只取第一个情形
            if len(items) > 0 and isinstance(items[0], dict):
                hospital_scope = items[0].get('医院范围', '')
                scenario_mapping['医院范围'] = [0]
                ai_result_parts.append("【医院范围】\n" + json.dumps(
                    {"情形0": {"医院范围": hospital_scope}},
                    ensure_ascii=False, indent=2
                ))

            # 发票：根据逻辑选择情形
            invoice_scenarios, selected_items = _analyze_invoice_scenarios(multi_comp)
            scenario_mapping['发票'] = invoice_scenarios

            # 构建发票评估文本
            invoice_parts = []
            for idx, item in zip(invoice_scenarios, selected_items):
                if isinstance(item, dict):
                    invoice = item.get('发票', '')
                    invoice_parts.append(f'"情形{idx}": {{"发票": "{invoice}"}}')
            if invoice_parts:
                ai_result_parts.append("【发票】\n{\n" + ",\n".join(invoice_parts) + "\n}")

    # 等待期和既往症保持原有格式（不添加 scenario 字段）
    if waiting_period:
        ai_result_parts.append("【等待期】\n" + json.dumps(waiting_period, ensure_ascii=False, indent=2))

    if past_illness:
        ai_result_parts.append("【既往症】\n" + json.dumps(past_illness, ensure_ascii=False, indent=2))

    return "\n\n".join(ai_result_parts), scenario_mapping


def evaluate_result_confidence(state: FlowState) -> FlowState:
    """
    置信度评估节点：评估模型拆解结果的置信度
    """
    # 1. 汇总所有召回文本作为 recall_context
    liability_text = state.get('liability_clauses_joined', '')
    agreement_text = state.get('selected_agreement_merged_text', '')
    waiting_period_text = state.get('waiting_period_scope_merged', '')
    past_illness_text = state.get('past_illness_scope_merged', '')
    recall_context = ""
    if liability_text:
        recall_context += "【责任条款召回文本】\n" + liability_text + "\n\n"
    if agreement_text:
        recall_context += "【特别约定召回文本】\n" + agreement_text + "\n\n"
    if waiting_period_text:
        recall_context += "【等待期召回文本】\n" + waiting_period_text + "\n\n"
    if past_illness_text:
        recall_context += "【既往症召回文本】\n" + past_illness_text + "\n\n"

    # 2. 准备数据
    multi_comp = state.get('multi_compensation_json', {})
    waiting_period = state.get('waiting_period_json', {})
    past_illness = state.get('past_illness_json', {})

    # 3. 构建用于评估的 ai_extraction_result 和 scenario 映射
    ai_extraction_result, scenario_mapping = _build_ai_extraction_result_for_confidence(
        multi_comp, waiting_period, past_illness
    )

    # 4. 调用 LLM 进行评估
    try:
        result = evaluate_result_confidence_with_langfuse(
            recall_context=recall_context,
            ai_extraction_result=ai_extraction_result,
            plan_clause_liability_keyword=state.get('plan_clause_liability_keyword', ''),
            session_id=state.get('session_id')
        )

        # 5. 添加 scenario 字段到结果中
        for field_name, scenarios in scenario_mapping.items():
            if field_name in result and isinstance(result[field_name], dict):
                result[field_name]['scenario'] = scenarios

        # 6. 存储完整的字段级置信度评估结果
        state['confidence_evaluation_result'] = result
        logger.info(f"置信度评估完成：result={result}")

    except Exception as e:
        logger.error(f"Confidence evaluation failed: {e}")
        state['confidence_evaluation_result'] = {}

    return state


def extract_chunks_by_index(indexes: List[int], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    根据索引列表从 records 中提取对应的 chunk 并合并文本

    匹配策略：
    1. 精确匹配：record['index'] == query_idx（整数）
    2. 子块匹配：支持被分割的超长 chunk，如 index 为 "5_0", "5_1" 的子块
       当查询 index=5 时，会匹配所有 "5_*" 格式的子块并按顺序合并
    3. metadata 回溯：检查 metadata['original_chunk_index'] 字段
    4. 递归子块提取：根据 structure_path 递归提取所有子块

    递归提取规则：
    - 父子关系判断：子块的 structure_path 必须以父块的 structure_path 加 "/" 开头
    - 去重：如果子块的 index 已经在 indexes 列表中，不再重复添加
    - 输出顺序：按 index 数值升序排列

    Args:
        indexes: 要提取的索引列表
        records: 包含 chunk 信息的记录列表，每个记录应包含 'index' 字段

    Returns:
        Dict[str, Any]: 包含以下字段的字典：
            - "text": str, 合并后的文本，格式为 "【目录检索内容】\n\n第1段文本:\n{content}..."
            - "items": List[Dict]，每个字典包含：
                - "index": int, chunk 的索引（父块索引）
                - "content": str, chunk 的文本内容
                - "structure_path": str, 结构路径
    """
    if not indexes or not records:
        return {"text": "", "items": []}

    # 1. 构建 index -> record 映射，方便快速查找
    index_to_record = {r.get('index'): r for r in records if isinstance(r, dict)}

    # 2. 收集所有需要提取的 index（包括递归子块）
    indexes_to_extract = set(indexes)
    child_indexes_count = 0

    for query_idx in indexes:
        parent_record = index_to_record.get(query_idx)
        if not parent_record:
            continue

        parent_metadata = parent_record.get('metadata', {})
        parent_structure_path = parent_metadata.get('structure_path')

        if parent_structure_path:
            # 查找所有子块：子块的 structure_path 以父块路径加 "/" 开头
            prefix = f"{parent_structure_path}/"
            for record in records:
                if not isinstance(record, dict):
                    continue

                record_idx = record.get('index')
                # 跳过已在 indexes 中的（去重）
                if record_idx in indexes:
                    continue

                record_metadata = record.get('metadata', {})
                record_structure_path = record_metadata.get('structure_path')

                # 检查是否为子块
                if record_structure_path and record_structure_path.startswith(prefix):
                    if record_idx not in indexes_to_extract:
                        indexes_to_extract.add(record_idx)
                        child_indexes_count += 1

    if child_indexes_count > 0:
        logger.info(f"extract_chunks_by_index: 递归提取了 {child_indexes_count} 个子块，"
                    f"原始索引 {indexes}，最终索引 {sorted(indexes_to_extract)}")

    # 3. 构建父子块映射关系，将子块归属到父块下
    # parent_children_map: {parent_idx: [child_idx1, child_idx2, ...]}
    parent_children_map: Dict[int, List[int]] = {idx: [] for idx in indexes}

    for idx in indexes_to_extract:
        if idx in indexes:
            # 这是原始父块索引，跳过
            continue
        # 找到该子块所属的父块
        child_record = index_to_record.get(idx)
        if not child_record:
            continue
        child_structure_path = child_record.get('metadata', {}).get('structure_path', '')

        # 遍历原始索引，找到匹配的父块
        for parent_idx in indexes:
            parent_record = index_to_record.get(parent_idx)
            if not parent_record:
                continue
            parent_structure_path = parent_record.get('metadata', {}).get('structure_path', '')
            if parent_structure_path and child_structure_path.startswith(f"{parent_structure_path}/"):
                parent_children_map[parent_idx].append(idx)
                break

    # 4. 按原始索引顺序处理，合并父块及其子块内容
    selected: List[Dict[str, Any]] = []

    for parent_idx in sorted(indexes):
        # 获取该父块及其所有子块的索引，按升序排列
        related_indexes = [parent_idx] + sorted(parent_children_map.get(parent_idx, []))
        all_contents: List[str] = []

        for query_idx in related_indexes:
            matched_records = []

            for record in records:
                if not isinstance(record, dict):
                    continue

                record_index = record.get('index')
                metadata = record.get('metadata', {})

                # 策略1: 精确整数匹配
                if isinstance(record_index, int) and record_index == query_idx:
                    matched_records.append((0, record))  # (排序键, record)
                    continue

                # 策略2: 子块字符串匹配 (如 "5_0", "5_1")
                if isinstance(record_index, str):
                    # 检查是否为 "{query_idx}_{sub_idx}" 格式
                    if record_index.startswith(f"{query_idx}_"):
                        try:
                            sub_idx = int(record_index.split('_')[1])
                            matched_records.append((sub_idx, record))
                            continue
                        except (ValueError, IndexError):
                            pass

                # 策略3: 通过 metadata 中的 original_chunk_index 匹配
                original_index = metadata.get('original_chunk_index')
                if original_index == query_idx:
                    split_part = metadata.get('split_part', 0)
                    matched_records.append((split_part, record))

            # 按子块顺序排序并合并内容
            if matched_records:
                matched_records.sort(key=lambda x: x[0])
                chunk_content = '\n'.join([
                    rec.get('page_content') or rec.get('content') or ''
                    for _, rec in matched_records
                ])
                all_contents.append(chunk_content)
            else:
                logger.warning(f"extract_chunks_by_index: 索引 {query_idx} 未找到匹配的记录")

        # 将父块及其子块的内容合并为一个整体
        if all_contents:
            combined_content = '\n'.join(all_contents)
            child_count = len(parent_children_map.get(parent_idx, []))
            # 获取父块的 structure_path
            parent_record = index_to_record.get(parent_idx, {})
            parent_structure_path = parent_record.get('metadata', {}).get('structure_path', '')

            selected.append({
                'index': parent_idx,
                'content': combined_content,
                'structure_path': parent_structure_path,
                'source_index_query': parent_idx,
                'sub_chunks_count': len(related_indexes),
                'child_indexes': parent_children_map.get(parent_idx, [])
            })
            if child_count > 0:
                logger.info(f"extract_chunks_by_index: 父块 {parent_idx} 合并了 {child_count} 个子块")

    # 合并第一部分文本（带段号标识和目录检索标识）
    if selected:
        content = "\n".join([
            f"- 第{i + 1}段文本："
            f"文本来源：{rec.get('structure_path', '')}\n"
            f"{rec.get('content', '')}"
            for i, rec in enumerate(selected)
            if rec.get('content')
        ]).strip()
        merged_text_from_indexes = f"【目录检索内容】\n\n{content}"
    else:
        logger.warning("extract_chunks_by_index: 没有找到任何匹配的文本块，返回空字符串")
        merged_text_from_indexes = ""

    # 构建返回结果：包含文本和结构化 items
    items = [
        {
            "index": rec.get('index'),
            "content": rec.get('content', ''),
            "structure_path": rec.get('structure_path', '')
        }
        for rec in selected
        if rec.get('content')
    ]

    return {
        "text": merged_text_from_indexes,
        "items": items
    }


def select_agreement_contents(state: FlowState) -> FlowState:
    """
    合并两部分内容：
    1. 通过索引精确/位置匹配获取的文本（目录检索）
    2. 通过向量检索获取的相关文本（向量检索）

    该函数包含了索引提取和文本抽取的逻辑（原run_special_agreement_index_reader节点）

    去重逻辑：
    - 基于 chunk index 进行去重，优先保留目录检索结果
    - 目录检索和向量检索返回相同 chunk 时，只保留目录检索结果
    """

    # 1. 提取必要组件
    policy_no = state.get('policy_no')
    session_id = state.get('session_id')
    records: List[Dict[str, Any]] = state.get('markdown_chunks_with_idx_json', [])

    # 2. 提取特别约定索引（整合自 run_special_agreement_index_reader）
    special_agreement_indexes = extract_special_agreement_index_with_langfuse(
        state['catalog_content'], session_id=session_id
    )
    state['special_agreement_indexes'] = special_agreement_indexes

    # 3. 第一部分：通过索引提取内容（返回字典格式，包含 items）
    catalog_result = extract_chunks_by_index(special_agreement_indexes, records)
    catalog_items = catalog_result.get("items", [])

    # 4. 第二部分：向量检索（始终执行，返回字典格式，包含 items）
    vector_result = retrieve_text_by_vector_search(
        policy_id=policy_no or 'unknown',
        query="特别约定",
        query_type="特别约定"
    )
    vector_items = vector_result.get("items", [])

    # 5. 基于 chunk index 去重，优先保留目录检索结果
    all_items: Dict[int, Dict[str, Any]] = {}

    # 先放入目录检索结果（优先级更高）
    for item in catalog_items:
        idx = item.get("index")
        if idx is not None:
            all_items[idx] = item

    # 再放入向量检索结果（只添加不存在的 index）
    vector_duplicates = 0
    for item in vector_items:
        idx = item.get("index")
        if idx is not None:
            if idx not in all_items:
                all_items[idx] = item
            else:
                vector_duplicates += 1

    # 记录去重统计
    total_before_dedup = len(catalog_items) + len(vector_items)
    total_after_dedup = len(all_items)
    logger.info(
        f"select_agreement_contents: 去重统计 - "
        f"目录检索: {len(catalog_items)} 条, "
        f"向量检索: {len(vector_items)} 条, "
        f"重复: {vector_duplicates} 条, "
        f"最终: {total_after_dedup} 条"
    )

    # 6. 按 index 排序并构建最终文本
    sorted_items = sorted(all_items.values(), key=lambda x: x.get("index", 0))

    if sorted_items:
        content_parts = [
            f"- 第{i + 1}段文本："
            f"文本来源：{item.get('structure_path', '')}\n"
            f"{item.get('content', '')}"
            for i, item in enumerate(sorted_items)
            if item.get("content")
        ]
        final_content = "\n".join(content_parts).strip()
        final_merged_text = f"【特别约定合并内容】\n\n{final_content}"
    else:
        final_merged_text = ""
        logger.warning('select_agreement_contents: 目录检索和向量检索均未返回任何内容')

    # 7. 更新state
    state['selected_agreement_merged_text'] = final_merged_text

    # 8. 警告检查
    if not final_merged_text.strip():
        logger.warning('selected_agreement_merged_text 为空，无法通过特别约定抽取')

    return state


# ====== 责任条款正文抽取 ======
def select_liability_clauses(state: FlowState) -> FlowState:
    """
    仅按 0-based:
      1. 先用 index 精确匹配
      2. 若未命中且 0 <= idx < total 则按位置取

    该函数整合了索引提取和文本抽取的逻辑（原run_liability_index_reader节点）
    """
    # 1. 提取责任条款索引（整合自 run_liability_index_reader）
    if not state.get('liability_keyword'):
        logger.error('liability_keyword 为空，无法抽取责任条款索引')
        state['liability_indexes'] = []
        state['liability_clauses_joined'] = ''
        return state

    # 调用 Langfuse 集成函数获取索引（已解析）
    liability_indexes = extract_liability_index_with_langfuse(
        state['plan_keyword'],
        state['clause_keyword'],
        state['liability_keyword'],
        state.get('catalog_content', ''),
        session_id=state.get('session_id')
    )
    state['liability_indexes'] = liability_indexes

    if not liability_indexes:
        logger.warning('liability_indexes 为空，无法抽取责任条款正文')
        state['liability_clauses_joined'] = ''
        return state

    # 2. 使用抽象函数提取合并文本
    records: List[Dict[str, Any]] = state.get('markdown_chunks_with_idx_json', [])
    extract_result = extract_chunks_by_index(liability_indexes, records)
    merged_text = extract_result.get("text", "")
    state['liability_clauses_joined'] = merged_text

    return state


# ====== Step5: 基础赔付范围生成 ======
def generate_base_compensation(state: FlowState) -> FlowState:
    # 检查并处理每个必要参数
    liability_text = state.get('liability_clauses_joined', '')
    if not liability_text:
        logger.warning('生成基础情形时，未找到责任相关条款文本片段 liability_clauses_joined 为空，将使用"无"作为默认值')
        liability_text = '无'

    # hospital_text = state.get('selected_agreement_merged_text', '')
    # if not hospital_text:
    #     logger.warning('selected_agreement_merged_text 为空，将使用"无"作为默认值')
    #     hospital_text = '无'

    liability_keyword = state.get('plan_clause_liability_keyword', '')
    if not liability_keyword:
        logger.warning('plan_clause_liability_keyword 为空，将使用"无"作为默认值')
        liability_keyword = '无'

    session_id = state.get('session_id')
    if not session_id:
        logger.warning('session_id 为空，Langfuse tracing 可能无法正常工作')

    # 调用生成函数
    raw = generate_base_compensation_with_langfuse(
        liability_paragraph=liability_text,
        # hospital_paragraph=hospital_text,
        # liability_keyword 参数接收完整格式: "计划名称_条款名称_责任名称"
        liability_keyword=liability_keyword,
        session_id=session_id
    )

    state['base_compensation_raw'] = raw
    # 解析JSON结果
    state['base_compensation_json'] = json_repair.loads(raw)

    return state


# ====== Step6: 多情形赔付范围生成（修改基础） ======
def generate_multi_compensation(state: FlowState) -> FlowState:
    # 检查并处理每个必要参数
    base_json = state.get('base_compensation_json') or []
    base_json_str = json.dumps(base_json, ensure_ascii=False, indent=2)
    if not base_json:
        logger.warning('基础情形 base_compensation_json 为空，将使用空数组作为默认值')

    supplement_text = state.get('selected_agreement_merged_text', '')
    if not supplement_text:
        logger.warning('selected_agreement_merged_text 为空，将使用"无"作为默认值')
        supplement_text = '无'

    structure_tree = state.get('structure_tree_markdown_table', '')
    if not structure_tree:
        logger.warning('structure_tree_markdown_table 为空，将使用"无"作为默认值')
        structure_tree = '无'

    current_liability = state.get('plan_clause_liability_keyword', '')
    if not current_liability:
        logger.warning('plan_clause_liability_keyword 为空，将使用"无"作为默认值')
        current_liability = '无'

    session_id = state.get('session_id')
    if not session_id:
        logger.warning('session_id 为空，Langfuse tracing 可能无法正常工作')

    # 调用生成函数
    raw = generate_multi_compensation_with_langfuse(
        base_liability_json=base_json_str,
        supplement_text=supplement_text,
        structure_tree=structure_tree,
        # current_liability 参数接收完整格式: "计划名称_条款名称_责任名称"
        current_liability=current_liability,
        session_id=session_id
    )

    # 确保原始响应也被保存
    state['multi_compensation_raw'] = raw

    # 从 markdown JSON 代码块中提取 JSON 内容
    code_block_pattern = r'```(?:json)?\s*(.*?)\s*```'
    matches = re.findall(code_block_pattern, raw, re.DOTALL | re.IGNORECASE)
    if matches:
        # 使用第一个匹配的代码块内容
        json_to_parse = matches[0].strip()
    else:
        json_to_parse = raw

    state['multi_compensation_json'] = json_repair.loads(json_to_parse)

    return state


def generate_waiting_period(state: FlowState) -> FlowState:
    """
    等待期责任范围抽取和生成函数（整合原extract_waiting_period_scope节点）
    ---
    - 获取合并条款特别约定文本 + 向量召回文本
    - 拼接两部分文本进行一次性抽取
    - 调用模型抽取等待期内容
    - 生成等待期责任范围
    """
    plan_clause_liability_keyword = state.get('plan_clause_liability_keyword', '')
    if not plan_clause_liability_keyword:
        logger.warning('plan_clause_liability_keyword 为空，将使用"无"作为默认值')
        plan_clause_liability_keyword = '无'

    structure_tree_str = state.get('structure_tree_markdown_table', '')
    if not structure_tree_str:
        logger.warning('structure_tree_markdown_table 为空，将使用"无"作为默认值')
        structure_tree_str = '无'

    session_id = state.get('session_id')
    if not session_id:
        logger.warning('session_id 为空，Langfuse tracing 可能无法正常工作')

    # 1. 获取合并文本（整合自extract_waiting_period_scope）
    agreement_text = state.get('selected_agreement_merged_text', '') or ''
    if not agreement_text.strip():
        logger.warning("generate_waiting_period: '特别约定'召回为空，将仅使用'等待期'召回结果。")

    # 2. 从 state 中提取必要上下文
    policy_no = state.get('policy_no') or 'unknown'

    # 3. 使用抽象函数执行向量召回
    vector_result = retrieve_text_by_vector_search(
        policy_id=policy_no,
        query="等待期",
        query_type="等待期"
    )
    retrieved_text = vector_result.get("text", "")

    # 4. 合并两部分文本
    merged_lines = []


    # 6. retrieved_text 直接拼接进 merged_lines（不经过模型抽取）
    if retrieved_text.strip():
        # merged_lines.append("\n【等待期向量检索内容】")
        merged_lines.extend([l for l in retrieved_text.split('\n') if l.strip()])
        logger.info("generate_waiting_period: 等待期向量检索内容已添加。")

    # 5. 只对 agreement_text 调用模型进行抽取
    # plan_clause_liability_keyword加入抽取上下文
    if agreement_text.strip():
        try:
            raw = extract_waiting_period_scope_with_langfuse(agreement_text, plan_clause_liability_keyword, session_id=session_id)
            extracted = raw.strip()
            logger.info("generate_waiting_period: 特别约定等待期抽取完成。")
            if extracted and extracted != '无':
                merged_lines.extend([l for l in extracted.split('\n') if l.strip()])
                logger.info("generate_waiting_period: 特别约定等待期抽取内容已添加。")
        except Exception as e:
            logger.error(f"generate_waiting_period: 特别约定等待期抽取失败: {e}")

    if not merged_lines:
        logger.error("generate_waiting_period: 无任何可供抽取的文本内容。")
        state['waiting_period_scope_merged'] = ''
        state['waiting_period_json'] = []
        return state

    state['waiting_period_scope_merged'] = '\n'.join(merged_lines)

    # 7. 生成等待期责任范围
    result = generate_waiting_period_with_langfuse(
        waiting_period_text=state['waiting_period_scope_merged'],
        structure_tree=structure_tree_str,
        # current_liability 参数接收完整格式: "计划名称_条款名称_责任名称"
        current_liability=plan_clause_liability_keyword,
        session_id=session_id
    )
    state['waiting_period_json'] = result

    return state

def generate_past_illness(state: FlowState) -> FlowState:
    """
    既往症责任范围抽取和生成函数（整合原extract_past_illness_scope节点）
    ---
    - 获取合并条款特别约定文本 + 向量召回文本
    - 拼接两部分文本进行一次性抽取
    - 调用模型抽取既往症内容
    - 生成既往症责任范围
    """
    plan_clause_liability_keyword = state.get('plan_clause_liability_keyword', '')
    if not plan_clause_liability_keyword:
        logger.warning('plan_clause_liability_keyword 为空，将使用"无"作为默认值')
        plan_clause_liability_keyword = '无'

    structure_tree_str = state.get('structure_tree_markdown_table', '')
    if not structure_tree_str:
        logger.warning('structure_tree_markdown_table 为空，将使用"无"作为默认值')
        structure_tree_str = '无'

    session_id = state.get('session_id')
    if not session_id:
        logger.warning('session_id 为空，Langfuse tracing 可能无法正常工作')

    # 1. 获取合并文本（整合自extract_past_illness_scope）
    agreement_text = state.get('selected_agreement_merged_text', '') or ''
    if not agreement_text.strip():
        logger.warning("generate_past_illness: '特别约定'召回为空，将仅使用'既往症'向量召回结果。")

    # 2. 从 state 中提取上下文
    policy_no = state.get('policy_no') or 'unknown'

    # 3. 使用抽象函数执行向量召回
    vector_result = retrieve_text_by_vector_search(
        policy_id=policy_no,
        query="既往症",
        query_type="既往症"
    )
    retrieved_text = vector_result.get("text", "")

    # 4. 合并两部分文本
    agreement_text_display = "特别约定文本：\n" + agreement_text.strip() if agreement_text.strip() else '无'
    if retrieved_text and agreement_text_display:
        past_illness_text = retrieved_text + "\n\n" + agreement_text_display
    else:
        past_illness_text = retrieved_text or agreement_text_display

    if not past_illness_text.strip():
        logger.error("generate_past_illness: 无任何可供抽取的文本内容。")
        state['past_illness_scope_merged'] = ''
        state['past_illness_json'] = []
        return state

    # 5. 直接将合并后的原始文本存入 state（跳过 extraction 步骤）
    state['past_illness_scope_merged'] = past_illness_text
    logger.info(f"generate_past_illness: 使用原始合并文本，长度 = {len(past_illness_text)}")

    # 6. 生成既往症责任范围
    result = generate_past_illness_with_langfuse(
        past_illness_text=past_illness_text,
        structure_tree=structure_tree_str,
        # current_liability 参数接收完整格式: "计划名称_条款名称_责任名称"
        current_liability=plan_clause_liability_keyword,
        session_id=session_id
    )
    state['past_illness_json'] = result

    return state


# ================== 通用向量召回抽象函数 ==================
def retrieve_text_by_vector_search(
        policy_id: str,
        query: str,
        query_type: str
) -> Dict[str, Any]:
    """
    通用向量召回函数 - 执行向量检索并返回拼接好的文本，带标记

    Args:
        policy_id: 保单ID，用于追踪和定位向量库
        query: 查询词
        query_type: 查询类型（如"特别约定", "等待期", "既往症"）

    Returns:
        Dict[str, Any]: 包含以下字段的字典：
            - "text": str, 拼接好的召回文本，带【向量检索内容】标记和结构路径信息
            - "items": List[Dict]，每个字典包含：
                - "index": int, chunk 的索引（从 metadata.original_index 获取）
                - "content": str, chunk 的文本内容
                - "structure_path": str, 结构路径（从 metadata.structure_path 获取）
        向量库通过 PolicyVectorStoreManager 根据 policyId 自动定位。
    """
    # 构建输入状态 - RetrieveTextState 使用 policyId 自动定位向量库
    input_state = RetrieveTextState(
        policyId=policy_id,
        query=query,
        queryType=query_type,
        # 以下是 RetrieveTextState 必需的字段初始化
        matchedFiles=[],
        localVsPath='',
        results=[],
        code='200',
        message=''
    )

    try:
        # 构建并执行图
        graph = build_retrieve_text_graph()
        result_state = graph.invoke(input_state)

        # 提取召回结果 - 注意：使用 results 字段
        related_chunks = result_state.get("results", []) if result_state else []
        if not isinstance(related_chunks, list):
            logger.warning("retrieve_text_by_vector_search: results 结构异常，强制置为空列表")
            related_chunks = []

        logger.info(f"retrieve_text_by_vector_search: '{query_type}' 召回成功，共 {len(related_chunks)} 条结果")

        # 拼接文本，带标记
        # 向量检索返回的结构: {"text": ..., "metadata": {...}, "score": ..., "sources": [...]}
        items = []
        if related_chunks:
            content_parts = []
            for i, item in enumerate(related_chunks):
                if not isinstance(item, dict):
                    continue
                metadata = item.get('metadata', {})
                structure_path = metadata.get('structure_path', '')
                text_content = item.get('text') or item.get('page_content') or item.get('content', '')

                content_parts.append(
                    f"- 第{i + 1}段文本："
                    f"文本来源：{structure_path}\n"
                    f"{text_content}"
                )

                # 提取 original_index 作为 chunk 索引
                original_index = metadata.get('original_index')
                if original_index is not None:
                    try:
                        chunk_index = int(original_index)
                    except (ValueError, TypeError):
                        chunk_index = None
                else:
                    chunk_index = None

                items.append({
                    "index": chunk_index,
                    "content": text_content,
                    "structure_path": structure_path
                })

            content = "\n".join(content_parts).strip()
            text_result = f"【'{query}'的向量检索内容】\n\n{content}" if content else ""

            return {
                "text": text_result,
                "items": items
            }

        return {"text": "", "items": []}

    except Exception as e:
        logger.error(f"retrieve_text_by_vector_search: '{query_type}' 召回失败: {e}")
        return {"text": "", "items": []}

def plan_list_to_markdown_table(plan_list: List[Any]) -> str:
    """
    将计划列表(List[Plan])转换为 Markdown 表格格式
    输入: List[Plan] - 计划列表
    返回: Markdown 表格字符串
    逻辑:
      1. 提取所有行数据。
      2. 如果行数超过20，则按条款名称去重（保留首个）。
      3. 如果发生了去重（即有数据被省略），在文首提示并在表尾加省略号。
    """
    if not plan_list:
        return "无计划数据"

    raw_rows = []
    for plan in plan_list:
        plan_name = plan.planName
        for clause in plan.clauseList:
            clause_name = clause.clauseName
            responsibilities = [
                (liability.liabName or liability.liabilityName or "-")
                for liability in clause.liabilityList
                if liability.liabName or liability.liabilityName
            ]
            resp_str = "<br>".join(responsibilities) if responsibilities else "-"

            raw_rows.append({
                "plan": plan_name,
                "clause": clause_name,
                "resp": resp_str
            })

    final_rows = raw_rows
    has_skipped = False

    if len(raw_rows) > 20:
        deduplicated_rows = []
        seen_clauses = set()

        for row in raw_rows:
            if row["clause"] not in seen_clauses:
                deduplicated_rows.append(row)
                seen_clauses.add(row["clause"])

        if len(deduplicated_rows) < len(raw_rows):
            final_rows = deduplicated_rows
            has_skipped = True

    lines = []
    if has_skipped:
        lines.append(
            "> **注：由于表格行数过多，已按条款名称进行去重处理，仅展示每个条款的首条记录。**\n")

    lines.append("| 计划 | 条款 | 责任 |")
    lines.append("|---|---|---|")

    for row in final_rows:
        lines.append(f"| {row['plan']} | {row['clause']} | {row['resp']} |")

    if has_skipped:
        lines.append("| ... | ... | ... |")

    return "\n".join(lines)


def plan_dto_list_to_markdown_table(plan_dto_list: List[Any]) -> str:
    """
    将PlanDto列表转换为 Markdown 表格格式

    输入: List[PlanDto] - PlanDto对象列表
    返回: Markdown 表格字符串

    PlanDto结构特点：
    - 每个PlanDto已经包含了计划、条款和责任的完整信息
    - 结构扁平化，不再嵌套

    逻辑:
      1. 提取所有行数据，每个PlanDto生成一行
      2. 如果行数超过20，则按条款名称去重（保留首个）
      3. 如果发生了去重（即有数据被省略），在文首提示并在表尾加省略号
    """
    if not plan_dto_list:
        return "无计划数据"

    # 1. 提取所有原始数据
    raw_rows = []
    for plan_dto in plan_dto_list:
        # 提取计划名称
        plan_name = getattr(plan_dto, 'planName', '') or '-'

        # 提取条款名称
        clause_name = getattr(plan_dto, 'clauseName', '') or '-'

        # 提取责任列表
        liability_list = getattr(plan_dto, 'liabilityList', [])

        # 提取责任名称并处理为空的情况
        responsibilities = []
        if liability_list:
            for liability in liability_list:
                liab_name = getattr(liability, 'liabName', '') or getattr(liability, 'liabilityName', '')
                if liab_name:
                    responsibilities.append(liab_name)

        resp_str = "<br>".join(responsibilities) if responsibilities else "-"

        raw_rows.append({
            "plan": plan_name,
            "clause": clause_name,
            "resp": resp_str
        })

    # 2. 处理去重逻辑
    final_rows = raw_rows
    has_skipped = False  # 标记是否触发了"跳过去重"

    if len(raw_rows) > 20:
        deduplicated_rows = []
        seen_clauses = set()

        for row in raw_rows:
            if row["clause"] not in seen_clauses:
                deduplicated_rows.append(row)
                seen_clauses.add(row["clause"])

        # 只有当去重后的数量确实少于原始数量时，才认为进行了"跳过"操作
        if len(deduplicated_rows) < len(raw_rows):
            final_rows = deduplicated_rows
            has_skipped = True

    # 3. 生成 Markdown 内容
    lines = []

    # 如果有跳过，添加头部说明
    if has_skipped:
        lines.append(
            "> **注：由于表格行数过多，已按条款名称进行去重处理，仅展示每个条款的首条记录。**\n")

    # 表头
    lines.append("| 计划 | 条款 | 责任 |")
    lines.append("|---|---|---|")

    # 表内容
    for row in final_rows:
        lines.append(f"| {row['plan']} | {row['clause']} | {row['resp']} |")

    # 如果有跳过，添加底部省略号行
    if has_skipped:
        lines.append("| ... | ... | ... |")

    return "\n".join(lines)


def run_flow(catalog_md: str,
             markdown_chunks_with_idx_json: str,
             plan_clause_liability_keyword: str,
             structure_tree_markdown_table: str,
             policy_no: str,
             org_code: str):
    from langfuse import propagate_attributes
    from repositories.langfuse_integration import langfuse_client

    app = build_graph()
    # Set sessionId and tags for Langfuse tracing
    tags = []
    if policy_no:
        tags.append(f"policy_no:{policy_no}")
    if plan_clause_liability_keyword:
        tags.append(f"plan_clause_liability_keyword:{plan_clause_liability_keyword}")

    # Generate session_id for Langfuse tracing

    session_id = generate_session_id_with_timestamp(
        policy_no=policy_no,
        plan_clause_liability_keyword=plan_clause_liability_keyword
    )
    logger.info(f"Langfuse trace sessionId设置为: {session_id}, tags: {tags}")

    # Prepare initial state
    initial: FlowState = {
        'catalog_md_path': catalog_md,
        'markdown_chunks_with_idx_json_path': markdown_chunks_with_idx_json,
        'plan_clause_liability_keyword': plan_clause_liability_keyword,
        'structure_tree_markdown_table': structure_tree_markdown_table,
        'policy_no': policy_no,
        'session_id': session_id,  # 添加planNo到FlowState
        'org_code': org_code,  # 添加org_code到FlowState
    }

    # Use propagate_attributes context manager to set session_id and tags
    # This properly propagates attributes to all child spans within the context
    if langfuse_client and session_id:
        with propagate_attributes(session_id=session_id, tags=tags):
            final_state = app.invoke(initial)# type: ignore

            # Capture Langfuse trace ID and observation ID inside the context
            # Only attempt to get IDs when there's an active span context
            try:
                from opentelemetry import trace as otel_trace
                current_span = otel_trace.get_current_span()
                # Only proceed if there's an active recording span
                if current_span.is_recording():
                    trace_id = langfuse_client.get_current_trace_id()
                    observation_id = langfuse_client.get_current_observation_id()
                    if trace_id:
                        final_state['traceId'] = trace_id
                    if observation_id:
                        final_state['observationId'] = observation_id
                    logger.info(
                        f"已添加 Langfuse IDs 到 final_state - traceId: {trace_id}, observationId: {observation_id}")
            except Exception as e:
                logger.warning(f"无法获取 Langfuse trace/observation ID: {e}")
    else:
        # Fallback when Langfuse is not enabled
        final_state = app.invoke(initial)# type: ignore

    return final_state
