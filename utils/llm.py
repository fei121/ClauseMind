# 这个文件封装了项目需要的所有工具，包括字符串处理、数据库落库、网管调用、标签获取等
import json
import os
import threading
import time
import traceback
import uuid  # 新增：用于生成调用UUID
from collections import deque
from contextlib import contextmanager
from datetime import datetime

from utils.logger import logger

from langchain_core.messages import AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.outputs import LLMResult
from infrastructure.http_session import get_session
from repositories.langfuse_integration import get_langfuse_client, LANGFUSE_ENABLED

langfuse_client = get_langfuse_client()
from langchain_core.outputs import ChatGeneration


# 延迟导入，避免循环导入
# def extract_field_content_with_hub(field_name: str, original_content: str) -> str:
#     # 使用 Langfuse 集成
#     from repositories.langfuse_integration import extract_field_content_with_langfuse as func
#     return func(field_name, original_content)

# def check_liability_field_requirement_with_hub(liab_name: str, field_name: str, base_text: str, conf:RunnableConfig, field_related_para_requirement:str) -> tuple:
#     from field_redefine.langfuse_hub_integration import check_liability_field_requirement_with_hub as func
#     return func(liab_name, field_name, base_text, conf, field_related_para_requirement)

from config import *
from infrastructure.db_utils import db_manager
import json_repair
import re
from typing import List, Dict, Optional, Any
from langchain_core.language_models import SimpleChatModel
from langchain_core.prompts import PromptTemplate

# Tracing 配置（支持 LangFuse / None）
tracing_client = None
tracing_enabled = False
tracing_context_manager = None


# 定义排序键函数
def sort_liab(item):
    # 检查 liability_name 中是否包含 "特定的" 或 "一般的"
    name = item['liab_name']
    if '特定' in name:
        return 0  # 特定的排在前面
    elif '一般' in name:
        return 1  # 一般的排在后面
    else:
        return 2  # 其他情况排在最后

# 抽取条款责任描述做格式化信息
def extract_liab(text):
    # 匹配所有赔付情形块（使用非贪婪匹配）
    clauses = re.findall(r'[\(（]\d+[\)）](.*?)(?=\s*[\(（]\d+[\)）]|$)', text, flags=re.DOTALL)

    result = []
    for clause in clauses:
        # 匹配每个条件项并提取键值对
        matches = re.findall(r'【\s*([^:：]+?)\s*[:：]\s*([^】]*?)\s*】', clause)

        # 转换为字典并去除首尾空格
        clause_dict = {}
        for key, value in matches:
            clause_dict[key.strip()] = value.strip()

        if clause_dict:  # 避免空字典
            result.append(clause_dict)

    return result

# 去除列表中重复的字典元素
def remove_duplicate_dicts(list_of_dicts):
    seen = set()
    unique_dicts = []
    for d in list_of_dicts:
        # 将字典转换为不可变的元组
        # 这里假设字典的键和值都是可哈希的
        dict_tuple = tuple(sorted(d.items()))
        if dict_tuple not in seen:
            seen.add(dict_tuple)
            unique_dicts.append(d)
    return unique_dicts


# 示例算法-余琦做的json解析修复函数
def isstr(s, ntry=3):
    for i in range(ntry):
        if not isinstance(s, str):
            return s
        s = json_repair.loads(s)
    else:
        return None


# 字符串列表提取工具
def list_extract(s):
    # 找到第一个"["的位置
    start = s.find("[")
    # 找到最后一个"]"的位置
    end = s.rfind("]")
    # 提取第一个"["和最后一个"]"之间的内容，包括这两个字符
    s_list_content = s[start : end + 1]
    # 注意返回值还是一个字符串类型，如果要按列表消费需要后处理
    return s_list_content


# 字符串字典提取工具
def json_extract(s):
    # 找到第一个"{"的位置
    start = s.find("{")
    # 找到最后一个"}"的位置
    end = s.rfind("}")
    # 提取第一个"{"和最后一个"}"之间的内容，包括这两个字符
    s_json_content = s[start : end + 1]
    # 注意返回值还是一个字符串类型，如果要按字典消费需要后处理
    return s_json_content


