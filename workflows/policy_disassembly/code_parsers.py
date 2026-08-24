"""
Date: 2026-01-05 10:04:10
LastEditTime: 2026-01-09 15:32:13
Description:
文本解析函数模块 - 优化版
"""

import json
import re
from typing import Any, Dict, List, Optional, Union

from models.pydantic.response import (
    HospitalScopeDto,
    HospitalParamDto,
    PayScopeDto,
    ClaimNatureDto,
    MedicalTypeDto,
    FeeCategoryDto,
    RuleDto,
    NonResponsibilityDto
)

from utils import logger
from models.pydantic.code_tables import (
    MEDICAL_TYPE_KEYWORD_TO_CODE_MAP,
    FEE_CATEGORY_KEYWORD_TO_CODE_MAP,
    ALL_FEE_CATEGORIES,
    NON_RESP_TYPE_KEYWORD_TO_CODE_MAP,
    HOSPITAL_GRADE_KEYWORD_TO_CODE_MAP,
    HOSPITAL_NATURE_MAP,
    HOSPITAL_ORG_TYPE_MAP
)

from repositories.langfuse_integration import parse_hospital_scope_with_langfuse

# ==========================================
# 常量与配置
# ==========================================

_BRACE_REGEX = re.compile(r'[｛{]指定医院(除外|包含)[：:]\s*([^}｝]+)[｝}]')
_SEPARATOR_REGEX = re.compile(r'[、。，；;,\s]+')
_DAYS_REGEX = re.compile(r'(\d+)\s*[天日]')

# 责免类型映射 - 从 xlsx 动态加载（对应"医疗种类"）
_NON_RESP_TYPE_MAP = NON_RESP_TYPE_KEYWORD_TO_CODE_MAP

# 医院等级映射 - 从 xlsx 动态加载（对应"hospital_grade"）
_HOSPITAL_GRADE_MAP = HOSPITAL_GRADE_KEYWORD_TO_CODE_MAP

# 医院性质映射 - 从码值到文本的逆映射
_HOSPITAL_NATURE_KEYWORD_TO_CODE_MAP = {v: k for k, v in HOSPITAL_NATURE_MAP.items()}

# ==========================================
# 文本提取函数
# ==========================================

def extract_claim_nature(accident_type: str) -> str:
    """提取事故类型码值"""
    if not accident_type:
        return "1,2"

    acc_lower = accident_type.lower()
    has_acc = '意外' in acc_lower
    has_ill = '疾病' in acc_lower

    if has_acc and not has_ill:
        return "1"
    if has_ill and not has_acc:
        return "2"
    return "1,2"


def extract_medical_type(treatment_type: str) -> str:
    """提取治疗类型码值

    规则：当治疗类型包含"住院"时，自动添加 ICU 码值
    """
    if not treatment_type:
        return "hospital,generalOutpatient"

    treatment_lower = treatment_type.lower()
    codes = [
        code for keyword, code in MEDICAL_TYPE_KEYWORD_TO_CODE_MAP.items()
        if keyword.lower() in treatment_lower
    ]

    # 当治疗类型包含"住院"时，自动添加 ICU 码值
    has_hospital = '住院' in treatment_type
    if has_hospital and 'icu' in MEDICAL_TYPE_KEYWORD_TO_CODE_MAP:
        icu_code = MEDICAL_TYPE_KEYWORD_TO_CODE_MAP['icu']
        if icu_code not in codes:
            codes.append(icu_code)
            logger.debug(f"检测到住院类型，自动添加 ICU 码值: {icu_code}")

    return ",".join(codes) if codes else "hospital,generalOutpatient"


def extract_fee_category(fee_type: str) -> str:
    """提取费用类型码值"""
    if not fee_type or fee_type == '所有':
        return ALL_FEE_CATEGORIES

    codes = [
        code for keyword, code in FEE_CATEGORY_KEYWORD_TO_CODE_MAP.items()
        if keyword in fee_type
    ]
    return ",".join(codes) if codes else 'other'


def extract_non_resp_type(non_resp_name: str) -> str:
    """提取责免类型码值"""
    for keyword, code in _NON_RESP_TYPE_MAP.items():
        if keyword in non_resp_name:
            return code
    return '01'


# ==========================================
# 医院范围解析函数
# ==========================================

