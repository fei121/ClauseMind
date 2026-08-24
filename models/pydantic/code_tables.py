"""
码表映射模块
集中管理所有码表转换函数
所有码表均从 sys_code.xlsx 动态加载

Date: 2026-01-07
Updated: 2026-01-20 - 改为从 xlsx 动态加载码表，移除所有硬编码
"""

from pathlib import Path
from typing import Dict
import pandas as pd

# 获取当前文件所在目录
_CURRENT_DIR = Path(__file__).parent
_XLSX_PATH = _CURRENT_DIR / "sys_code.xlsx"


def _load_code_table_from_xlsx(code_category: str) -> Dict[str, str]:
    """
    从 xlsx 文件加载指定类别的码表

    Args:
        code_category: 码表类别名称

    Returns:
        码值 -> 文本的映射字典
    """
    if not _XLSX_PATH.exists():
        raise FileNotFoundError(f"码表文件不存在: {_XLSX_PATH}")

    try:
        df = pd.read_excel(_XLSX_PATH)
        # 筛选指定类别且未删除的记录
        filtered = df[
            (df['code_category'] == code_category) &
            (df['is_deleted'] == 'N')
        ][['key_code', 'key_name']]

        # 将 key_code 转换为字符串并构建映射
        result = {}
        for _, row in filtered.iterrows():
            key_code = str(row['key_code'])
            key_name = row['key_name']
            if pd.notna(key_name):  # 跳过 NaN 的 key_name
                result[key_code] = key_name

        return result
    except Exception as e:
        raise RuntimeError(f"加载码表 '{code_category}' 失败: {e}")


# ==========================================
# 从 xlsx 动态加载所有码表
# ==========================================

NON_RESPONSIBILITY_TYPE_MAP = _load_code_table_from_xlsx("免责分类")
MEDICAL_TYPE_MAP = _load_code_table_from_xlsx("医疗类型")
HOSPITAL_LEVEL_MAP = _load_code_table_from_xlsx("医院等级")
HOSPITAL_NATURE_MAP = _load_code_table_from_xlsx("医院性质")
HOSPITAL_ORG_TYPE_MAP = _load_code_table_from_xlsx("医疗机构属性")
CLAIM_NATURE_MAP = _load_code_table_from_xlsx("事故性质")
DEF_DIRECTION_MAP = _load_code_table_from_xlsx("def_direction")
DEF_LEVEL_MAP = _load_code_table_from_xlsx("资源配置方式 ")
FEE_CATEGORY_MAP = _load_code_table_from_xlsx("费用大项")
HOSPITAL_GRADE_MAP = _load_code_table_from_xlsx("hospital_grade")

# 文本 -> 码值映射（用于从文本中提取码值），从 FEE_CATEGORY_MAP 动态生成
FEE_CATEGORY_KEYWORD_TO_CODE_MAP = {v: k for k, v in FEE_CATEGORY_MAP.items()}
# 所有费用类型（默认）- 从 FEE_CATEGORY_MAP 中提取常用项
ALL_FEE_CATEGORIES = ','.join(sorted(FEE_CATEGORY_MAP.keys()))



# 文件类型码表 - xlsx 中无对应，保留硬编码
FILE_CLASS_MAP = {
    "01": "协议文档",
    "02": "特别约定文档",
    "03": "条款文档",
    "04": "其他文档",
    "附件下载": "附件下载文档"
}

# 保单类型码表 - xlsx 中无对应，保留硬编码
POLICY_TYPE_MAP = {
    "1": "个险",
    "2": "团险"
}

# 文本 -> 码值映射（MEDICAL_TYPE_MAP的逆映射）
MEDICAL_TYPE_KEYWORD_TO_CODE_MAP = {v: k for k, v in MEDICAL_TYPE_MAP.items()}
# 添加 ICU 别名
if "ICU病房" in MEDICAL_TYPE_MAP.values():
    for code, name in MEDICAL_TYPE_MAP.items():
        if name == "ICU病房":
            MEDICAL_TYPE_KEYWORD_TO_CODE_MAP["icu"] = code
            break