# 数据库结果落库封装
def db_insert_result(
    report_no,
    request_json,
    prompt,
    agent_name,
    response_json,
    audit_memo,
    text_messages,
    tool_messages,
    time_cost,
):
    sql = """INSERT INTO demo_one_agent_multiple_tools
    (report_no, request_json, prompt, agent_name, response_json, audit_memo, text_messages, tool_messages, time_cost)
    VALUES (:report_no, :request_json, :prompt, :agent_name, :response_json, :audit_memo, :text_messages, :tool_messages, :time_cost)"""

    params = {
        "report_no": report_no,
        "request_json": request_json,
        "prompt": prompt,
        "agent_name": agent_name,
        "response_json": response_json,
        "audit_memo": audit_memo,
        "text_messages": text_messages,
        "tool_messages": tool_messages,
        "time_cost": time_cost
    }

    db_manager.execute_insert(sql, params=params)

def db_insert_disassemble_result(
        input_json,
        prompts_json,
        output_json
):
    sql = """INSERT INTO demo_disassemble_service_middle_info
    (input_json, prompts_json, output_json)
    VALUES (:input_json, :prompts_json, :output_json)"""

    params = {
        "input_json": input_json,
        "prompts_json": prompts_json,
        "output_json": output_json
    }
    try:
        db_manager.execute_insert(sql, params=params)
    except Exception as e:
        logger.info(f"条款拆解：拆解中间结果插入数据库异常，{str(e)}")
        print("堆栈跟踪信息:")
        traceback.print_exc()


# ==============================
# 并发 + 速率限制配置
# ==============================
# 从 config 导入速率限制参数（支持环境变量配置）
from config import MAX_CONCURRENT_REQUESTS, MAX_CALLS_PER_SECOND, MAX_CALLS_PER_MINUTE

_api_call_semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)

# 本地内存速率限制（取代 Redis 分布式限流）
_call_timestamps = deque()  # 保存每次调用时间戳
_lock = threading.Lock()    # 保证线程安全


def _rate_limiter():
    """速率限制：限制每秒和每分钟调用次数，并提供等待提示（内存限流）"""
    while True:
        with _lock:
            now = time.time()
            # 清理超过一分钟的记录
            while _call_timestamps and now - _call_timestamps[0] > 60:
                _call_timestamps.popleft()

            # 当前统计
            calls_last_minute = len(_call_timestamps)
            # 当前1秒内的调用次数
            calls_last_second = sum(1 for t in _call_timestamps if now - t <= 1)

            if calls_last_minute < MAX_CALLS_PER_MINUTE and calls_last_second < MAX_CALLS_PER_SECOND:
                # ✅ 允许调用
                _call_timestamps.append(now)
                return
            else:
                # 🚦 超出速率限制：计算预计等待时间
                wait_seconds = 0.05  # 默认短等待
                next_available_time = None

                if calls_last_second >= MAX_CALLS_PER_SECOND:
                    oldest_recent = max(t for t in _call_timestamps if now - t <= 1)
                    next_available_time = oldest_recent + 1.0
                elif calls_last_minute >= MAX_CALLS_PER_MINUTE:
                    oldest_minute = _call_timestamps[0]
                    next_available_time = oldest_minute + 60.0

                if next_available_time:
                    wait_seconds = max(next_available_time - now, 0.05)

                # 输出等待提示
                logger.info(
                    f"⏳ [限流等待中] 当前1秒内{calls_last_second}次，1分钟内{calls_last_minute}次，"
                    f"等待约 {wait_seconds:.2f} 秒..."
                )

        # 释放锁后再等待
        time.sleep(wait_seconds)


@contextmanager
def limit_concurrent_calls():
    """上下文管理器：限制并发数量 + 速率（内存限流）"""
    _api_call_semaphore.acquire()
    try:
        _rate_limiter()  # ✅ 加入速率限制 + 等待提示（内存限流）
        yield
    finally:
        _api_call_semaphore.release()