def extract_brace_specifications(text: str) -> List[Dict[str, str]]:
    """提取花括号中的指定医院规格"""
    if not text:
        return []
    return [
        {'direction': direction, 'hospitals': hospitals.strip()}
        for direction, hospitals in _BRACE_REGEX.findall(text)
    ]


def remove_brace_content(text: str) -> str:
    """移除花括号内容"""
    if not text:
        return ""
    cleaned = _BRACE_REGEX.sub('', text)
    return re.sub(r'[、。，；;,\s]+$', '', cleaned.strip())


def normalize_hospital_names(hospital_names_str: str) -> str:
    """标准化医院名称"""
    if not hospital_names_str:
        return ""
    parts = _SEPARATOR_REGEX.split(hospital_names_str.strip())
    return ",".join(filter(None, (p.strip() for p in parts)))


def parse_hospital_param(text: str) -> HospitalParamDto:
    """ai解析失败时，使用规则解析医院属性参数"""
    # 1. 解析医院级别
    hospital_levels = None

    # 检查是否包含"一级"
    has_level_1 = "一级" in text
    # 检查是否包含"二级"
    has_level_2 = "二级" in text
    # 检查是否包含"三级"
    has_level_3 = "三级" in text

    # 检查"以上"/"及以上"关键词
    has_above = "以上" in text
    has_and_above = "及以上" in text

    if has_level_1:
        # 一级相关：一级、一级以上、一级及以上
        # 一级以上/及以上 = 一级、二级、三级
        hospital_levels = "1,2,3"
    elif has_level_2:
        if has_level_3:
            # 二级和三级同时存在（不管有没有"以上"）
            hospital_levels = "2,3"
        elif has_above or has_and_above:
            # 二级以上/及以上（无三级）= 二级、三级
            hospital_levels = "2,3"
        else:
            # 只有二级
            hospital_levels = "2"
    elif has_level_3:
        # 三级相关
        if has_above or has_and_above:
            # 三级以上/及以上 = 三级
            hospital_levels = "3"
        else:
            # 只有三级
            hospital_levels = "3"

    # 2. 解析医院等级
    hospital_grades = next(
        (code for key, code in _HOSPITAL_GRADE_MAP.items() if key in text),
        None
    )

    # 3. 解析医院性质
    hospital_natures = None
    for keyword, code in _HOSPITAL_NATURE_KEYWORD_TO_CODE_MAP.items():
        if keyword in text:
            hospital_natures = code
            break

    # 特殊处理："私立" 映射到 "民营" (02)
    if hospital_natures is None and "私立" in text:
        hospital_natures = "02"

    # 4. 解析医保定点（先检查"非医保定点"，避免包含"医保定点"字符串时误匹配）
    is_nssf_hospital = None
    if "非医保定点" in text:
        is_nssf_hospital = "N"
    elif "医保定点" in text:
        is_nssf_hospital = "Y"

    # 5. 解析医疗机构属性（类别）
    # 从 HOSPITAL_ORG_TYPE_MAP 构建关键词到码值的映射
    # HOSPITAL_ORG_TYPE_MAP: {'1': '医院', '2': '门诊', '6': '疾控机构', '7': '其他'}
    hospital_org_types = None
    _HOSPITAL_ORG_KEYWORD_TO_CODE_MAP = {v: k for k, v in HOSPITAL_ORG_TYPE_MAP.items()}

    # 检查文本中是否包含医疗机构属性关键词
    # 支持识别：医院、门诊、疾控机构、其他
    # 同时支持识别常见同义词：门诊部、卫生所、医务室、社区站 -> 门诊
    org_type_keywords = {
        "门诊": "2",      # 门诊
        "门诊部": "2",    # 门诊
        "卫生所": "2",    # 门诊
        "医务室": "2",    # 门诊
        "社区站": "2",    # 门诊
    }

    for keyword, code in org_type_keywords.items():
        if keyword in text:
            hospital_org_types = code
            break

    # 如果没有匹配到上述关键词，尝试从 HOSPITAL_ORG_TYPE_MAP 中匹配
    if hospital_org_types is None:
        for keyword, code in _HOSPITAL_ORG_KEYWORD_TO_CODE_MAP.items():
            if keyword in text:
                hospital_org_types = code
                break

    # 6. hospitalGrades 默认值填充
    # 如果 hospitalGrades 为 None 或空字符串，则填充默认值 "0,1,2,3,4"
    # 0=无等级, 1=特等, 2=甲等, 3=乙等, 4=丙等
    if not hospital_grades:
        hospital_grades = "0,1,2,3,4"

    return HospitalParamDto(
        hospitalArea=None,
        hospitalLevels=hospital_levels,
        hospitalGrades=hospital_grades,
        hospitalNatures=hospital_natures,
        hospitalOrgTypes=hospital_org_types,
        isNssfHospital=is_nssf_hospital,
        hospitalTypes=None
    )


