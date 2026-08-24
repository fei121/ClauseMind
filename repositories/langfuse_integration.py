"""
Langfuse 集成工具 - v3版本
用于实现LLM调用追踪、Prompt管理、评估系统
基于OpenTelemetry标准

注意：Langfuse SDK 日志级别通过环境变量 LANGFUSE_LOG_LEVEL 控制
(DEBUG/INFO/WARNING/ERROR)，无需在代码中手动设置
"""
import os
import re
import traceback
from datetime import datetime
from typing import Dict, Any, Optional, List, Union

from langfuse import Langfuse, get_client as get_langfuse_client_v3, propagate_attributes
from langfuse.langchain import CallbackHandler
from json_repair import json_repair

from utils.logger import logger as _logger

# 全局客户端实例
langfuse_client: Optional[Langfuse] = None
LANGFUSE_ENABLED = False

if os.environ.get("TRACING_METHOD") == "langfuse":
    try:
        # v3方式：使用环境变量配置，get_client自动初始化
        langfuse_client = get_langfuse_client_v3()

        # 验证连接
        if langfuse_client.auth_check():
            LANGFUSE_ENABLED = True
            _logger.info("Langfuse v3 客户端初始化成功 (基于OpenTelemetry)")
        else:
            _logger.error("Langfuse 连接验证失败，请检查环境变量配置")
            langfuse_client = None
            LANGFUSE_ENABLED = False

    except Exception as e:
        _logger.error(f"Langfuse 客户端初始化失败: {e}")
        langfuse_client = None
        LANGFUSE_ENABLED = False
else:
    _logger.info("TRACING_METHOD 未设置为 'langfuse'，跳过 Langfuse 客户端初始化")
    langfuse_client = None
    LANGFUSE_ENABLED = False



def get_langfuse_client():
    """
    获取 Langfuse 客户端单例

    Returns:
        Langfuse 客户端实例，如果未启用则返回 None
    """
    return langfuse_client if LANGFUSE_ENABLED else None

# 延迟导入，避免循环导入
def original_llm_call(prompt: str, model_name: Optional[str] = None, temperature: Optional[float] = None) -> str:
    from utils.llm import llm_call
    return llm_call(prompt, model_name, temperature)

# 从配置文件导入prompt配置（本地默认配置）
try:
    from .langfuse_prompts_config import ALL_PROMPTS as LOCAL_ALL_PROMPTS
except ImportError:
    _logger.warning("无法导入prompt配置，将使用默认配置")
    LOCAL_ALL_PROMPTS = {}

# prompts-mounted.py 文件路径配置
MOUNTED_PROMPTS_PATH = os.environ.get(
    "MOUNTED_PROMPTS_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts-mounted.py")
)

def _load_mounted_prompts() -> Dict[str, Any]:
    """
    动态加载 prompts-mounted.py 文件中的 ALL_PROMPTS
    绕过 Python import 缓存，实现实时更新

    Returns:
        Dict: prompts-mounted.py 中的 ALL_PROMPTS，如果加载失败返回空字典
    """
    import importlib.util

    if not os.path.exists(MOUNTED_PROMPTS_PATH):
        _logger.debug(f"prompts-mounted.py 文件不存在: {MOUNTED_PROMPTS_PATH}")
        return {}

    try:
        # 使用 spec_from_file_location 动态加载，避免 import 缓存
        spec = importlib.util.spec_from_file_location("prompts_mounted_dynamic", MOUNTED_PROMPTS_PATH)
        if spec is None or spec.loader is None:
            _logger.warning(f"无法创建 prompts-mounted.py 的模块规范")
            return {}

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, 'ALL_PROMPTS'):
            _logger.debug(f"成功从 prompts-mounted.py 动态加载 {len(module.ALL_PROMPTS)} 个 prompts")
            return module.ALL_PROMPTS
        else:
            _logger.warning(f"prompts-mounted.py 中未找到 ALL_PROMPTS")
            return {}
    except Exception as e:
        _logger.warning(f"动态加载 prompts-mounted.py 失败: {e}")
        return {}

def get_prompt_config(prompt_name: str) -> Optional[Dict[str, Any]]:
    """
    获取 prompt 配置，优先从 prompts-mounted.py 读取，失败则回退到本地配置

    Args:
        prompt_name: prompt 名称

    Returns:
        prompt 配置字典，如果未找到返回 None
    """
    # 优先从 mounted prompts 获取（实时加载）
    mounted_prompts = _load_mounted_prompts()
    if prompt_name in mounted_prompts:
        _logger.debug(f"从 发布平台的配置文件prompts-mounted.py 获取 prompt 配置: {prompt_name}")
        return mounted_prompts[prompt_name]

    # 回退到本地配置
    if prompt_name in LOCAL_ALL_PROMPTS:
        _logger.warning(f"未能从发布平台的配置文件prompts-mounted.py 获取 prompt，回退本地配置: {prompt_name}")
        return LOCAL_ALL_PROMPTS[prompt_name]

    return None

# 为了向后兼容，保留 ALL_PROMPTS 变量（但推荐使用 get_prompt_config 函数）
ALL_PROMPTS = LOCAL_ALL_PROMPTS

def flush_langfuse_client():
    """
    Flush Langfuse client to ensure all trace events are sent to the server.
    Should be called before application exit in short-lived applications.
    """
    try:
        langfuse_client = get_langfuse_client()
        if langfuse_client:
            langfuse_client.flush()
            _logger.info("Langfuse client flushed successfully")
    except Exception as e:
        _logger.warning(f"Failed to flush Langfuse client: {e}")


def _get_prompt_from_langfuse_or_local(
    prompt_name: str,
    langfuse_prompt_label: str,
    prompt_config: Optional[Dict[str, Any]]
) -> tuple[str, Any]:
    """
    从Langfuse获取prompt，失败则回退到本地配置

    Returns:
        tuple: (prompt_text, langfuse_prompt_obj)
    """
    from langchain_core.prompts import PromptTemplate
    import html

    langfuse_client = get_langfuse_client()
    langfuse_prompt_obj = None

    if langfuse_client and LANGFUSE_ENABLED:
        try:
            langfuse_prompt_obj = langfuse_client.get_prompt(prompt_name, label=langfuse_prompt_label)
            if langfuse_prompt_obj:
                _logger.info(f"从 Langfuse Prompt Management 获取 prompt: {prompt_name} (label: {langfuse_prompt_label})")
                template = html.unescape(langfuse_prompt_obj.prompt)
                prompt = PromptTemplate.from_template(template, template_format="mustache")
                return prompt, langfuse_prompt_obj
            else:
                raise ValueError(f"Langfuse prompt not found: {prompt_name}")
        except Exception as e:
            _logger.debug(f"Langfuse prompt '{prompt_name}' (label: {langfuse_prompt_label}) 未创建或获取失败，使用本地配置")
            _logger.debug(f"详细信息: {e}")

    # 回退到本地配置
    if prompt_config:
        template = prompt_config["template"]
        prompt = PromptTemplate.from_template(template, template_format="mustache")
        _logger.debug(f"使用本地 prompt 配置: {prompt_name}")
        return prompt, None
    else:
        raise ValueError(f"未找到 prompt 配置: {prompt_name}")


