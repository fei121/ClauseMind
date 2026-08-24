import json
import traceback
from concurrent.futures import as_completed
from typing import Any, Dict, List, Optional, Union

from models.oldpydantic.request import Plan
from models.pydantic.request import PlanDto
from utils import logger
from infrastructure.thread_pool_manager import get_thread_pool
from workflows.policy_disassembly.extract_general_audit_items import (
    run_flow,
    plan_dto_list_to_markdown_table,
    plan_list_to_markdown_table,
)
from workflows.policy_disassembly.code_parsers import (
    convert_multi_scope_to_pay_scope_dto_list,
    convert_pay_param_to_rule_dto_list,
    convert_to_non_responsibility_dto_list
)


def process_plans_parallel_deconstruction(
        markdown_catalog_with_idx: Dict[str, str],
        plan_list: List[PlanDto],
        policy_no: str,
        org_code: Optional[str] = None
):
    """
    使用线程池并行处理计划拆解任务 (已移除中间包装函数)
    """
    sep = "_"
    # 获取包含codes的字典映射
    plan_clause_liability_with_codes_dict = flatten_product_plan_clause_liability_with_codes(plan_list, sep)

    # 生成结构树markdown表格（使用新的PlanDto专用函数）
    structure_tree_markdown_table = plan_dto_list_to_markdown_table(plan_list)

    plan_results = {}
    middle_results_dict = {}

    # 使用全局线程池
    executor = get_thread_pool("llm_cpu")
    # 建立 future 到 keyword 的映射，以便后续追踪是哪个任务完成
    future_to_keyword = {}

    # 1. 提交任务：直接调用 plan_kb
    for plan_index, (_, plan_clause_liability_keyword) in enumerate(plan_clause_liability_with_codes_dict.items()):
        # 注意：这里参数名需要与 plan_kb 定义的参数名一致，或者按顺序传参
        future = executor.submit(
            plan_kb,
            markdown_catalog_with_idx=markdown_catalog_with_idx,
            structure_tree_markdown_table=structure_tree_markdown_table,
            plan_clause_liability_keyword=plan_clause_liability_keyword,
            plan_index=plan_index,
            policy_no=policy_no,
            org_code=org_code
        )
        future_to_keyword[future] = plan_clause_liability_keyword

    # 2. 获取并处理结果
    for future in as_completed(future_to_keyword):
        keyword = future_to_keyword[future]
        try:
            # 获取 plan_kb 的返回值（final_state）
            final_state = future.result()

            if final_state:
                # 在上层进行格式化处理（使用deconstruction版本）
                formatted_result, middle_results = format_kb_results_to_legacy_format_deconstruction(final_state, keyword)
                plan_results[keyword] = formatted_result
                middle_results_dict[keyword] = middle_results
            else:
                # plan_kb 内部发生异常或返回 None
                logger.warning(f"Empty result for keyword={keyword}")
                plan_results[keyword] = {}

        except Exception as e:
            # 极其罕见的情况（例如 OOM 或 系统错误），因为 plan_kb 内部已经 catch 了大部分异常
            logger.error(f"Critical execution error for keyword={keyword}: {e}")
            plan_results[keyword] = {}

    return plan_results, middle_results_dict