def parse_base_hospital_scope(text: str, scope_id: int, confidence: str = "1") -> Optional[HospitalScopeDto]:
    """解析基础医院范围"""
    if not text.strip():
        return None

    # 判断是否为除外方向
    # 1. 显式"除外"关键词（但排除"指定医院除外"这种花括号语法）
    has_exclude = "除外" in text and "指定医院除外" not in text

    def_direction = "2" if has_exclude else "1"

    return HospitalScopeDto(
        id=str(scope_id),
        defDirection=def_direction,
        defLevel="1",
        hospitalParam=parse_hospital_param(text),
        confidence=confidence
    )


def parse_brace_specification(spec: Dict[str, str], scope_id: int, confidence: str = "1") -> HospitalScopeDto:
    """解析花括号指定医院"""
    return HospitalScopeDto(
        id=str(scope_id),
        defDirection="2" if spec['direction'] == "除外" else "1",
        defLevel="2",
        hospitalNames=normalize_hospital_names(spec['hospitals']),
        confidence=confidence
    )


def create_default_hospital_scope(scope_id: int) -> HospitalScopeDto:
    """创建默认医院范围"""
    logger.warning("医院范围文本为空，使用默认医院范围")
    return HospitalScopeDto(
        id=str(scope_id),
        defDirection="1",
        defLevel="1",
        hospitalParam=HospitalParamDto(
            hospitalLevels="2,3",
            hospitalNatures="04"
        ),
        confidence="0"
    )


def create_hospital_scopes(hospital_scope_text: str, start_id: int, confidence: str = "1") -> List[HospitalScopeDto]:
    """创建 HospitalScopeDto 列表

    支持按逗号等分隔符分拆医院范围，例如：
    "非医保定点，公立医院" 会分拆为两个医院范围
    """
    if not hospital_scope_text:
        return [create_default_hospital_scope(start_id)]

    result = []
    current_id = start_id

    # 1. 基础范围 - 移除花括号后按分隔符分拆
    base_text = remove_brace_content(hospital_scope_text)
    if base_text.strip():
        # 按分隔符分拆基础文本
        separators = [',', '，', '、']
        # 使用正则表达式按分隔符分拆，同时保留分隔符位置信息用于后续处理
        import re as re_module
        split_pattern = '[' + ''.join(separators) + ']'
        parts = re_module.split(split_pattern, base_text)

        # 对每个分拆后的部分分别解析
        for part in parts:
            part = part.strip()
            if part:  # 忽略空字符串
                if scope := parse_base_hospital_scope(part, current_id, confidence):
                    result.append(scope)
                    current_id += 1

    # 2. 指定范围（花括号）
    for spec in extract_brace_specifications(hospital_scope_text):
        result.append(parse_brace_specification(spec, current_id, confidence))
        current_id += 1

    return result or [create_default_hospital_scope(start_id)]