# 文本 -> 码值映射（NON_RESPONSIBILITY_TYPE_MAP的逆映射）
# 去掉 "非责标签" 后缀，例如 "药品非责标签" -> "药品"
NON_RESP_TYPE_KEYWORD_TO_CODE_MAP = {
    v.replace("非责标签", ""): k for k, v in NON_RESPONSIBILITY_TYPE_MAP.items()
}

# 文本 -> 码值映射（HOSPITAL_GRADE_MAP的逆映射）
HOSPITAL_GRADE_KEYWORD_TO_CODE_MAP = {v: k for k, v in HOSPITAL_GRADE_MAP.items()}


# 医院医保定点码表 - xlsx 中无对应，保留硬编码
NSSF_HOSPITAL_MAP = {
    "1": "是",
    "2": "否"
}

# 规则类型码表 - xlsx 中无对应，保留硬编码
RULE_TYPE_MAP = {
    # E1 系列 - 医保身份投保
    "E1_001": "医保身份投保",
    "E1_002": "疾病范围",
    "E1_003": "诊疗范围",
    "E1_004": "材料范围",
    "E1_005": "药品范围",
    "E1_006": "手术范围",
    "E1_007": "相同事故原因且在住院前后的特定门诊",
    "E1_008": "门诊手术判定规则",
    "E1_009": "重大疾病判定规则",
    "E1_010": "非重大疾病判定规则",
    "E1_011": "门诊手术同日归并规则",
    "E1_012": "门诊手术向前归并规则",
    "E1_013": "恶性肿瘤判定规则",
    "E1_014": "非恶性肿瘤判定规则",
    "E1_015": "相同事故原因住院归并规则",
    "E1_016": "入院日距事故日的特定天数规则",
    "E1_017": "无投保地社保参保证明拒赔规则",
    "E1_018": "非社保身份投保",
    "E1_019": "与前次疾病出险间隔天数判定规则",
    "E1_020": "特定出险保单年度判定规则",
    "E1_021": "特定出险年龄判定规则",
    "E1_022": "确诊日期有效期",
    "E1_023": "就诊行为发生在距离事故日期的180天内",
    "E1_024": "重症监护室住院判定规则",
    "E1_025": "24小时内的出入院判定规则",
    "E1_999": "自定义",
    # E2 系列 - 社保/自费
    "E2_001": "获得统筹支付",
    "E2_002": "社保账单",
    "E2_003": "自费",
    "E2_999": "自定义",
    # E3 系列 - 等待期/既往症
    "E3_001": "等待期（按疾病意外）",
    "E3_002": "等待期（按诊断）",
    "E3_003": "既往症",
    "E3_004": "特定疾病既往症",
    "E3_005": "等待期内后续治疗",
    "E3_999": "自定义"
}


# ==========================================
# 所有码表映射字典
# 用于根据字段名快速查找对应的映射字典
# ==========================================
CODE_TABLE_MAPS = {
    "fileClass": FILE_CLASS_MAP,
    "policyType": POLICY_TYPE_MAP,
    "claimNature": CLAIM_NATURE_MAP,
    "hospitalLevels": HOSPITAL_LEVEL_MAP,
    "hospitalGrades": HOSPITAL_GRADE_MAP,
    "hospitalNatures": HOSPITAL_NATURE_MAP,
    "hospitalOrgTypes": HOSPITAL_ORG_TYPE_MAP,
    "isNssfHospital": NSSF_HOSPITAL_MAP,
    "defDirection": DEF_DIRECTION_MAP,
    "defLevel": DEF_LEVEL_MAP,
    "type": NON_RESPONSIBILITY_TYPE_MAP,  # NonResponsibility.type
    "medicalType": MEDICAL_TYPE_MAP,
    "feeCategory": FEE_CATEGORY_MAP,
    "ruleType": RULE_TYPE_MAP,
}

def convert_code_to_text(field_name: str, code: str) -> str:
    """
    通用码表转换函数，根据字段名自动选择对应的映射字典

    Args:
        field_name: 字段名，如"fileClass"、"policyType"等
        code: 需要转换的码值

    Returns:
        转换后的文本，如果未找到对应的映射字典则返回原值
    """
    code_map = CODE_TABLE_MAPS.get(field_name)
    if not code_map:
        return code
    return code_map.get(code, code)