def _call_llm_model(prompt: Any, model_name: Optional[str], **kwargs) -> tuple[str, dict]:
    """
    调用LLM模型

    Returns:
        tuple: (result, usage_info)
    """
    from utils.llm import GatewayLLM
    from langchain_core.messages import HumanMessage

    # 创建 LLM model
    model = GatewayLLM(engine=model_name) if model_name else GatewayLLM()

    # 格式化 prompt
    prompt_text = prompt.format(**kwargs)

    # 直接调用模型
    messages = [HumanMessage(content=prompt_text)]
    response = model.invoke(messages)

    # 确保结果是字符串格式
    if hasattr(response, 'content'):
        result = response.content
    else:
        result = str(response)

    # 获取usage信息
    usage_info = model.last_usage if hasattr(model, 'last_usage') and model.last_usage else {}

    return result, usage_info


def _update_langfuse_generation(
    generation: Any,
    result: str,
    prompt_text: str,
    raw_inputs: Dict[str, Any],
    usage_info: dict,
    model_name: Optional[str],
    prompt_name: str,
    langfuse_prompt_label: str,
    langfuse_prompt_obj: Any,
    session_id: Optional[str],
    user_id: Optional[str],
    metadata: Optional[Dict[str, Any]]
):
    """
    更新Langfuse generation observation
    """
    from utils.llm import calculate_cost_from_usage

    # 准备usage_details
    usage_details = {}
    if usage_info:
        if "prompt_tokens" in usage_info:
            usage_details["input"] = usage_info["prompt_tokens"]
        if "completion_tokens" in usage_info:
            usage_details["output"] = usage_info["completion_tokens"]
        if "total_tokens" in usage_info:
            usage_details["total"] = usage_info["total_tokens"]

    # 计算cost_details
    cost_details = calculate_cost_from_usage(usage_info, model_name or "unknown") if usage_info else None

    # 准备元数据
    observation_metadata = {
        "prompt_name": prompt_name,
        "prompt_label": langfuse_prompt_label,
        "raw_inputs": raw_inputs,
    }
    if metadata:
        observation_metadata.update(metadata)
    if session_id:
        observation_metadata["session_id"] = session_id
    if user_id:
        observation_metadata["user_id"] = user_id

    # 构建更新参数
    # 注意：input 直接使用 formatted_prompt，使 playground 可以正确识别
    update_params = {
        "name": prompt_name,
        "input": prompt_text,  # 直接使用格式化后的 prompt
        "output": result,
        "metadata": observation_metadata,
    }

    if model_name:
        update_params["model"] = model_name
    if usage_details:
        update_params["usage_details"] = usage_details
    if cost_details:
        update_params["cost_details"] = cost_details
    if langfuse_prompt_obj:
        update_params["prompt"] = langfuse_prompt_obj

    # 更新generation
    generation.update(**update_params)
    _logger.info(f"已更新 Langfuse observation: prompt_name={prompt_name}, input_len={len(prompt_text)}, output_len={len(result)}")
    if usage_details:
        _logger.info(f"已设置 usage_details: {usage_details}, cost_details: {cost_details}")


def _handle_llm_call_error(
    error: Exception,
    prompt_name: str,
    model_name: Optional[str],
    session_id: Optional[str],
    user_id: Optional[str],
    **kwargs
) -> str:
    """
    处理LLM调用错误，回退到原始调用
    """
    error_type = type(error).__name__
    error_traceback = traceback.format_exc()
    _logger.warning(f"Langfuse 调用失败 - 详细信息:")
    _logger.warning(f"  错误类型: {error_type}")
    _logger.warning(f"  错误消息: {error}")
    _logger.warning(f"  Prompt名称: {prompt_name}")
    _logger.warning(f"  模型名称: {model_name}")
    _logger.warning(f"  会话ID: {session_id}")
    _logger.warning(f"  用户ID: {user_id}")
    _logger.warning(f"  完整堆栈跟踪:\n{error_traceback}")
    _logger.warning(f"回退到原始调用")

    # 最后的回退：简单拼接
    prompt_text = " ".join([f"{k}: {v}" for k, v in kwargs.items()])
    return original_llm_call(prompt_text, model_name)


# 默认的llmcall的函数
def llm_call_with_langfuse(
    prompt_name: str,
    model_name: Optional[str] = "qwen-deepseek-v3.2",
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    langfuse_prompt_label: Optional[str] = None,
    **kwargs
) -> str:
    # 获取 prompt 配置（优先从 prompts-mounted.py 读取）
    prompt_config = get_prompt_config(prompt_name)

    # 确定 Langfuse prompt 标签
    if langfuse_prompt_label is None:
        langfuse_prompt_label = os.environ.get("LANGFUSE_PROMPT_LABEL", "latest")

    try:
        langfuse_client = get_langfuse_client()

        if LANGFUSE_ENABLED and langfuse_client:
            # 使用 propagate_attributes 确保 session_id 和 user_id 被正确传递到子 observation
            with propagate_attributes(session_id=session_id, user_id=user_id):
                # 使用 as_type='generation' 确保创建 generation 类型，以便正确关联 prompt
                with langfuse_client.start_as_current_observation(name=prompt_name, as_type='generation') as generation:
                    # 获取prompt
                    prompt, langfuse_prompt_obj = _get_prompt_from_langfuse_or_local(
                        prompt_name, langfuse_prompt_label, prompt_config
                    )

                    # 调用LLM
                    result, usage_info = _call_llm_model(prompt, model_name, **kwargs)

                    # 更新observation
                    _update_langfuse_generation(
                        generation=generation,
                        result=result,
                        prompt_text=prompt.format(**kwargs),
                        raw_inputs=kwargs,
                        usage_info=usage_info,
                        model_name=model_name,
                        prompt_name=prompt_name,
                        langfuse_prompt_label=langfuse_prompt_label,
                        langfuse_prompt_obj=langfuse_prompt_obj,
                        session_id=session_id,
                        user_id=user_id,
                        metadata=metadata
                    )

                    _logger.info(f"Langfuse 调用成功: {prompt_name}, model: {model_name}")
                    return result
        else:
            # Langfuse 未启用，直接调用LLM
            prompt, _ = _get_prompt_from_langfuse_or_local(
                prompt_name, langfuse_prompt_label, prompt_config
            )
            result, _ = _call_llm_model(prompt, model_name, **kwargs)
            return result

    except Exception as e:
        # 错误处理
        return _handle_llm_call_error(
            error=e,
            prompt_name=prompt_name,
            model_name=model_name,
            session_id=session_id,
            user_id=user_id,
            **kwargs
        )