def create_hospital_scopes_with_llm_fallback(
    hospital_scope_text: str,
    confidence: str = "1",
    session_id: Optional[str] = None
) -> List[HospitalScopeDto]:
    """
    使用 LLM 模型解析医院范围，失败时回退到规则解析

    Args:
        hospital_scope_text: 医院范围描述文本
        confidence: 置信度评估结果（覆盖模型返回的 confidence）
        session_id: 会话ID，用于 Langfuse 追踪关联

    Returns:
        HospitalScopeDto 列表
    """
    # 1. 尝试使用 LLM 模型解析
    try:
        if hospital_scope_text and hospital_scope_text.strip():
            result = parse_hospital_scope_with_langfuse(
                hospital_scope_text=hospital_scope_text,
                session_id=session_id,
                metadata={"source": "code_parsers", "confidence_source": "confidence_evaluation"}
            )

            if result and result.get("hospitalScopes"):
                scopes = result["hospitalScopes"]
                dto_list = []
                for idx, scope_dict in enumerate(scopes, start=1):
                    # 构建 HospitalParamDto
                    param_dict = scope_dict.get("hospitalParam")
                    hospital_param = None
                    if param_dict:
                        # 为 hospitalLevels 和 hospitalGrades 填充默认值
                        hospital_levels = param_dict.get("hospitalLevels")
                        # 第一个 scope 且 hospital_levels 为空时设置默认值
                        if idx == 1 and (not hospital_levels or not hospital_levels.strip()):
                            hospital_levels = "2,3"  # 默认值：二级和三级医院
                            logger.info(f"第一个 scope 的 医院级别 为空，使用默认值: {hospital_levels}")

                        hospital_grades = param_dict.get("hospitalGrades")
                        # 第一个 scope 且 hospital_grades 为空时设置默认值
                        if idx == 1 and (not hospital_grades or not hospital_grades.strip()):
                            hospital_grades = "0,1,2,3,4"  # 默认值：所有等级
                            logger.info(f"第一个 scope 的 医院等级 为空，使用默认值: {hospital_grades}")

                        hospital_org_types = param_dict.get("hospitalOrgTypes")
                        # if not hospital_org_types:
                        #     hospital_org_types = "1,2,6,7"  # 默认值：医院机构类别
                        #todo: 2026-02-28 固定医院机构类别默认值，后续可以根据业务实际情况调整优化
                        # hospital_org_types = "1,2,6,7"  # 默认值：医院机构类别

                        hospital_param = HospitalParamDto(
                            hospitalArea=param_dict.get("hospitalArea"),
                            hospitalLevels=hospital_levels,
                            hospitalGrades=hospital_grades,
                            hospitalNatures=param_dict.get("hospitalNatures"),
                            hospitalOrgTypes=hospital_org_types,
                            isNssfHospital=param_dict.get("isNssfHospital"),
                            hospitalTypes=param_dict.get("hospitalTypes")
                        )

                    # 构建 HospitalScopeDto，使用外部传入的 confidence
                    dto = HospitalScopeDto(
                        id=str(idx),
                        defDirection=scope_dict.get("defDirection", "1"),
                        defLevel=scope_dict.get("defLevel", "1"),
                        hospitalNames=scope_dict.get("hospitalNames"),
                        hospitalParam=hospital_param,
                        confidence=confidence,  # 使用置信度评估结果的 confidence
                        position=scope_dict.get("position")
                    )
                    dto_list.append(dto)

                if dto_list:
                    logger.info(f"LLM 医院范围解析成功: '{hospital_scope_text[:50]}...' -> {len(dto_list)} 个范围对象")
                    return dto_list

    except Exception as e:
        logger.warning(f"LLM 医院范围解析失败，将回退到规则解析: {e}")

    # 2. 回退到规则解析
    logger.info(f"使用规则解析医院范围: '{hospital_scope_text[:50]}...'")
    return create_hospital_scopes(hospital_scope_text, 1, confidence)


# ==========================================
# 辅助工具函数
# ==========================================

def extract_days_from_text(text: str) -> Optional[int]:
    """从文本中提取天数"""
    if match := _DAYS_REGEX.search(text):
        return int(match.group(1))
    return None


def _make_rule(rule_id: int, rule_type: str, params: Dict[str, Any], position: Any = None, confidence: str = "1") -> RuleDto:
    """内部辅助：构建 RuleDto"""
    return RuleDto(
        id=str(rule_id),
        ruleType=rule_type,
        ruleParams=json.dumps(params, ensure_ascii=False),
        confidence=confidence,
        position=position
    )


# ==========================================
# 转换主函数
# ==========================================