# ==============================
# Calculate token cost
# ==============================
def calculate_cost_from_usage(usage_info: dict, model_name: str = "unknown") -> dict:
    """
    根据usage信息计算成本

    Args:
        usage_info: API返回的usage字典，包含prompt_tokens, completion_tokens等
        model_name: 模型名称，用于确定价格

    Returns:
        包含input_cost, output_cost, total_cost的字典
    """
    # Default pricing (CNY per thousand tokens)
    # Note: These are example values and should be updated based on actual model pricing
    default_input_cost_per_1k = 0.01
    default_output_cost_per_1k = 0.03

    # Model pricing configuration (CNY per thousand tokens)
    # TODO: Move to configuration file or environment variables for easier updates
    model_pricing = {
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-3.5-turbo": {"input": 0.0015, "output": 0.002},
        "moonshot": {"input": 0.012, "output": 0.012},
        # Add more models as needed
    }

    # Search for matching pricing (use exact key match first, then substring)
    input_cost_per_1k = default_input_cost_per_1k
    output_cost_per_1k = default_output_cost_per_1k

    # First try exact match
    if model_name.lower() in model_pricing:
        input_cost_per_1k = model_pricing[model_name.lower()]["input"]
        output_cost_per_1k = model_pricing[model_name.lower()]["output"]
    else:
        # Then try substring match
        for model_key, pricing in model_pricing.items():
            if model_key.lower() in model_name.lower():
                input_cost_per_1k = pricing["input"]
                output_cost_per_1k = pricing["output"]
                break

    # Extract token counts
    prompt_tokens = usage_info.get("prompt_tokens", 0)
    completion_tokens = usage_info.get("completion_tokens", 0)

    # Calculate costs
    input_cost = (prompt_tokens / 1000.0) * input_cost_per_1k
    output_cost = (completion_tokens / 1000.0) * output_cost_per_1k
    total_cost = input_cost + output_cost

    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": total_cost
    }


# ==============================
# Langfuse observation helper - v3 compatible
# ==============================
def _update_langfuse_observation(
    messages_input,
    model_output,
    usage_info,
    engine,
    agent_name,
    report_no,
    model_name='unknown',
    call_id='unknown',
    gateway_url=None
):
    """
    更新Langfuse observation (兼容v3版本)

    Args:
        messages_input: 输入消息列表
        model_output: 模型输出
        usage_info: 使用信息字典
        engine: 使用的引擎/模型
        agent_name: agent名称
        report_no: 报告编号
        model_name: 模型名称
        call_id: 调用ID
        gateway_url: 网关URL（可选）
    """
    if not LANGFUSE_ENABLED or not langfuse_client:
        return

    try:
        # 准备usage详细信息，处理不同格式的usage_info
        usage_details = {}
        if usage_info:
            # 处理标准的usage格式
            if isinstance(usage_info, dict):
                prompt_tokens = usage_info.get('prompt_tokens') or usage_info.get('input_tokens') or usage_info.get('input')
                completion_tokens = usage_info.get('completion_tokens') or usage_info.get('output_tokens') or usage_info.get('output')
                total_tokens = usage_info.get('total_tokens') or usage_info.get('total')

                if prompt_tokens is not None:
                    usage_details['input'] = prompt_tokens
                if completion_tokens is not None:
                    usage_details['output'] = completion_tokens
                if total_tokens is not None:
                    usage_details['total'] = total_tokens

        # 计算成本详情
        cost_details = None
        if usage_details:
            cost_details = calculate_cost_from_usage(usage_info or usage_details, engine)

        # 准备元数据
        metadata = {
            'agent_name': agent_name,
            'report_no': report_no,
            'model_name': model_name or engine,
            'call_id': call_id
        }
        if gateway_url:
            metadata['gateway_url'] = gateway_url

        # 提取输入文本（从messages_input）
        input_text = ""
        if isinstance(messages_input, list) and len(messages_input) > 0:
            # 处理OpenAI消息格式
            if isinstance(messages_input[0], dict) and 'content' in messages_input[0]:
                input_text = messages_input[0]['content']
            # 处理LangChain消息对象
            elif hasattr(messages_input[0], 'content'):
                input_text = messages_input[0].content

        # 在v3中，由于我们没有使用@observe装饰器，我们直接记录日志而不创建observation
        # 这种方式保持了向后兼容，同时利用了v3的客户端的自动上下文管理

        logger.debug(f"[Langfuse] Recording LLM call: {agent_name} | {engine} | tokens: {usage_details}")

    except Exception as e:
        logger.warning(f"更新Langfuse observation失败: {e}")