# ================== extract_general_audit_items 专用函数 ==================

def extract_json_dict_from_response(raw_content: str) -> Dict[str, Any]:
    """
    从原始响应字符串中提取 JSON 字典对象。

    处理流程：
    1. 从 markdown JSON 代码块中提取内容（如果存在）
    2. 使用 json_repair 解析 JSON
    3. 如果是字典，直接返回
    4. 如果是列表，取最后一个元素（如果最后一个元素是字典）

    适用于处理 LLM 返回的混合格式，例如:
    - 正常: {"key": "value"}
    - markdown: ```json\n{"key": "value"}\n```
    - 列表: [..., {"key": "value"}]

    Args:
        raw_content: 原始响应字符串

    Returns:
        提取的字典对象，如果未找到返回空字典
    """
    if not raw_content or not isinstance(raw_content, str):
        return {}

    content = raw_content.strip()
    if not content:
        return {}

    # 步骤 1: 提取 markdown JSON 代码块
    code_block_pattern = r'```(?:json)?\s*(.*?)\s*```'
    matches = re.findall(code_block_pattern, content, re.DOTALL | re.IGNORECASE)
    if matches:
        content = matches[0].strip()

    # 步骤 2: 解析 JSON
    try:
        parsed = json_repair.loads(content)
    except Exception:
        return {}

    # 步骤 3: 提取字典
    if isinstance(parsed, dict):
        return parsed

    if isinstance(parsed, list) and parsed:
        # 取最后一个元素
        last_item = parsed[-1]
        if isinstance(last_item, dict):
            return last_item

    return {}


def extract_confidence_dict_from_parsed(parsed: Any) -> Dict[str, Any]:
    """
    从 json_repair 解析结果中提取目标字典对象。专用于正确提取置信度评估字典

    处理 LLM 返回带 markdown 代码块和说明文本的混合格式，例如:
    - 正常: {"医院范围": {...}, "发票": {...}}
    - 异常: [一些文本解析结果, {"医院范围": {...}, "发票": {...}}, 更多文本]
    - 嵌套: [[...], {"医院范围": {...}}]

    策略：
    1. 如果输入是字符串，先提取 JSON 代码块，然后解析
    2. 如果直接是字典，直接返回
    3. 如果是列表，遍历查找所有符合置信度评估结构的字典
    4. 验证字典结构：检查是否包含类似 score/reasoning 的嵌套字典值
    5. 返回最后一个符合条件的字典（通常是最终修正后的结果）

    Args:
        parsed: json_repair.loads() 的解析结果，或原始字符串

    Returns:
        目标字典对象，如果未找到返回空字典
    """
    def _is_confidence_eval_dict(obj: Any) -> bool:
        """检查是否为置信度评估结果字典结构"""
        if not isinstance(obj, dict) or not obj:
            return False
        # 检查字典的值是否包含 score 或 reasoning 字段（置信度评估结构特征）
        for v in obj.values():
            if isinstance(v, dict) and ('score' in v or 'reasoning' in v or 'matched_rule' in v):
                return True
        return False

    # 情况0: 如果是字符串，先提取 JSON 代码块并解析
    if isinstance(parsed, str):
        content = parsed.strip()
        if not content:
            return {}

        # 提取 markdown JSON 代码块
        code_block_pattern = r'```(?:json)?\s*(.*?)\s*```'
        matches = re.findall(code_block_pattern, content, re.DOTALL | re.IGNORECASE)

        if matches:
            # 使用第一个匹配的代码块内容
            content = matches[0].strip()

        # 解析 JSON
        try:
            parsed = json_repair.loads(content)
        except Exception:
            return {}

    # 情况1: 直接是字典格式
    if isinstance(parsed, dict):
        return parsed

    # 情况2: 是列表格式（LLM 返回了混合内容被解析为列表）
    if isinstance(parsed, list):
        last_match = None

        for item in parsed:
            # 直接是目标字典
            if _is_confidence_eval_dict(item):
                last_match = item
            # 嵌套列表中可能包含目标字典
            if isinstance(item, list):
                for sub_item in item:
                    if _is_confidence_eval_dict(sub_item):
                        last_match = sub_item

        if last_match is not None:
            return last_match

    return {}


def parse_and_extract_dict_list(content: str) -> List[Dict[str, Any]]:
    """
    从字符串内容中解析并提取包含字典的列表。

    处理步骤：
    1. 先查找 markdown JSON 代码块（```json...```），提取其中的内容
    2. 使用 json_repair.loads 解析 JSON
    3. 从解析结果中提取包含字典的列表（处理嵌套列表情况）

    处理 json_repair 解析混合格式文本时可能产生的嵌套列表情况，例如:
    - 正常: [{"index": 1}, {"index": 2}]
    - 嵌套: [[92], [{"index": 1}, {"index": 2}]]

    Args:
        content: 原始字符串内容（可能包含 markdown 代码块）

    Returns:
        包含字典的列表
    """
    from json_repair import json_repair

    items: List[Dict[str, Any]] = []

    if not content or not isinstance(content, str):
        return items

    content = content.strip()
    if not content:
        return items

    # 步骤 1: 提取 markdown JSON 代码块
    # 匹配 ```json ... ``` 或 ``` ... ``` 格式的代码块
    code_block_pattern = r'```(?:json)?\s*(.*?)\s*```'
    matches = re.findall(code_block_pattern, content, re.DOTALL | re.IGNORECASE)

    if matches:
        # 使用第一个匹配的代码块内容
        content = matches[0].strip()

    # 步骤 2: 解析 JSON
    try:
        parsed = json_repair.loads(content)
    except Exception:
        return items

    # 步骤 3: 提取包含字典的列表（原有逻辑）
    if not isinstance(parsed, list):
        return items

    if not parsed:
        return items

    # 情况 1: 直接是列表格式 [{...}, {...}]
    if isinstance(parsed[0], dict):
        return parsed

    # 情况 2: 嵌套列表格式 [[...], [{...}]]
    if isinstance(parsed[0], list):
        for sublist in parsed:
            if isinstance(sublist, list) and sublist and isinstance(sublist[0], dict):
                items = sublist
                break
        # 如果没找到包含 dict 的子列表，尝试最后一个元素
        if not items:
            last = parsed[-1]
            if isinstance(last, list):
                items = last

    return items