def convert_multi_scope_to_pay_scope_dto_list(
    base_data: Union[Dict, List, None],
    multi_data: Union[Dict, List, None],
    confidence_eval_result: Optional[Dict] = None,
    session_id: Optional[str] = None
) -> List[PayScopeDto]:
    """转换赔付范围"""
    # raw_items = (base_data if isinstance(base_data, list) else ([base_data] if base_data else [])) + \
    #             (multi_data if isinstance(multi_data, list) else ([multi_data] if multi_data else []))
    raw_items = (multi_data if isinstance(multi_data, list) else ([multi_data] if multi_data else []))

    if not raw_items:
        logger.warning(f"[session_id={session_id}] 拆解值为空，未能从文档中提取到赔付范围")
        return []

    # 去重逻辑
    unique_items = {}
    for item in raw_items:
        if not isinstance(item, dict): continue
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key not in unique_items:
            unique_items[key] = item

    pay_scope_list = []
    # enumerate 从 0 开始，但 PayScopeDto.id 从 1 开始
    for idx, item in enumerate(unique_items.values(), start=1):
        # 情形索引 = idx - 1（用于置信度查询）
        scenario_idx = idx - 1

        # 获取字段级置信度，传入 scenario_idx
        hospital_confidence = _get_field_confidence(
            confidence_eval_result, '医院范围', scenario_idx
        )
        invoice_confidence = _get_field_confidence(
            confidence_eval_result, '发票', scenario_idx
        )

        # 1. 先创建 hospitalScopes
        hospital_scopes = create_hospital_scopes_with_llm_fallback(
            item.get('医院范围', ''),
            hospital_confidence,
            session_id=session_id
        )

        # 2. 检查是否需要删除特需治疗类型
        should_exclude_special = any(
            scope.defDirection == "2" and
            scope.defLevel == "2" and
            scope.hospitalNames and
            "特需" in scope.hospitalNames
            for scope in hospital_scopes
        )

        # 3. 生成治疗类型（根据医院范围条件过滤特需类型）
        medical_type_codes = extract_medical_type(item.get('治疗类型'))
        if should_exclude_special:
            codes = [c.strip() for c in medical_type_codes.split(',') if c.strip()]
            filtered_codes = [c for c in codes if c not in ('specialDemandWard', 'specialPatient')]
            medical_type_codes = ','.join(filtered_codes) if filtered_codes else 'hospital,generalOutpatient'

        # 4. 创建 PayScopeDto
        pay_scope_list.append(PayScopeDto(
            id=str(idx),
            claimNature=ClaimNatureDto(id="1", claimNature=extract_claim_nature(item.get('事故类型')), confidence="1"),
            medicalType=MedicalTypeDto(id="1", medicalType=medical_type_codes, confidence="1"),
            hospitalScopes=hospital_scopes,
            feeCategory=FeeCategoryDto(id="1", feeCategory=extract_fee_category(item.get('费用类型', '所有')), confidence="1"),
            invoiceRules=create_invoice_rules(item.get('发票', ''), 1, invoice_confidence),
            sceneRules=create_scene_rules(item.get('场景', ''), 1)
        ))

    return pay_scope_list


def _get_field_confidence(
    confidence_eval_result: Optional[Dict],
    field_name: str,
    scenario_idx: Optional[int] = None
) -> str:
    """
    从置信度评估结果中获取指定字段的置信度分数

    Args:
        confidence_eval_result: 置信度评估结果字典
        field_name: 字段名称（如'医院范围'、'发票'）
        scenario_idx: 情形索引，对于多情形字段需要传入

    Returns:
        0-1 范围的置信度字符串，默认为 "1"
    """
    if not confidence_eval_result or field_name not in confidence_eval_result:
        return "1"

    field_result = confidence_eval_result.get(field_name, {})
    if not isinstance(field_result, dict):
        return "1"

    score = field_result.get('score')
    if score is None:
        return "1"

    # 检查是否有 scenario 字段（多情形字段）
    scenarios = field_result.get('scenario')
    if scenarios is not None:
        # 多情形字段：检查当前情形是否在 scenario 列表中
        if scenario_idx is not None and scenario_idx in scenarios:
            return str(score / 10)
        else:
            # 当前情形不在评估范围内，返回 "0"
            return "0"
    else:
        # 单情形字段（既往症、等待期）：直接返回分数
        return str(score / 10)


def convert_pay_param_to_rule_dto_list(
    waiting_period_json: Optional[Dict],
    past_illness_json: Optional[Dict],
    confidence_eval_result: Optional[Dict] = None
) -> List[RuleDto]:
    """转换等待期和既往症规则"""
    rules = []
    rule_id = 1

    # 获取字段级置信度
    waiting_period_confidence = _get_field_confidence(confidence_eval_result, '等待期')
    past_illness_confidence = _get_field_confidence(confidence_eval_result, '既往症')

    if waiting_period_json:
        wp_rules = create_waiting_period_rules(waiting_period_json, rule_id, waiting_period_confidence)
        rules.extend(wp_rules)
        rule_id += len(wp_rules)

    if past_illness_json:
        rules.extend(create_past_illness_rules(past_illness_json, rule_id, past_illness_confidence))

    return rules