# ==============================
# Call ChatGPT API (with automatic retry)
# ==============================
def call_chatgpt_api(
    prompt: str | list,
    engine: str = LLM_DEFAULT,
    report_no: str = "19990615",
    agent_name: str = "default_agent",
    timeout: int = 500,
    enable_search: bool = True,
    max_retries: int = 15,
    return_usage: bool = False,
) -> str | tuple[str, dict]:
    """LLM call with retry and rate limiting

    Args:
        prompt: Prompt text (str) or message list (list of dicts with 'role' and 'content' keys).
                If list, it's assumed to be in OpenAI message format: [{"role": "user", "content": "..."}]
        engine: Model engine name
        report_no: Report number
        agent_name: Agent name
        timeout: Timeout in seconds
        enable_search: Enable search
        max_retries: Maximum retry attempts
        return_usage: Whether to return usage info. If True, returns (output, usage_dict) tuple

    Returns:
        If return_usage=False: str (model output)
        If return_usage=True: tuple[str, dict] (model output, usage dict)
    """
    retry = 0
    base_wait = 2  # Initial wait time (seconds)
    # Prepare input for langfuse observation (message format)
    # Note: If prompt is a list, we assume it's already in OpenAI message format
    # [{"role": "user", "content": "..."}]. No validation is performed for performance.
    if isinstance(prompt, list):
        messages_input = prompt  # Already in message format
    else:
        messages_input = [{"role": "user", "content": prompt}]

    while True:
        _uuid = str(uuid.uuid4())
        try:
            with limit_concurrent_calls():  # Concurrency and rate control
                # 使用共享Session
                session = get_session()
                if OPENAI_API_BASE and OPENAI_API_KEY:
                    # 本地 OpenAI 兼容模式统一使用已验证的模型，避免业务节点中的旧内部模型别名失效。
                    effective_engine = OPENAI_CHAT_MODEL
                    concise_user_messages = []
                    for message in messages_input:
                        message = dict(message)
                        content = message.get("content")
                        if isinstance(content, str):
                            content = content.replace(
                                "先对问题进行分析，给出你的思考，同时你的返回应该",
                                "你的返回应该",
                            ).replace(
                                "在给出最终结果前先进行任务分析，给出你的思考。",
                                "直接给出最终结果。",
                            ).replace(
                                "请先输出思考过程，用<thought>包裹，然后输出包含/不包含，用<output>包裹。",
                                "请直接输出包含/不包含，用<output>包裹。",
                            )
                            message["content"] = content
                        concise_user_messages.append(message)
                    concise_messages = [{
                        "role": "system",
                        "content": (
                            "直接输出最终答案，不展示分析、思考过程或任务复述。"
                            "严格遵守用户要求的 JSON 或文本格式，并保持答案简洁。"
                        ),
                    }, *concise_user_messages]
                    data = {
                        "model": effective_engine,
                        "messages": concise_messages,
                        "temperature": 0,
                        "top_p": 1,
                        "max_tokens": int(os.getenv("OPENAI_MAX_TOKENS", "2048")),
                        "enable_thinking": False,
                    }
                    headers = {
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    }
                    prompt_length = len(str(messages_input))
                    start_time = time.time()
                    response = session.post(
                        f"{OPENAI_API_BASE}/chat/completions",
                        headers=headers,
                        json=data,
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    response_json = response.json()
                    model_output = response_json.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if not model_output:
                        raise ValueError(f"OpenAI-compatible response has no content: {response_json}")

                    call_id = response_json.get("id", "unknown")
                    model_name = response_json.get("model", effective_engine)
                    usage_info = response_json.get("usage", {})
                    logger.info(
                        f"【OpenAI兼容LLM调用】agent：{agent_name} | 模型：{model_name} | "
                        f"耗时：{time.time() - start_time:.2f}秒 | prompt长度：{prompt_length}"
                    )
                    _update_langfuse_observation(
                        messages_input=messages_input,
                        model_output=model_output,
                        usage_info=usage_info,
                        engine=effective_engine,
                        agent_name=agent_name,
                        report_no=report_no,
                        model_name=model_name,
                        call_id=call_id,
                        gateway_url=OPENAI_API_BASE,
                    )
                    if return_usage:
                        return model_output, usage_info
                    return model_output

                elif APP_ENV == "demo":
                    data = {
                        "customer_id": "clausemind_demo",
                        "engine": engine,
                        "messages": messages_input,
                        "temperature": 0,
                        "top_p": 1,
                        "max_tokens": 8000,
                        "extra_body": {"enable_thinking": False}
                    }
                    if not engine.startswith("gateway"):
                        data["extra_body"] = {"enable_thinking": False}

                    # # 添加详细日志：记录调用开始
                    prompt_length = len(str(messages_input))
                    # logger.debug(
                    #     f"[LLM调用开始] 案件：{report_no} | agent：{agent_name} | "
                    #     f"模型：{engine} | prompt长度：{prompt_length} | max_tokens：8000"
                    # )

                    start_time = time.time()
                    response = session.post(GATEWAY_URL, json=data, timeout=timeout)
                    response.raise_for_status()
                    response_json = json.loads(response.text)

                    elapsed_time = time.time() - start_time

                    # 添加性能预警：超过120秒记录WARNING
                    if elapsed_time > 120:
                        logger.warning(
                            f"⚠️ [LLM调用耗时过长] 案件：{report_no} | agent：{agent_name} | "
                            f"模型：{engine} | 耗时：{elapsed_time:.2f}秒 | "
                            f"prompt长度：{prompt_length} | 响应大小：{len(response.text)}"
                        )

                    model_output = response_json.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if not model_output or "模型结果解析异常" in model_output:
                        logger.warning(f"模型结果解析异常: {response_json}")
                        raise ValueError("模型结果解析异常")

                    call_id = response_json.get('id', 'unknown')
                    model_name = response_json.get('model', 'unknown')
                    usage_info = response_json.get('usage', {})
                    logger.info(
                        f"【llm调用记录】案件：{report_no} | agent：{agent_name} | 调用uuid：{call_id} | "
                        f"模型：{model_name} | 耗时：{time.time() - start_time:.2f}秒"
                    )

                    # Update langfuse observation with proper input/output format
                    _update_langfuse_observation(
                        messages_input=messages_input,
                        model_output=model_output,
                        usage_info=usage_info,
                        engine=engine,
                        agent_name=agent_name,
                        report_no=report_no,
                        model_name=model_name,
                        call_id=call_id
                    )

                    if return_usage:
                        return model_output, usage_info
                    return model_output

                elif APP_ENV == "disabled":
                    if agent_name in ["medical_record_combiner", "medical_record_parser", "policy_liab_match", "special_liab_match"]:
                        gateway_url = DISTILL_GATEWAY_URL
                        gateway_key = DISTILL_GATEWAY_KEY
                        gateway_channel = DISTILL_GATEWAY_CHANNEL
                    else:
                        gateway_url = GATEWAY_URL
                        gateway_key = GATEWAY_KEY
                        gateway_channel = GATEWAY_CHANNEL

                    za_headers = {
                        "Content-Type": "application/json",
                        "access-key": gateway_key,
                        "access-channel": gateway_channel
                    }

                    # Convert message format to prompt string for za environment
                    # Safely extract content from message list
                    prompt_content = prompt  # Default fallback
                    if isinstance(messages_input, list) and len(messages_input) > 0:
                        first_msg = messages_input[0]
                        if isinstance(first_msg, dict) and "content" in first_msg:
                            prompt_content = first_msg["content"]

                    data = {
                        "isNeedSession": False,
                        "skillParams": {
                            "report_no": report_no,
                            "content": prompt_content
                        }
                    }
                    response = session.post(url=gateway_url, headers=za_headers, json=data, timeout=timeout)
                    response.raise_for_status()
                    response_data = json.loads(response.text)
                    model_output = response_data['data']['response']
                    if not model_output or "模型结果解析异常" in model_output:
                        raise ValueError("模型结果解析异常")

                    # Try to extract usage info if available
                    usage_info = response_data.get('usage', {})

                    # Update langfuse observation for za environment
                    _update_langfuse_observation(
                        messages_input=messages_input,
                        model_output=model_output,
                        usage_info=usage_info,
                        engine=engine,
                        agent_name=agent_name,
                        report_no=report_no,
                        gateway_url=gateway_url
                    )

                    if return_usage:
                        return model_output, usage_info
                    return model_output

        except Exception as e:
            retry += 1
            wait_time = base_wait * (2 ** (retry - 1))  # Exponential backoff
            wait_time = min(wait_time, 60)  # Max wait 1 minute

            logger.warning(
                f"⚠️ [调用异常] {_uuid} 第 {retry}/{max_retries} 次重试：{e}，"
                f"等待 {wait_time:.1f}s 后重试..."
            )

            if retry >= max_retries:
                logger.warning(f"❌ [重试失败] 已达到最大重试次数({max_retries})，返回空字符串。")
                if return_usage:
                    return f"❌ [重试失败] 已达到最大重试次数({max_retries})，返回空字符串。", {}
                return f"❌ [重试失败] 已达到最大重试次数({max_retries})，返回空字符串。"

            time.sleep(wait_time)

class GatewayLLM(SimpleChatModel):
    """Minimal LLM wrapper for any gateway, with Langfuse tracing support"""
    timeout: int = 60
    model_name: str = "auto"                            # Model identifier for Langfuse
    engine: Optional[str] = LLM_DEFAULT                  # Optional model engine name
    last_usage: Optional[Dict] = None                   # Stores usage info from last call

    @property
    def _llm_type(self) -> str:
        return "gateway"

    def _call(
            self,
            messages,
            stop=None,
            run_manager=None,
            **kwargs
    ) -> str:
        # Convert LangChain Message to plain text (or gateway required format)
        # For single message, just use content; for multiple, join with newlines
        if len(messages) == 1:
            prompt = messages[0].content
        else:
            prompt = "\n".join(m.content for m in messages)
        try:
            # Use return_usage=True to get usage info from call_chatgpt_api
            if self.engine:
                resptext, usage_info = call_chatgpt_api(prompt, engine=self.engine, return_usage=True)
            else:
                resptext, usage_info = call_chatgpt_api(prompt, engine=LLM_DEFAULT, return_usage=True)

            # Save usage info for later use
            self.last_usage = usage_info

            # Notify run_manager (for Langfuse callbacks)
            if run_manager:
                run_manager.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content=resptext))]]))

        except Exception as e:
            logger.error(f"[LLM Call] Gateway call error: {str(e)}")
            self.last_usage = {}
            if run_manager:
                run_manager.on_llm_error(e)
            resptext = ""
        return resptext