def extract_special_agreement_index_with_langfuse(index_content: str, session_id: Optional[str] = None) -> List[int]:
    """提取特别约定索引，返回解析后的整数列表"""

    result = llm_call_with_langfuse(
        "special_agreement_index",
        index_content=index_content,
        model_name="qwen-deepseek-v3.2",
        session_id=session_id,
        tags=["extraction", "agreement", "insurance"]
    )

    if not result:
        return []

    # 解析并提取字典列表
    items = parse_and_extract_dict_list(str(result))

    # 提取索引：保留全部合法整数 index，保持顺序，可含重复
    indexes: List[int] = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get('index'), int):
            indexes.append(it['index'])

    return indexes

def extract_liability_index_with_langfuse(plan: str, clause: str, liability: str, index_content: str, session_id: Optional[str] = None) -> List[int]:
    """提取保险责任索引，返回解析后的整数列表"""

    result = llm_call_with_langfuse(
        "liability_index_extraction",
        plan=plan,
        clause=clause,
        liability=liability,
        index_content=index_content,
        model_name="qwen-deepseek-v3.2",
        session_id=session_id,
        tags=["extraction", "liability", "insurance"]
    )

    if not result:
        return []

    # 解析并提取字典列表
    items = parse_and_extract_dict_list(str(result))

    # 提取索引：保留全部合法整数 index，保持顺序，可含重复
    indexes: List[int] = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get('index'), int):
            indexes.append(it['index'])

    return indexes

def extract_responsibility_discern_index_with_langfuse(clause: str, liability: str, catalog_content: str, session_id: Optional[str] = None) -> List[int]:
    """提取责任免除条款索引，返回解析后的整数列表"""

    result = llm_call_with_langfuse(
        "responsibility_discern_index",
        clause=clause,
        liability=liability,
        catalog_content=catalog_content,
        model_name="qwen-deepseek-v3.2",
        session_id=session_id,
        tags=["extraction", "responsibility", "insurance", "discernment"]
    )

    if not result:
        return []

    # 解析并提取字典列表
    items = parse_and_extract_dict_list(str(result))

    # 提取索引：保留全部合法整数 index，保持顺序，可含重复
    indexes: List[int] = []
    for it in items:
        if isinstance(it, dict) and isinstance(it.get('index'), int):
            indexes.append(it['index'])

    return indexes

def extract_waiting_period_scope_with_langfuse(block: str, structure_tree: str, session_id: Optional[str] = None) -> str:
    """提取等待期范围"""
    result = llm_call_with_langfuse(
        "waiting_period_extraction",
        block=block,
        structure_tree=structure_tree,
        model_name="qwen-deepseek-v3.2",
        session_id=session_id,
        tags=["extraction", "waiting_period", "insurance"]
    )
    return str(result).strip() if result is not None else ""

def extract_past_illness_scope_with_langfuse(block: str, structure_tree: str, session_id: Optional[str] = None) -> str:
    """提取既往症范围"""
    result = llm_call_with_langfuse(
        "past_illness_extraction",
        block=block,
        structure_tree=structure_tree,
        model_name="qwen-deepseek-v3.2",
        session_id=session_id,
        tags=["extraction", "past_illness", "insurance"]
    )
    return str(result).strip() if result is not None else ""

# TODO: 检查agent在graph的作用是否合理
def extract_multi_scenario_with_langfuse(block: str, session_id: Optional[str] = None) -> str:
    """提取多情形补充文本"""
    result = llm_call_with_langfuse(
        "multi_scenario_extraction",
        block=block,
        model_name="kimi/kimi-k2.5",
        session_id=session_id,
        tags=["extraction", "scenario", "insurance"]
    )
    return str(result).strip() if result is not None else ""

def generate_base_compensation_with_langfuse(
    liability_paragraph: str,
    liability_keyword: str,
    session_id: Optional[str] = None
) -> str:
    """生成基础赔付情形"""
    result = llm_call_with_langfuse(
        "base_compensation_generation",
        liability_paragraph=liability_paragraph,
        liability_keyword=liability_keyword,
        model_name="kimi/kimi-k2.5",
        session_id=session_id,
        tags=["generation", "compensation", "insurance"]
    )
    return str(result).strip() if result is not None else ""

def generate_multi_compensation_with_langfuse(
    base_liability_json: str,
    supplement_text: str,
    structure_tree: str,
    current_liability: str,
    session_id: Optional[str] = None
) -> str:
    """生成多情形赔付范围"""
    result = llm_call_with_langfuse(
        "multi_compensation_generation",
        base_liability_json=base_liability_json,
        supplement_text=supplement_text,
        structure_tree=structure_tree,
        current_liability=current_liability,
        model_name="kimi/kimi-k2.5",
        session_id=session_id,
        tags=["generation", "compensation", "insurance"]
    )
    return str(result).strip() if result is not None else ""