def convert_to_non_responsibility_dto_list(non_resp_list: List[Dict]) -> List[NonResponsibilityDto]:
    """转换责任免除"""
    return [
        NonResponsibilityDto(
            id=str(idx),
            type=extract_non_resp_type(item.get('nonResponsibilityName', '')),
            tagNames=item.get('nonResponsibilityInfo', ''),
            confidence="1",
            position=item.get('position')
        )
        for idx, item in enumerate(non_resp_list, start=1)
        if isinstance(item, dict)
    ]


def create_invoice_rules(invoice_text: str, start_id: int, confidence: str = "1") -> List[RuleDto]:
    """创建发票规则

    根据接口规范（models/接口规范.md - RuleDto发票规则）：
    - E2_001: 获得统筹支付
    - E2_002: 社保账单
    - E2_003: 自费
    - E2_999: 自定义

    处理逻辑：
    - 只在发票文本中明确包含关键字时才添加对应规则，不预设默认值
    - 包含"统筹"或"统筹支付" -> E2_001
    - 包含"社保账单" -> E2_002
    - 包含"自费" -> E2_003
    """
    rules = []
    current_id = start_id
    text = (invoice_text or '').strip()

    # 如果文本为空，不添加任何规则
    if not text:
        return rules

    # 按优先级顺序检查关键字

    # E2_001: 获得统筹支付
    if '统筹' in text:
        rules.append(_make_rule(current_id, "E2_001", {"P1": "Y"}, confidence=confidence))
        current_id += 1

    # E2_002: 社保账单
    if '社保账单' in text:
        rules.append(_make_rule(current_id, "E2_002", {"P1": "Y"}, confidence=confidence))
        current_id += 1

    # E2_003: 自费
    if '自费' in text:
        rules.append(_make_rule(current_id, "E2_003", {"P1": "Y"}, confidence=confidence))
        current_id += 1

    return rules


# ==========================================
# 场景规则解析常量
# ==========================================

# 场景关键词到规则类型的映射
# 格式: (关键词列表, rule_type, has_params)
# has_params: True 表示需要解析 P1/P2 参数，False 表示参数为空，'custom' 表示使用 E1_999
_SCENE_KEYWORD_MAPPING = [
    # E1_001 - 医保身份投保
    (["医保身份投保", "需有医保身份", "需提供医保身份", "仅限医保身份"], "E1_001", True),
    (["非医保身份投保", "无医保身份要求"], "E1_018", False),

    # E1_002 - 疾病范围
    (["除外疾病", "除以下疾病", "不承担下列疾病"], "E1_002", "exclude"),
    (["包含疾病", "承担以下疾病"], "E1_002", "include"),

    # E1_003 - 诊疗范围
    (["除外诊疗", "除以下诊疗", "不承担下列诊疗"], "E1_003", "exclude"),
    (["包含诊疗", "承担以下诊疗"], "E1_003", "include"),

    # E1_004 - 材料范围
    (["除外材料", "除以下材料", "不承担下列材料"], "E1_004", "exclude"),
    (["包含材料", "承担以下材料"], "E1_004", "include"),

    # E1_005 - 药品范围
    (["除外药品", "除以下药品", "不承担下列药品"], "E1_005", "exclude"),
    (["包含药品", "承担以下药品"], "E1_005", "include"),

    # E1_006 - 手术范围
    (["除外手术", "除以下手术", "不承担下列手术"], "E1_006", "exclude"),
    (["包含手术", "承担以下手术"], "E1_006", "include"),

    # E1_007 - 相同事故原因且在住院前后的特定门诊
    (["住院前后", "住院前后门诊", "相同事故原因且在住院前后"], "E1_007", False),

    # E1_008 - 门诊手术判定规则
    (["门诊手术", "门诊手术费用"], "E1_008", False),

    # E1_009 - 重大疾病判定规则
    (["重大疾病", "重症"], "E1_009", False),

    # E1_010 - 非重大疾病判定规则
    (["非重大疾病", "非重症"], "E1_010", False),

    # E1_011 - 门诊手术同日归并规则
    (["同日归并", "同日门诊"], "E1_011", False),

    # E1_012 - 门诊手术向前归并规则
    (["向前归并", "门诊向前归并"], "E1_012", False),

    # E1_013 - 恶性肿瘤判定规则
    (["恶性肿瘤", "癌症"], "E1_013", False),

    # E1_014 - 非恶性肿瘤判定规则
    (["非恶性肿瘤", "良性肿瘤"], "E1_014", False),

    # E1_015 - 相同事故原因住院归并规则
    (["相同事故原因住院", "住院归并"], "E1_015", False),

    # E1_016 - 入院日距事故日的特定天数规则
    (["入院日距事故日", "距事故日"], "E1_016", False),

    # E1_017 - 无投保地社保参保证明拒赔规则
    (["无社保参保证明", "无投保地社保", "无社保证明拒赔"], "E1_017", False),

    # E1_018 - 非社保身份投保
    (["非社保身份", "无社保要求", "不限社保身份"], "E1_018", False),

    # E1_019 - 与前次疾病出险间隔天数判定规则
    (["与前次疾病", "间隔天数"], "E1_019", False),

    # E1_020 - 特定出险保单年度判定规则
    (["保单年度", "第.*年出险"], "E1_020", False),

    # E1_021 - 特定出险年龄判定规则
    (["出险年龄", "年龄限制"], "E1_021", False),

    # E1_022 - 确诊日期有效期
    (["确诊日期", "有效期"], "E1_022", False),

    # E1_023 - 就诊行为发生在距离事故日期的180天内
    (["180天内", "半年内"], "E1_023", False),

    # E1_024 - 重症监护室住院判定规则
    (["重症监护室", "ICU", "重症监护"], "E1_024", False),

    # E1_025 - 24小时内的出入院判定规则
    (["24小时", "24小时内出入院", "24小时内住院"], "E1_025", False),
]