def llm_call(prompt: str, model_name: Optional[str] = None, temperature: Optional[float] = None) -> str:
    """
    LangGraph/LCEL 标准调用：
    - 使用 ChatPromptTemplate + ChatOpenAI + StrOutputParser
    - 在设置了 LANGCHAIN_TRACING_V2/LANGCHAIN_API_KEY/LANGCHAIN_PROJECT 后，自动被 LangFuse 采集
    - 返回纯字符串
    """
    model = GatewayLLM()
    tpl = PromptTemplate.from_template("{prompt}")
    chain = tpl | model | StrOutputParser()
    return chain.invoke({"prompt": prompt})

def call_qwen_plus_api(prompt: str, engine: str=LLM_DEFAULT, report_no: str="19990615", agent_name: str="default_agent", timeout: int = 500, enable_search: bool = True
) -> str:
    if APP_ENV == "demo":
        session = get_session()
        data = {"customer_id": "clausemind_demo",
                "engine": engine,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "top_p": 1,
                "max_token": 2000,
                "extra_body": {"enable_thinking": True},
                "stream": True
                }
        start_time = time.time()

        completion = session.post(GATEWAY_URL, json=data, stream=True, timeout=timeout)
        full_content = ""
        thinking_content = ""
        completion_id = ""
        model_name = ""

        for chunk in completion.iter_lines():
            if chunk:  # 确保chunk不为空
                chunk_data = json.loads(chunk.decode())

                # 从第一个chunk中提取id和model信息
                if not completion_id and "id" in chunk_data:
                    completion_id = chunk_data["id"]
                if not model_name and "model" in chunk_data:
                    model_name = chunk_data["model"]

                if len(chunk_data["choices"]) > 0:
                    delta = chunk_data["choices"][0]["delta"]

                    # 获取思考过程
                    reasoning = delta.get("reasoning_content", "")
                    if reasoning:
                        thinking_content += reasoning
                        # print(f"[思考] {reasoning}", end="")

                    # 获取回答内容
                    content = delta.get("content", "")
                    if content:
                        full_content += content
                        # print(f"[回答] {content}", end="")

        # print(f"\n\n思考过程:\n{thinking_content}")
        # print(f"\n最终回答:\n{full_content}")
        logger.info(
            f"【llm调用记录】当前案件：{report_no} 的 {agent_name} agent, 调用uuid：{completion_id}, 调用引擎：{model_name}, 耗时：{time.time() - start_time:.2f}秒"
        )
        return full_content
    elif APP_ENV == "disabled":
        raise NotImplementedError("Qwen Plus API调用仅支持个人 Demo环境")