def process_plans_parallel(
        markdown_catalog_with_idx: Dict[str, str],
        plan_list: List[Plan],
        policy_no: str,
        org_code: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """
    使用线程池并行处理计划拆解任务（旧接口）
    """
    sep = "_"
    plan_clause_liability_with_codes_dict = flatten_plan_clause_liability_with_codes(plan_list, sep)
    structure_tree_markdown_table = plan_list_to_markdown_table(plan_list)

    plan_results = {}

    # 使用全局线程池
    executor = get_thread_pool("llm_cpu")
    future_to_keyword = {}

    for plan_index, (_, keyword) in enumerate(plan_clause_liability_with_codes_dict.items()):
        future = executor.submit(
            plan_kb,
            markdown_catalog_with_idx=markdown_catalog_with_idx,
            structure_tree_markdown_table=structure_tree_markdown_table,
            plan_clause_liability_keyword=keyword,
            plan_index=plan_index,
            policy_no=policy_no,
            org_code=org_code
        )
        future_to_keyword[future] = keyword

    for future in as_completed(future_to_keyword):
        keyword = future_to_keyword[future]
        try:
            final_state = future.result()

            if final_state:
                formatted_result = format_kb_results_to_legacy_format(final_state, keyword)
                plan_results[keyword] = formatted_result
            else:
                logger.warning(f"Empty result for keyword={keyword}")
                plan_results[keyword] = {}

        except Exception as e:
            logger.error(f"Critical execution error for keyword={keyword}: {e}")
            plan_results[keyword] = {}

    return plan_results


def plan_kb(
        markdown_catalog_with_idx: Dict[str, str],
        structure_tree_markdown_table: str,
        plan_clause_liability_keyword: str,
        plan_index: int,
        policy_no: str,
        org_code: Optional[str] = None
) -> Optional[Dict]:
    """
    KB项目的计划拆解核心逻辑

    参数说明：
    - structure_tree_markdown_table: 计划结构树的markdown表格字符串（已在上层函数中生成）
    """
    log_prefix = f'KB项目条款拆解: policyNo-{policy_no} 第{plan_index + 1}份责任：{plan_clause_liability_keyword}'
    logger.info(f'{log_prefix}拆解开始')

    try:
        if not (markdown_catalog_with_idx
                and 'catalog_md_path' in markdown_catalog_with_idx
                and 'markdown_chunks_with_idx_json_path' in markdown_catalog_with_idx):
            logger.error(f'{log_prefix} 结构化数据缺少必要文件路径，无法进行拆解')
            return None

        # 直接调用 run_flow 获取 final_state，避免中间包装
        final_state = run_flow(
            catalog_md=markdown_catalog_with_idx['catalog_md_path'],
            markdown_chunks_with_idx_json=markdown_catalog_with_idx['markdown_chunks_with_idx_json_path'],
            structure_tree_markdown_table=structure_tree_markdown_table,
            plan_clause_liability_keyword=plan_clause_liability_keyword,
            policy_no=policy_no,
            org_code=org_code,
        )

        logger.info(f'{log_prefix}拆解成功')
        return final_state

    except Exception as e:
        logger.error(f'{log_prefix}拆解失败 - {str(e)}')
        traceback.print_exc()
        return None


def format_kb_results_to_legacy_format_deconstruction(final_state: Dict, plan_clause_liability_keyword: str):
    """
    将KB项目的结果格式化为原有rag_agent的输出格式

    Args:
        final_state: run_flow 返回的最终状态字典
        plan_clause_liability_keyword: 计划条款责任关键词

    参数示例：
    waiting_period_json = {
      "新保等待期": "30天"
    }
    past_illness_json = {
    "本次处理的保险责任": "疾病身故保险金",
    "本次处理的保险责任的既往症赔付参数": "承担一般既往症但不承担严重既往症",
    "严重既往症范围": "恶性肿瘤、心脏病（心功能不全Ⅱ级（含）以上）、..."
    }
    pay_scope = [
        {
          "事故类型": "疾病",
          "治疗类型": "住院治疗",
          "医院范围": "二级及以上公立医院",
          "费用类型": "所有",
          "发票": "原始发票",
          "场景": "一般疾病住院"
        }
    ]
    """

    # 获取字段级置信度评估结果
    confidence_eval_result = final_state.get('confidence_evaluation_result', {})

    # 赔付范围 - 转换为PayScopeDto列表
    pay_scope_list = convert_multi_scope_to_pay_scope_dto_list(
        final_state.get('base_compensation_json'),
        final_state.get('multi_compensation_json'),
        confidence_eval_result,
        session_id=final_state.get('session_id')
    )

    # 等待期和既往症 - 转换为RuleDto列表
    pay_param_rules = convert_pay_param_to_rule_dto_list(
        final_state.get('waiting_period_json'),
        final_state.get('past_illness_json'),
        confidence_eval_result
    )

    # 责任免除 - 转换为NonResponsibilityDto列表
    discern_result = final_state.get('responsibility_discern_result', {})
    non_responsibility_list = convert_to_non_responsibility_dto_list(
        discern_result.get('nonResponsibilityList', [])
    )

    return (
    # 最终输出结果
    {
        "plan_name": plan_clause_liability_keyword,
        "pay_scope": pay_scope_list,
        "factor": [],
        "pay_param": pay_param_rules,
        "nonResponsibilityList": non_responsibility_list,
        "healthNoticeList": discern_result.get('healthNoticeList', []),
        "scope_prompt": f"基于KB项目处理的{plan_clause_liability_keyword}相关条款",
        "traceId": final_state.get('traceId'),
        "observationId": final_state.get('observationId'),
        # 新增：置信度评估结果（字段级详细结果）
        "confidenceEvaluationResult": final_state.get('confidence_evaluation_result'),
        # Session ID 用于日志追踪
        "session_id": final_state.get('session_id'),
    },
    # 中间结果：落库demo_disassemble_service_middle_info，用于debug
    {
        # 拆解的原始数据
        "multi_compensation_json": final_state.get('multi_compensation_json'),
        "waiting_period_json": final_state.get('waiting_period_json'),
        "past_illness_json": final_state.get('past_illness_json'),
        "responsibility_discern_result": final_state.get('responsibility_discern_result'),
        # 置信度评估结果
        "confidenceEvaluationResult": final_state.get('confidence_evaluation_result'),
        # 跟踪信息
        "session_id": final_state.get('session_id'),
        "traceId": final_state.get('traceId'),
        "observationId": final_state.get('observationId'),
    })


def format_kb_results_to_legacy_format(final_state: Dict, plan_clause_liability_keyword: str) -> Dict:
    """
    将KB项目的结果格式化为旧接口输出格式
    """
    pay_scope = format_scope_from_kb_data(
        final_state.get('base_compensation_json'),
        final_state.get('multi_compensation_json')
    )

    pay_param_parts = []
    if waiting_period := final_state.get('waiting_period_json'):
        pay_param_parts.append(format_waiting_period_from_kb_data(waiting_period))

    if past_illness := final_state.get('past_illness_json'):
        pay_param_parts.append(format_past_illness_from_kb_data(past_illness))

    discern_result = final_state.get('responsibility_discern_result', {})

    return {
        "plan_name": plan_clause_liability_keyword,
        "scope": pay_scope,
        "factor": [],
        "pay_param": "；".join(pay_param_parts),
        "scope_prompt": f"基于KB项目处理的{plan_clause_liability_keyword}相关条款",
        "nonResponsibilityList": discern_result.get('nonResponsibilityList', []),
        "healthNoticeList": discern_result.get('healthNoticeList', []),
        "traceId": final_state.get('traceId'),
        "observationId": final_state.get('observationId')
    }


def format_scope_from_kb_data(base_data: Union[Dict, List, None], multi_data: Union[Dict, List, None]) -> str:
    def _ensure_list(x):
        return x if isinstance(x, list) else ([x] if x else [])

    raw_items = _ensure_list(base_data) + _ensure_list(multi_data)

    if not raw_items:
        return "拆解值为空，未能从文档中提取到赔付范围"

    unique_scope_map = {}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            unique_scope_map[key] = item
        except Exception:
            unique_scope_map[f"_{id(item)}"] = item

    scope_items = list(unique_scope_map.values())
    target_keys = ['事故类型', '治疗类型', '医院范围', '费用类型', '发票', '场景']
    defaults = {'费用类型': '所有'}

    output_lines = ["满足以下任一情形可以赔付："]
    for i, item in enumerate(scope_items, 1):
        parts = []
        for key in target_keys:
            val = item.get(key) or defaults.get(key)
            if val:
                parts.append(f"【{key}：{val}】")
        if parts:
            output_lines.append(f"({i}){' 且 '.join(parts)}")

    return "\n".join(output_lines) if len(output_lines) > 1 else "拆解数据格式异常，未找到有效的赔付范围信息"


def format_waiting_period_from_kb_data(waiting_period_items: Dict) -> str:
    if val := waiting_period_items.get('新保等待期'):
        return f"新保等待期{val}"
    return ""


def format_past_illness_from_kb_data(past_illness_items: Dict) -> str:
    parts = []
    for key, val in past_illness_items.items():
        if "赔付参数" in key and val:
            parts.append(str(val))
    for key, val in past_illness_items.items():
        if ("严重既往症" in key or "严重既往症范围" in key) and val:
            parts.append(f"不承担以下严重既往症：{val}")
    return "，".join(parts)

def format_scope_from_kb_data_deconstruction(base_data: Union[Dict, List, None], multi_data: Union[Dict, List, None]) -> dict:
    def _ensure_list(x):
        return x if isinstance(x, list) else ([x] if x else [])

    raw_items = _ensure_list(base_data) + _ensure_list(multi_data)

    if not raw_items:
        logger.warning("拆解值为空，未能从文档中提取到赔付范围")
        return {}

    unique_scope_map = {}
    for item in raw_items:
        if not isinstance(item, dict): continue
        try:
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            unique_scope_map[key] = item
        except Exception as e:
            logger.warning(f"赔付范围项 JSON 序列化失败，使用 id 作为 key: {e}")
            unique_scope_map[f"_{id(item)}"] = item

    return unique_scope_map


def flatten_plan_clause_liability_with_codes(plan_list: List[Plan], sep: str = "_") -> Dict[str, str]:
    """
    扩展版本：将计划_条款_责任结构扁平化，返回字典映射（旧接口）
    - 键：使用 oldpydantic 模型中的 "{planNo}_{clauseCode}_{liabCode}"
    - 值：原来的列表元素 "{planName}_{clauseName}_{liabName}"
    """
    result: Dict[str, str] = {}

    for plan in plan_list:
        plan_name = plan.planName.strip()
        plan_no = plan.planNo.strip()

        for clause in plan.clauseList:
            clause_name = clause.clauseName.strip()
            clause_code = (clause.clauseCode or "").strip()

            for liability in clause.liabilityList:
                liab_name = (liability.liabName or "").strip()
                liab_code = (liability.liabCode or "").strip()

                key = f"{plan_no}{sep}{clause_code}{sep}{liab_code}"
                value = f"{plan_name}{sep}{clause_name}{sep}{liab_name}"

                result[key] = value

    return result

def flatten_product_plan_clause_liability_with_codes(plan_list: List[PlanDto], sep: str = "_") -> Dict[str, str]:
    """
    扩展版本：处理 models/pydantic/request.py 中的新结构，将计划_条款_责任结构扁平化
    - 键：使用新模型中的 "{planCode}_{clauseCode}_{liabCode}"
    - 值：列表元素 "{planVersion}_{clauseName}_{liabName}"

    新模型结构特点：
    - PlanDto 包含: id, planCode, planVersion, clauseCode, clauseName, liabilityList
    - LiabilityDto 包含: id, liabCode, liabName

    如果任何字段缺少，将用空字符串替代
    """
    result: Dict[str, str] = {}

    for plan in plan_list:
        plan_name = (plan.planName or "").strip()
        plan_code = (plan.planCode or "").strip()
        clause_name = (plan.clauseName or "").strip()
        clause_code = (plan.clauseCode or "").strip()

        for liability in plan.liabilityList:
            liab_name = (liability.liabName or "").strip()
            liab_code = (liability.liabCode or "").strip()

            # 构建键和值
            key = f"{plan_code}{sep}{clause_code}{sep}{liab_code}"
            value = f"{plan_name}{sep}{clause_name}{sep}{liab_name}"

            result[key] = value

    return result