# 标签提取正则表达式 - 用于提取包含/除外后的具体内容
_LABEL_EXTRACT_REGEX = re.compile(r'[：:、，,\s]*(.{2,20?})[、，,和及与或\n]|$')


def _extract_labels_after_keyword(text: str, keyword: str) -> Optional[str]:
    """
    从文本中提取关键词后的标签列表

    Args:
        text: 场景文本
        keyword: 关键词（如"除外疾病"、"包含药品"）

    Returns:
        提取的标签字符串，用逗号分隔
    """
    idx = text.find(keyword)
    if idx == -1:
        return None

    # 获取关键词后的内容
    after_keyword = text[idx + len(keyword):].strip()

    # 尝试提取用标点符号分隔的标签
    # 支持多种分隔符：顿号、逗号、分号、空格、换行
    labels = re.split(r'[、。，；;,\s\n]+', after_keyword)

    # 过滤掉空字符串和过短的标签
    valid_labels = [label.strip() for label in labels if label.strip() and len(label.strip()) >= 2]

    # 限制标签数量，避免提取过多无关内容
    if valid_labels:
        return ",".join(valid_labels[:10])

    return None


def _parse_scene_rule_params(scene_text: str, rule_type: str, param_type) -> Dict[str, Any]:
    """
    解析场景规则参数

    Args:
        scene_text: 场景文本
        rule_type: 规则类型
        param_type: 参数类型 (True/False/'exclude'/'include')

    Returns:
        参数字典 {"P1": ..., "P2": ...}
    """
    # E1_001: 医保身份投保 - 参数为"是/否"
    if rule_type == "E1_001":
        return {"P1": "Y"}

    # E1_018: 非社保身份投保 - 参数为空
    if rule_type == "E1_018":
        return {}

    # E1_002~E1_006: 疾病/诊疗/材料/药品/手术范围
    if rule_type in ["E1_002", "E1_003", "E1_004", "E1_005", "E1_006"]:
        if isinstance(param_type, str) and param_type in ["exclude", "include"]:
            # P1: Y=包含, N=除外
            p1_value = "Y" if param_type == "include" else "N"

            # 尝试提取具体的标签
            keyword_map = {
                "E1_002": "疾病",
                "E1_003": "诊疗",
                "E1_004": "材料",
                "E1_005": "药品",
                "E1_006": "手术"
            }

            keyword = keyword_map.get(rule_type, "")
            labels = None

            # 尝试多种关键词组合
            for kw in [f"除外{keyword}", f"包含{keyword}", f"除以下{keyword}", f"不承担下列{keyword}"]:
                if kw in scene_text:
                    labels = _extract_labels_after_keyword(scene_text, kw)
                    if labels:
                        break

            if labels:
                return {"P1": p1_value, "P2": labels}
            else:
                return {"P1": p1_value}

        return {}

    # E1_007~E1_025: 其他规则 - 参数为空
    return {}