# def call_chatgpt_api(
#     prompt: str, engine: str=LLM_DEFAULT, report_no: str="19990615", agent_name: str="default_agent", timeout: int = 300
# ) -> str:
#     model_output = "这是测试输出"
#     return model_output
def catalog_generator(header_md_text: str) -> str:
    """
    基于 header_md_text 生成目录（含页码占位或模型推断）。
    使用 Langfuse 集成进行 Prompt 管理。
    """
    from repositories.langfuse_integration import catalog_generator_with_langfuse
    return catalog_generator_with_langfuse(header_md_text)

# def get_related_chunk_first_stage(raw_results, field_name, emb, k):
#     import math
#     q_vec = emb.embed_query(str(field_name))
#     def _cosine(u, v):
#         if not u or not v:
#             return 0.0
#         dot = sum(float(a) * float(b) for a, b in zip(u, v))
#         nu = math.sqrt(sum(float(a) * float(a) for a in u))
#         nv = math.sqrt(sum(float(b) * float(b) for b in v))
#         return (dot / (nu * nv)) if (nu > 0 and nv > 0) else 0.0
#     scored = []
#     # 1 从语义相关性层面先对所有候选做排序，如果遇到原始文本超长的情况，对原始文本中的内容进行抽取，然后统一计算emb相似度做排序
#     for item in raw_results:
#         txt = item.get("text") if isinstance(item, dict) else str(item)
#         # 如果 meta.oricontent 存在且比 text 更长，则对 oricontent 做 LLM 精确抽取，替换 txt，并写回 target_oricontent
#         try:
#             if isinstance(item, dict):
#                 meta = item.get("meta") or item.get("metadata") or {}
#                 ori = meta.get("oricontent")
#                 # 长度过长的时候，先用大模型对原始文本针对目标字段做总结，然后再计算后续的相关性得分
#                 if (isinstance(ori, str) and ori and len(ori) > len(str(txt or ""))) or len(txt) > 2048:
#                     # 使用LangFuse Prompt Hub进行精确抽取
#                     if ori:
#                         ext = extract_field_content_with_hub(field_name, ori)
#                     elif txt:
#                         ext = extract_field_content_with_hub(field_name, txt)
#                     else:
#                         continue
#                     ext_s = str(ext).strip() if ext is not None else ""
#                     if ext_s:
#                         txt = ext_s
#                         try:
#                             if "meta" in item and isinstance(item["meta"], dict):
#                                 item["meta"]["target_oricontent"] = ext_s
#                             elif "metadata" in item and isinstance(item["metadata"], dict):
#                                 item["metadata"]["target_oricontent"] = ext_s
#                         except Exception:
#                             pass
#         except Exception as _e:
#             logger.warning(f"oricontent 抽取失败，使用原始文本: {_e}")
#         if not txt:
#             continue
#         try:
#             d_vec = emb.embed_query(str(txt))
#             score = _cosine(q_vec, d_vec)
#             scored.append((score, item))
#         except Exception as e:
#             logger.warning(f"query emb cosine cal exception: {e}")
#             continue
#     scored.sort(key=lambda x: x[0], reverse=True)
#     # 得到了从语义上的排序结果，然后进行过滤，主要是过滤掉目标责任、计划的描述，除非这个候选的上下文里没有明显的责任或计划的限定
#     k = min(k, len(scored))
#     scored = scored[:k]
#     return scored