def generate_waiting_period_with_langfuse(
    waiting_period_text: str,
    structure_tree: str,
    current_liability: str,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """生成等待期要求，返回解析后的字典"""
    result = llm_call_with_langfuse(
        "waiting_period_generation",
        waiting_period_text=waiting_period_text,
        structure_tree=structure_tree,
        current_liability=current_liability,
        model_name="kimi/kimi-k2.5",
        session_id=session_id,
        tags=["generation", "waiting_period", "insurance"]
    )
    raw = str(result).strip() if result is not None else ""
    return extract_json_dict_from_response(raw)

def generate_past_illness_with_langfuse(
    past_illness_text: str,
    structure_tree: str,
    current_liability: str,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """生成既往症赔付参数，返回解析后的字典"""
    result = llm_call_with_langfuse(
        "past_illness_generation",
        past_illness_text=past_illness_text,
        structure_tree=structure_tree,
        current_liability=current_liability,
        model_name="kimi/kimi-k2.5",
        session_id=session_id,
        tags=["generation", "past_illness", "insurance"]
    )
    raw = str(result).strip() if result is not None else ""
    return extract_json_dict_from_response(raw)

def evaluate_result_confidence_with_langfuse(
    recall_context: str,
    ai_extraction_result: str,
    plan_clause_liability_keyword: str,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    使用 Langfuse 调用置信度评估 Prompt，返回解析后的字典

    Args:
        recall_context: 召回的上下文文本（特别约定 + 等待期 + 既往症）
        ai_extraction_result: AI 抽取的结果（多情形赔付范围 + 等待期 + 既往症）
        session_id: 会话ID，用于关联多个调用

    Returns:
        Dict[str, Any]: 解析后的置信度评估字典，包含 score 和 reasoning 等字段
    """
    result = llm_call_with_langfuse(
        "confidence_evaluation",
        model_name="kimi/kimi-k2.5",
        session_id=session_id,
        tags=["confidence_evaluation", "quality_check", "insurance"],
        recall_context=recall_context,
        ai_extraction_result=ai_extraction_result,
        plan_clause_liability_keyword=plan_clause_liability_keyword
    )
    raw = str(result).strip() if result is not None else ""
    return extract_confidence_dict_from_parsed(raw)

# ================== hybrid_retrieval 专用函数 ==================

def check_fee_scope_info_with_langfuse(field_name: str, base_text: str) -> tuple:
    """判断保险条款是否包含理算因子相关数值"""
    result = llm_call_with_langfuse(
        "fee_scope_info_check",
        field_name=field_name,
        base_text=base_text,
        model_name="qwen-deepseek-v3.2",
        tags=["check", "fee_scope", "insurance"]
    )

    import re
    s = str(result).strip() if result is not None else ""

    # 解析相关/不相关决策
    m_out = re.search(r"<output>(.*?)</output>", s, flags=re.DOTALL)
    decision = m_out.group(1).strip() if m_out else s

    # 解析判断思路
    m_abs = re.search(r"<thought>(.*?)</thought>", s, flags=re.DOTALL)
    abstract_txt = m_abs.group(1).strip() if m_abs else ""

    # 返回决策和摘要
    is_related = "不包含" not in decision
    return is_related, abstract_txt

def check_fee_scope_info_batch_with_langfuse(field_name: str, batch_texts: list) -> list:
    """
    批量判断多段保险条款是否包含理算因子相关数值

    Args:
        field_name: 理算因子名称
        batch_texts: 文本列表，每个元素是一段保险条款

    Returns:
        list: 每段文本的判断结果列表，每个元素是 (is_related, abstract_txt) 元组
    """
    if not batch_texts:
        return []

    # 构建批量文本输入
    batch_text_input = ""
    for idx, text in enumerate(batch_texts, 1):
        batch_text_input += f"\n=== 第{idx}段文本 ===\n{text}\n"

    result = llm_call_with_langfuse(
        "fee_scope_info_check_batch",
        field_name=field_name,
        batch_texts=batch_text_input,
        model_name="qwen-deepseek-v3.2",
        tags=["check", "fee_scope", "insurance", "batch"]
    )

    import re
    s = str(result).strip() if result is not None else ""

    # 解析批量结果
    results = []
    for idx in range(1, len(batch_texts) + 1):
        # 尝试解析每一段的结果
        segment_pattern = rf"<segment_{idx}>(.*?)</segment_{idx}>"
        m_segment = re.search(segment_pattern, s, flags=re.DOTALL)

        if m_segment:
            segment_content = m_segment.group(1).strip()

            # 解析决策
            m_out = re.search(r"<output>(.*?)</output>", segment_content, flags=re.DOTALL)
            decision = m_out.group(1).strip() if m_out else segment_content

            # 解析思考过程
            m_abs = re.search(r"<thought>(.*?)</thought>", segment_content, flags=re.DOTALL)
            abstract_txt = m_abs.group(1).strip() if m_abs else ""

            # 判断是否相关
            is_related = "不包含" not in decision
            results.append((is_related, abstract_txt))
        else:
            # 如果无法解析，默认为不相关
            results.append((False, ""))

    return results

def check_waiting_period_info_with_langfuse(base_text: str) -> tuple:
    """
    判断保险条款是否包含等待期相关数值

    Args:
        base_text: 保险条款原始文本

    Returns:
        (is_related, abstract_txt): 是否相关及摘要文本
    """
    result = llm_call_with_langfuse(
        "waiting_period_info_check",
        model_name="qwen-deepseek-v3.2",
        base_text=base_text,
        tags=["check", "waiting_period", "insurance"]
    )

    import re
    s = str(result).strip() if result is not None else ""

    # 解析相关/不相关决策
    m_out = re.search(r"<output>(.*?)</output>", s, flags=re.DOTALL)
    decision = m_out.group(1).strip() if m_out else s

    # 解析判断思路
    m_abs = re.search(r"<thought>(.*?)</thought>", s, flags=re.DOTALL)
    abstract_txt = m_abs.group(1).strip() if m_abs else ""

    # 返回决策和摘要
    is_related = "不包含" not in decision
    return is_related, abstract_txt

def check_agreement_with_langfuse(field_name: str, base_text: str) -> tuple:
    """
    判断保险条款是否包含特别约定相关内容

    Args:
        field_name: 特别约定字段名
        base_text: 保险条款原始文本

    Returns:
        (is_related, abstract_txt): 是否相关及摘要文本
    """
    result = llm_call_with_langfuse(
        "agreement_check",
        field_name=field_name,
        base_text=base_text,
        model_name="qwen-deepseek-v3.2",
        tags=["check", "agreement", "insurance"]
    )

    import re
    s = str(result).strip() if result is not None else ""

    # 解析相关/不相关决策
    m_out = re.search(r"<output>(.*?)</output>", s, flags=re.DOTALL)
    decision = m_out.group(1).strip() if m_out else s

    # 解析判断思路
    m_abs = re.search(r"<thought>(.*?)</thought>", s, flags=re.DOTALL)
    abstract_txt = m_abs.group(1).strip() if m_abs else ""

    # 返回决策和摘要
    is_related = "不包含" not in decision
    return is_related, abstract_txt

def check_responsibility_discern_with_langfuse(field_name: str, base_text: str) -> tuple:
    """
    判断保险条款是否包含责任免除相关内容

    Args:
        field_name: 责任免除字段名
        base_text: 保险条款原始文本

    Returns:
        (is_related, abstract_txt): 是否相关及摘要文本
    """
    result = llm_call_with_langfuse(
        "responsibility_discern_check",
        field_name=field_name,
        base_text=base_text,
        model_name="qwen-deepseek-v3.2",
        tags=["check", "responsibility", "exclusion", "insurance"]
    )

    import re
    s = str(result).strip() if result is not None else ""

    # 解析相关/不相关决策
    m_out = re.search(r"<output>(.*?)</output>", s, flags=re.DOTALL)
    decision = m_out.group(1).strip() if m_out else s

    # 解析判断思路
    m_abs = re.search(r"<thought>(.*?)</thought>", s, flags=re.DOTALL)
    abstract_txt = m_abs.group(1).strip() if m_abs else ""

    # 返回决策和摘要
    is_related = "不包含" not in decision
    return is_related, abstract_txt

# ================== VLM_markdown_post_processing 专用函数 ==================
def convert_merged_tables_with_langfuse(
    image_urls: list,
    prompt_text: str = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    langfuse_prompt_label: Optional[str] = None
) -> str:
    """
    使用 Langfuse 管理的 Prompt 合并多个表格图像为单个 HTML 表格。

    Args:
        image_urls (list): 图片 URL 列表
        prompt_text (str, optional): 自定义提示词；若为 None，则使用默认模板
        session_id (str, optional): 会话ID
        user_id (str, optional): 用户ID
        metadata (dict, optional): 附加元数据
        langfuse_prompt_label (str, optional): Langfuse prompt标签
    Returns:
        str: 合并后的 HTML 表格字符串
    """
    if not image_urls:
        return ""

    # 获取 prompt 配置
    prompt_config = get_prompt_config("vlm_merged_table_conversion")

    # 确定 prompt label
    if langfuse_prompt_label is None:
        langfuse_prompt_label = os.environ.get("LANGFUSE_PROMPT_LABEL", "latest")

    # 获取 prompt 和 langfuse_prompt_obj
    prompt_template, langfuse_prompt_obj = _get_prompt_from_langfuse_or_local(
        "vlm_merged_table_conversion", langfuse_prompt_label, prompt_config
    )

    # 使用自定义 prompt_text 或从模板渲染
    if prompt_text is None:
        prompt_text = prompt_template.format()

    # 构建多模态输入
    content_list = []
    for url in image_urls:
        content_list.append({"type": "image_url", "image_url": {"url": url}})
    content_list.append({"type": "text", "text": prompt_text})

    prompt = [{"role": "user", "content": content_list}]

    from utils.llm import call_chatgpt_api
    MODEL_CATALOG = 'qwen-vl-max-latest'

    try:
        langfuse_client = get_langfuse_client()

        if LANGFUSE_ENABLED and langfuse_client:
            # 使用 propagate_attributes 确保 session_id 和 user_id 被正确传递
            with propagate_attributes(session_id=session_id, user_id=user_id):
                # 使用 as_type='generation' 以正确关联 prompt 并记录 usage/cost
                with langfuse_client.start_as_current_observation(
                    name="vlm_merged_table_conversion",
                    as_type='generation'
                ) as generation:
                    # 调用 API，获取 usage 信息
                    resp, usage_info = call_chatgpt_api(
                        prompt, MODEL_CATALOG, agent_name='disassemble_pdf_ocr', return_usage=True
                    )

                    # 处理响应
                    result = ""
                    if isinstance(resp, str):
                        result = resp.strip()
                    elif isinstance(resp, dict) and 'choices' in resp:
                        txt = resp['choices'][0]['message']['content']
                        if isinstance(txt, list):
                            txt = ''.join(seg.get('text', '') if isinstance(seg, dict) else str(seg) for seg in txt)
                        result = (txt or '').strip()

                    # 使用 _update_langfuse_generation 更新 observation
                    _update_langfuse_generation(
                        generation=generation,
                        result=result,
                        prompt_text=prompt_text,
                        raw_inputs={"image_urls": image_urls, "image_count": len(image_urls)},
                        usage_info=usage_info,
                        model_name=MODEL_CATALOG,
                        prompt_name="vlm_merged_table_conversion",
                        langfuse_prompt_label=langfuse_prompt_label,
                        langfuse_prompt_obj=langfuse_prompt_obj,
                        session_id=session_id,
                        user_id=user_id,
                        metadata={
                            **(metadata or {}),
                            "html_length": len(result),
                            "html_preview": result[:500] + "..." if len(result) > 500 else result
                        }
                    )

                    return result
        else:
            # Langfuse 未启用，直接调用
            resp = call_chatgpt_api(prompt, MODEL_CATALOG, agent_name='disassemble_pdf_ocr')
            if isinstance(resp, str):
                return resp.strip()
            elif isinstance(resp, dict) and 'choices' in resp:
                txt = resp['choices'][0]['message']['content']
                if isinstance(txt, list):
                    txt = ''.join(seg.get('text', '') if isinstance(seg, dict) else str(seg) for seg in txt)
                return (txt or '').strip()
            return ""

    except Exception as e:
        _logger.error(f"[VLM-MERGE] Error: {e}")
        return ""


def convert_single_table_with_langfuse(
    image_url: str,
    prompt_text: str = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    langfuse_prompt_label: Optional[str] = None
) -> str:
    """
    使用 Langfuse 管理的 Prompt 转换单个表格图像为 HTML 表格。

    Args:
        image_url (str): 图片 URL
        prompt_text (str, optional): 自定义提示词；若为 None，则使用默认模板
        session_id (str, optional): 会话ID
        user_id (str, optional): 用户ID
        metadata (dict, optional): 附加元数据
        langfuse_prompt_label (str, optional): Langfuse prompt标签
    Returns:
        str: HTML 表格内容
    """
    if not image_url or not isinstance(image_url, str):
        _logger.error(f"[VLM-SINGLE] Invalid image_url: {image_url}")
        return ''

    # 获取 prompt 配置
    prompt_config = get_prompt_config("vlm_single_table_conversion")

    # 确定 prompt label
    if langfuse_prompt_label is None:
        langfuse_prompt_label = os.environ.get("LANGFUSE_PROMPT_LABEL", "latest")

    # 获取 prompt 和 langfuse_prompt_obj
    prompt_template, langfuse_prompt_obj = _get_prompt_from_langfuse_or_local(
        "vlm_single_table_conversion", langfuse_prompt_label, prompt_config
    )

    # 使用自定义 prompt_text 或从模板渲染
    if prompt_text is None:
        prompt_text = prompt_template.format()

    content_list = [
        {"type": "image_url", "image_url": {"url": image_url}},
        {"type": "text", "text": prompt_text}
    ]

    prompt = [{"role": "user", "content": content_list}]

    from utils.llm import call_chatgpt_api
    MODEL_CATALOG = 'qwen-vl-max-latest'

    try:
        langfuse_client = get_langfuse_client()

        if LANGFUSE_ENABLED and langfuse_client:
            # 使用 propagate_attributes 确保 session_id 和 user_id 被正确传递
            with propagate_attributes(session_id=session_id, user_id=user_id):
                # 使用 as_type='generation' 以正确关联 prompt 并记录 usage/cost
                with langfuse_client.start_as_current_observation(
                    name="vlm_single_table_conversion",
                    as_type='generation'
                ) as generation:
                    # 调用 API，获取 usage 信息
                    resp, usage_info = call_chatgpt_api(
                        prompt, MODEL_CATALOG, agent_name='disassemble_pdf_ocr', return_usage=True
                    )

                    # 处理响应
                    result = ''
                    if isinstance(resp, str):
                        result = resp.strip()
                    elif isinstance(resp, dict) and 'choices' in resp:
                        txt = resp['choices'][0]['message']['content']
                        if isinstance(txt, list):
                            txt = ''.join(seg.get('text', '') if isinstance(seg, dict) else str(seg) for seg in txt)
                        result = (txt or '').strip()

                    # 使用 _update_langfuse_generation 更新 observation
                    _update_langfuse_generation(
                        generation=generation,
                        result=result,
                        prompt_text=prompt_text,
                        raw_inputs={"image_url": image_url},
                        usage_info=usage_info,
                        model_name=MODEL_CATALOG,
                        prompt_name="vlm_single_table_conversion",
                        langfuse_prompt_label=langfuse_prompt_label,
                        langfuse_prompt_obj=langfuse_prompt_obj,
                        session_id=session_id,
                        user_id=user_id,
                        metadata={
                            **(metadata or {}),
                            "html_length": len(result),
                            "html_preview": result[:500] + "..." if len(result) > 500 else result
                        }
                    )

                    return result
        else:
            # Langfuse 未启用，直接调用
            resp = call_chatgpt_api(prompt, MODEL_CATALOG, agent_name='disassemble_pdf_ocr')
            if isinstance(resp, str):
                return resp.strip()
            elif isinstance(resp, dict) and 'choices' in resp:
                txt = resp['choices'][0]['message']['content']
                if isinstance(txt, list):
                    txt = ''.join(seg.get('text', '') if isinstance(seg, dict) else str(seg) for seg in txt)
                return (txt or '').strip()
            return ''

    except Exception as e:
        _logger.error(f"[VLM-SINGLE] Error: {e}")
        return ''
# ================== catalog_generator 专用函数 ==================

def catalog_generator_with_langfuse(
    header_md_text: str,
    model_name: Optional[str] = None,
    session_id: Optional[str] = None
) -> str:
    """
    使用 Langfuse 管理的 Prompt 生成目录结构
    
    Args:
        header_md_text: 待整理的目录与索引信息
        model_name: 可选的模型名称，默认为 'gpt-5-chat'
        session_id: 会话ID，用于关联多个调用
        
    Returns:
        str: 生成的Markdown格式分层目录
    """
    result = llm_call_with_langfuse(
        "catalog_generator",
        header_md_text=header_md_text,
        model_name=model_name or "gpt-5-chat",
        session_id=session_id,
        tags=["catalog", "generation", "insurance"]
    )
    return str(result).strip() if result is not None else ""

# ================== html_table_to_markdown 专用函数 ==================

def html_table_to_markdown_with_langfuse(
    table_text: str,
    model_name: Optional[str] = None,
    session_id: Optional[str] = None
) -> str:
    """
    使用 Langfuse 管理的 Prompt 将 HTML 表格转换为 Markdown 格式的连贯文本段落
    
    Args:
        table_text: HTML 表格内容
        model_name: 可选的模型名称，默认为 'kimi/kimi-k2.5'
        session_id: 会话ID，用于关联多个调用
        
    Returns:
        str: Markdown 格式的连贯文本段落
    """
    result = llm_call_with_langfuse(
        "html_table_to_markdown",
        table_text=table_text,
        model_name=model_name or "kimi/kimi-k2.5",
        session_id=session_id,
        tags=["html_table", "conversion", "markdown", "insurance"]
    )
    return str(result).strip() if result is not None else ""

# ================== responsibility_agent 专用函数 ==================

def extract_responsibility_with_langfuse(
    text: str,
    model_name: Optional[str] = None,
    session_id: Optional[str] = None
) -> str:
    """
    使用 Langfuse 管理的 Prompt 从责免文本中抽取责免信息实体
    
    Args:
        text: 责免文本内容
        model_name: 可选的模型名称，默认为 'kimi/kimi-k2.5'
        session_id: 会话ID，用于关联多个调用
        
    Returns:
        str: JSON数组格式的责免信息实体列表
        输出示例：
        ["保健", "预防", "醉酒", "毒品", "康复", "产后恢复", "拔罐", "轮椅", "眼镜", "隐形眼镜", "配镜", "假眼", "假肢", "助听器", "遗传性疾病", "先天性畸形", "染色体异常", "残疾", "宫外孕", "药物过敏", "整容手术", "美容", "人工流产"]
    """
    result = llm_call_with_langfuse(
        "responsibility_extraction",
        text=text,
        model_name=model_name or "kimi/kimi-k2.5",
        session_id=session_id,
        tags=["extraction", "responsibility", "insurance"]
    )
    return str(result).strip() if result is not None else ""

def extract_health_notice_with_langfuse(
    text: str,
    model_name: Optional[str] = None,
    session_id: Optional[str] = None
) -> str:
    """
    使用 Langfuse 管理的 Prompt 从健告文本中抽取健康告知列表

    Args:
        text: 健告文本内容
        model_name: 可选的模型名称，默认为 'qwen-deepseek-chat'
        session_id: 会话ID，用于关联多个调用

    Returns:
        str: JSON数组格式的健康告知列表
    """
    result = llm_call_with_langfuse(
        "health_notice_extraction",
        text=text,
        model_name=model_name or "kimi/kimi-k2.5",
        session_id=session_id,
        tags=["extraction", "health_notice", "insurance"]
    )
    return str(result).strip() if result is not None else ""

def extract_fee_scope_factor_with_langfuse(
    retrieved_snippets: str,
    factor_type: str,
    structure_tree_leaf: str,
    fee_scope_descriptions: str,
    factor_type_to_fee_scope_mapping: str,
    factor_explanation: str = None,
    factor_type_to_fee_scope_default: str = None,
    model_name: Optional[str] = None,
    session_id: Optional[str] = None,
    plan_id: Optional[str] = None
) -> str:
    """
    使用 Langfuse 进行因子抽取，为每个因子创建独立的trace

    Args:
        retrieved_snippets: 召回的条款文档选段
        factor_type: 因子类型
        structure_tree_leaf: 责任颗粒度的结构链
        fee_scope_descriptions: 因子类型对应的费用范围枚举与人工说明
        model_name: 可选的模型名称，例如 'kimi/kimi-k2.5'
        session_id: 会话ID，用于关联多个调用（通常使用planNo）
        plan_id: 计划ID，用于设置trace的sessionId

    Returns:
        LLM响应结果（JSON格式的因子提取结果）
    """
    # 使用@observe装饰器自动创建trace，不需要手动调用langfuse_client.trace()
    # 装饰器会自动处理trace的创建和管理

    result = llm_call_with_langfuse(
        "fee_scope_factor_extraction",
        model_name=model_name or "kimi/kimi-k2.5",
        session_id=session_id,
        metadata={
            "factor_type": factor_type,
            "structure_tree_leaf": structure_tree_leaf,
            "plan_id": plan_id
        },
        retrieved_snippets=retrieved_snippets,
        factor_type=factor_type,
        structure_tree_leaf=structure_tree_leaf,
        fee_scope_descriptions=fee_scope_descriptions,
        factor_explanation=factor_explanation,
        factor_type_to_fee_scope_mapping=factor_type_to_fee_scope_mapping,
        factor_type_to_fee_scope_default=factor_type_to_fee_scope_default,
        tags=["extraction", "fee_scope", "insurance", factor_type]
    )
    return str(result).strip() if result is not None else ""

# ================== Prompt 获取工具函数 ==================

def generate_session_id_with_timestamp(
    policy_no: Optional[str] = None,
    plan_clause_liability_keyword: Optional[str] = None
) -> str:
    """
    生成带有时间戳的sessionId，包含policy_no、plan_no、plan_clause_liability_keyword

    Args:
        plan_no: 计划ID (planNo)
        policy_no: 保单号
        plan_clause_liability_keyword: 计划条款责任关键词

    Returns:
        格式化的sessionId: "{policy_no}_{plan_no}_{plan_clause_liability_keyword}_{YYYYMMDDHHMMSS}"
        或 None（如果所有参数都为空）
        时间戳使用当前执行时间

    Example:
        >>> generate_session_id_with_timestamp("PLAN001", "POLICY123", "医疗责任")
        "POLICY123_PLAN001_医疗责任_20251107065530"  # 时间戳为函数执行时的当前时间
        >>> generate_session_id_with_timestamp("PLAN001")
        "PLAN001_20251107065530"  # 兼容旧的调用方式
    """
    # 收集所有非空参数
    parts = []
    if policy_no:
        parts.append(policy_no)
    if plan_clause_liability_keyword:
        parts.append(plan_clause_liability_keyword)

    # 添加时间戳
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    parts.append(timestamp)

    return "_".join(parts)


# ================== hospital_scope_parsing 专用函数 ==================

def parse_hospital_scope_with_langfuse(
    hospital_scope_text: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    解析医院范围文本为 HospitalScopeDto 结构

    使用 LLM Prompt 将复杂的自然语言医院范围描述（如"二级及以上"、"社区卫生所"、
    "县区级公立综合"）解析为结构化的 HospitalScopeDto 列表。

    Args:
        hospital_scope_text: 医院范围描述文本
        session_id: 会话ID，用于关联多个调用
        user_id: 用户ID
        metadata: 附加元数据

    Returns:
        Dict: 包含 hospitalScopes 列表的字典，每个元素是一个 HospitalScopeDto 字典
        格式示例:
        {
            "hospitalScopes": [
                {
                    "id": "1",
                    "defDirection": "1",
                    "defLevel": "1",
                    "hospitalParam": {
                        "hospitalLevels": "2,3",
                        "hospitalNatures": "04",
                        "isNssfHospital": "Y"
                    },
                    "confidence": "0.95"
                }
            ]
        }
    """
    if not hospital_scope_text:
        _logger.warning(f"[session_id={session_id}] 医院范围文本为空，返回空列表")
        return {"hospitalScopes": []}

    # 1. 调用 LLM
    result = llm_call_with_langfuse(
        "hospital_scope_parsing",
        hospital_scope_text=hospital_scope_text,
        model_name="qwen-deepseek-v3.2",  # 建议使用指令遵循能力强的模型
        session_id=session_id,
        user_id=user_id,
        metadata=metadata,
        tags=["parsing", "hospital_scope", "code_parsers"]
    )

    # 2. 提取 JSON
    data = extract_json_dict_from_response(result)

    if not data:
        _logger.warning(f"[session_id={session_id}] JSON 提取失败，原始结果: {result if result else '空'}")
        return {"hospitalScopes": []}

    if "hospitalScopes" not in data:
        _logger.warning(f"[session_id={session_id}] 提取的 JSON 中未找到 hospitalScopes 键，提取的数据: {data}")
        return {"hospitalScopes": []}

    # 3. 数据清洗与增强 (Post-processing)
    for idx, scope in enumerate(data["hospitalScopes"], start=1):
        # 3.1 生成从1开始的 ID 序号
        scope["id"] = str(idx)

        # 3.2 设置默认值
        if "defDirection" not in scope:
            scope["defDirection"] = "1"  # 默认为包含
        if "defLevel" not in scope:
            scope["defLevel"] = "1"  # 默认为按属性配置
        if "confidence" not in scope:
            scope["confidence"] = "1"  # 默认置信度为1

        # 3.3 结构一致性清理
        if scope.get("defLevel") == "2" and scope.get("hospitalParam"):
            # 指定医院模式：清空 param
            _logger.warning(f"[session_id={session_id}] defLevel=2 指定医院不应包含 hospitalParam，已自动清理: {scope.get('hospitalNames', '')[:50]}")
            scope["hospitalParam"] = None
        elif scope.get("defLevel") == "1" and scope.get("hospitalNames"):
            # 按属性配置模式：清空 hospitalNames
            _logger.warning(f"[session_id={session_id}] defLevel=1 按医院属性配置不应包含 hospitalNames，已自动清理: {scope.get('hospitalNames', '')[:50]}")
            scope["hospitalNames"] = None

    _logger.info(f"医院范围解析完成: 输入'{hospital_scope_text[:50]}...' -> 输出 {len(data['hospitalScopes'])} 个范围对象")

    return data