def create_scene_rules(scene_text: str, start_id: int) -> Optional[List[RuleDto]]:
    """
    创建场景规则

    根据场景文本解析并映射到对应的 E1_* 规则类型

    Args:
        scene_text: 场景描述文本，如 "一般疾病住院"、"除外牙科生育" 等
        start_id: 起始 ID

    Returns:
        RuleDto 列表，如果无需场景规则则返回 None
    """
    if not scene_text or not scene_text.strip():
        return None

    rules = []
    current_id = start_id
    text = scene_text.strip()

    # 遍历所有可能的关键词映射
    for keywords, rule_type, param_type in _SCENE_KEYWORD_MAPPING:
        # 检查是否匹配任何关键词
        matched = False
        for keyword in keywords:
            if keyword in text:
                matched = True
                break

        if matched:
            params = _parse_scene_rule_params(text, rule_type, param_type)
            rules.append(_make_rule(current_id, rule_type, params))
            current_id += 1

    # 如果没有匹配到任何预定义规则，使用 E1_999 自定义规则
    # if not rules:
    rules.append(_make_rule(start_id, "E1_999", {"P1": f"#{text}#"}))

    return rules


def create_waiting_period_rules(waiting_period_json: Dict, start_id: int, confidence: str = "1") -> List[RuleDto]:
    """创建等待期规则

    根据接口规范（models/接口规范.md - RuleDto判责参数）：
    - E3_001: 等待期（按疾病意外）：{"P1":"1/2","P2":"天数"}
      - P1: 1=疾病, 2=意外
      - P2: 天数

    处理逻辑：
    - 只处理"新保等待期"字段
    - 自动生成两条规则：疾病(P1="1")和意外(P1="2")
    - 示例1: {"新保等待期": "30天"} -> 两条规则: {"P1":"1","P2":"30"} 和 {"P1":"2","P2":"30"}
    - 示例2: {"新保等待期": "无等待期"} -> 两条规则: {"P1":"1","P2":"0"} 和 {"P1":"2","P2":"0"}
    - 示例3: {"新保等待期": "0天"} -> 两条规则: {"P1":"1","P2":"0"} 和 {"P1":"2","P2":"0"}
    """
    rules = []
    val = waiting_period_json.get('新保等待期', '')

    # 空值不生成规则
    if not val:
        return rules

    # 确定天数
    # 检查是否为"无等待期"（匹配"无"、"没有"等否定词）
    if ('无' in val or '没有' in val) and '等待期' in val:
        days = "0"
    else:
        # 提取天数
        extracted_days = extract_days_from_text(val)
        days = str(extracted_days) if extracted_days is not None else val

    # 生成两条规则：疾病(1)和意外(2)
    rules.append(_make_rule(
        start_id,
        "E3_001",
        {"P1": "1", "P2": days},
        confidence=confidence
    ))
    rules.append(_make_rule(
        start_id + 1,
        "E3_001",
        {"P1": "2", "P2": days},
        confidence=confidence
    ))

    return rules


def create_past_illness_rules(past_illness_json: Dict, start_id: int, confidence: str = "1") -> List[RuleDto]:
    """创建既往症规则"""
    pay_param = past_illness_json.get('本次处理的保险责任的既往症赔付参数', '')

    rules = []

    # 确定规则类型和参数
    if '不承担严重既往症' in pay_param:
        # E3_004: 特定疾病既往症（不承担严重既往症）
        severe_scope = past_illness_json.get('严重既往症范围', '严重既往症')
        rules.append(_make_rule(start_id, "E3_004", {"P1": "N", "P2": severe_scope}, confidence=confidence))
        # E3_003: 既往症（承担一般既往症）
        rules.append(_make_rule(start_id + 1, "E3_003", {"P1": "Y"}, confidence=confidence))
    elif '不承担' in pay_param:
        # 不承担既往症
        rules.append(_make_rule(start_id, "E3_003", {"P1": "N"}, confidence=confidence))
    elif '承担' in pay_param:
        # 承担既往症
        rules.append(_make_rule(start_id, "E3_003", {"P1": "Y"}, confidence=confidence))

    return rules