# ybk待调整代码 诊断标签获取工具，用于调用kb库获取诊断标签
def get_kb_tags(diagnose_list, org_code="10001"):
    try:
        session = get_session()
        items = [
            {
                "itemType": diagnose["itemType"],
                "itemCode": diagnose["itemCode"],
                "itemName": diagnose["itemName"],
                "detailCode": diagnose["detailCode"],
                "detailName": diagnose["detailName"],
            }
            for diagnose in diagnose_list
        ]
        data = {
            "organizationCode": org_code,
            "tenantCode": org_code,
            "items": items
        }
        headers = {
            'Content-Type': 'application/json'
        }
        url_response = session.post(TAG_URL, headers=headers, data=json.dumps(data))
        tag_list = []
        # 个人 Demo不消费detail相关内容
        if APP_ENV == 'demo':
            response_list = url_response.json()['result']
            for res in response_list:
                stdName = res["stdName"] if res["stdName"] else res["itemName"]
                tag_name_list = []
                tag_code_list = []
                if res["tags"]:
                    for tag in res["tags"]:
                        tag_name_list.append(tag["name"])
                        tag_code_list.append(tag["code"])
                tag_list.append({"stdName":stdName, "tag_name_list":tag_name_list, "tag_code_list":tag_code_list})
        elif APP_ENV == 'disabled':
            response_list = url_response.json()['result']
            for index, res in enumerate(response_list):
                tag_name_list = []
                tag_code_list = []
                if items[index]["detailCode"] or items[index]["detailName"]:
                    stdName = res["detailName"] if res["detailName"] else res["itemName"]
                    try:
                        for tag in res["detailList"]:
                            tag_name_list.append(tag["name"])
                            tag_code_list.append(tag["code"])
                    except Exception as e:
                        logger.warning(f"解析 detailList 标签失败: {e}")
                else:
                    try:
                        stdName = res["stdName"]
                    except Exception as e:
                        logger.warning(f"获取 stdName 失败，使用 itemName 替代: {e}")
                        stdName = res["itemName"]
                    for tag in res["tags"]:
                        tag_name_list.append(tag["name"])
                        tag_code_list.append(tag["code"])
                tag_list.append({"stdName":stdName, "tag_name_list":tag_name_list, "tag_code_list":tag_code_list})
        return tag_list
    except Exception as e:
        logger.info("【kb库标签获取报错】兜底处理返回空列表,错误原因:"+str(e))
        return [{"stdName":"匹配异常报错", "tag_name_list":[], "tag_code_list":[]}]

def parse_tags_by_item_type(json_data):
    """
    从JSON数据中解析tags里的item，按照itemType分组，每组存储name的列表
    """
    # 初始化结果字典
    result = {}

    # 遍历JSON数据中的每个tag项
    for tag_item in json_data.get('result', {}).get('paramDTOList', []):
        if not tag_item.get('tags', []):
            continue
        for tag in tag_item.get('tags', []):
            item_type = tag.get('itemType')
            name = tag.get('name')

            # 如果itemType和name都存在
            if item_type is not None and name is not None:
                item_type = str(item_type)
                # 如果该itemType还没有在结果字典中，初始化一个空列表
                if item_type not in result:
                    result[item_type] = []
                # 将name添加到对应itemType的列表中
                result[item_type].append(name)

    return result

# 新接口，输入是潜在的标签文本（不区分标签类型），返回对应的标签id和标签类型
def get_general_kb_tags(input_text_list, org_code="10001"):
    result_map = {}
    try:
        session = get_session()
        # 只在个人 Demo调用
        if APP_ENV == 'disabled':
            return {}
        items = [
                    {
                        "tagAliasesName": name
                    }
            for name in input_text_list
        ]
        data = {
            "tenantCode": org_code,
            "paramDTOList": items
        }
        headers = {
            'Content-Type': 'application/json'
        }
        url_response = session.post(GENERAL_TAG_URL, headers=headers, data=json.dumps(data))
        output = url_response.json()
        if output['code'] == '200':
            result_map = parse_tags_by_item_type(output)
        return result_map
    except Exception as e:
        logger.info("【general kb库标签获取报错】兜底处理返回空列表,错误原因:"+str(e))
        print("堆栈跟踪信息:")
        traceback.print_exc()
        return {}

def generate_reject_mapping_cache() -> Dict[str, List[Dict]]:
    reject_mapping_cache = {}
    try:
        # 查询租户映射配置
        enable_query = """
        SELECT org_code FROM demo_tenant_mapping_enable
        WHERE is_enabled = 'Y' AND is_deleted = 'N'
        """
        enable_results = db_manager.execute_query(enable_query, return_type = "list_of_dicts")

        if enable_results:
            for result in enable_results:
                org_code = result['org_code']
                # 根据 org_code 查询 demo_reject_mapping_config 表的数据
                mapping_query = """
                    SELECT * FROM demo_reject_mapping_config
                    WHERE org_code = :org_code AND is_deleted = 'N'
                    """
                mapping_results = db_manager.execute_query(mapping_query, params={"org_code": org_code}, return_type = "list_of_dicts")
                reject_mapping_cache[org_code] = mapping_results
    except Exception as e:
        logger.error(f"生成拒付映射配置缓存时出错: {str(e)}")
    return reject_mapping_cache
