"""
Date: 2026-01-05 10:02:54
LastEditTime: 2026-01-22 11:27:07
Description: 这是默认设置,可以在设置》工具》File Description中进行配置
"""
from typing import List, Optional

from pydantic import BaseModel, Field

# 从code_tables模块导入码表转换工具
from .code_tables import (
    convert_code_to_text
)

# 为向后兼容，创建简短的别名函数
convert_file_class_code_to_text = lambda code: convert_code_to_text("fileClass", code)
convert_policy_type_code_to_text = lambda code: convert_code_to_text("policyType", code)
convert_claim_nature_code_to_text = lambda code: convert_code_to_text("claimNature", code)
convert_hospital_level_code_to_text = lambda code: convert_code_to_text("hospitalLevels", code)
convert_hospital_grade_code_to_text = lambda code: convert_code_to_text("hospitalGrades", code)
convert_hospital_nature_code_to_text = lambda code: convert_code_to_text("hospitalNatures", code)
convert_hospital_org_type_code_to_text = lambda code: convert_code_to_text("hospitalOrgTypes", code)
convert_nssf_hospital_code_to_text = lambda code: convert_code_to_text("isNssfHospital", code)
convert_def_direction_code_to_text = lambda code: convert_code_to_text("defDirection", code)
convert_def_level_code_to_text = lambda code: convert_code_to_text("defLevel", code)
convert_non_responsibility_type_code_to_text = lambda code: convert_code_to_text("type", code)
convert_medical_type_code_to_text = lambda code: convert_code_to_text("medicalType", code)
convert_fee_category_code_to_text = lambda code: convert_code_to_text("feeCategory", code)
convert_rule_type_code_to_text = lambda code: convert_code_to_text("ruleType", code)

# ==========================================
# 基础 DTO 定义 (公共组件)
# ==========================================

class RuleDto(BaseModel):
    """
    规则实体 (用于发票规则、场景规则、判责参数)
    """
    id: str = Field(..., description="唯一标识")
    ruleType: str = Field(..., description="参数类型码值 (如 E1_001, E2_001, E3_001 等)")
    ruleParams: str = Field(..., description="参数值，JSON文本格式，例如: '{\"P1\":\"Y\"}'")
    confidence: str = Field(..., description="置信度百分比，如 1 或 0.8")
    position: Optional[str] = Field(None, description="位置坐标，如 [1069, 398, 1343, 448]")

class NonResponsibilityDto(BaseModel):
    """
    责任免除信息
    """
    id: str = Field(..., description="唯一标识")
    type: str = Field(..., description="免责分类：01-药品, 02-诊疗, 03-材料, 04-诊断, 05-手术")
    tagNames: Optional[str] = Field(None, description="免除标签内容，多个之间用英文逗号隔开")
    confidence: str = Field(..., description="置信度百分比")
    position: Optional[str] = Field(None, description="位置坐标")

# ==========================================
# 请求体相关 DTO (Request)
# ==========================================

class LiabilityDto(BaseModel):
    """
    请求体 - 责任信息
    """
    id: int = Field(..., description="责任ID")
    liabCode: str = Field(..., description="责任代码")
    liabName: str = Field(..., description="责任名称")

class PlanDto(BaseModel):
    """
    请求体 - 计划条款信息
    """
    id: int = Field(..., description="计划ID")
    planCode: str = Field(..., description="计划代码")
    planName: str = Field(..., description="计划名称")
    planVersion: str = Field(..., description="计划版本")
    clauseCode: str = Field(..., description="条款（险种）代码")
    clauseName: str = Field(..., description="条款（险种）名称")
    liabilityList: List[LiabilityDto] = Field(..., description="责任信息列表")

class FileDto(BaseModel):
    """
    请求体 - 文件信息
    """
    id: int = Field(..., description="文件ID")
    fileClass: str = Field(..., description="文件类型：01-协议, 02-特别约定, 03-条款, 04-其他")
    fileFormat: str = Field(..., description="文件格式")
    fileUrl: str = Field(..., description="文件地址")
    fileExternalUrl: Optional[str] = Field(None, description="可选的外部文件地址；公开演示默认不依赖对象存储权限。")
    fileName: str = Field(..., description="文件名称")

    @property
    def fileClassDisplay(self) -> str:
        """转换fileClass为可读文本"""
        return convert_code_to_text("fileClass", self.fileClass)

class ProductInfoDto(BaseModel):
    """
    请求体 - 产品信息
    """
    id: int = Field(..., description="产品ID")
    orgCode: str = Field(..., description="机构代码")
    policyType: str = Field("1", description="保单类型：1-个险（默认）；2-团险")
    groupPolicyNo: Optional[str] = Field(None, description="团单号码，当 policyType为 2 时必传")
    planList: List[PlanDto] = Field(..., description="计划条款信息列表")
    fileList: List[FileDto] = Field(..., description="文件信息列表")

class DsRequest(BaseModel):
    """
    ds_request 请求体根对象
    """
    productInfo: ProductInfoDto = Field(..., description="产品结构信息")
    transDate: int = Field(..., description="交易时间，毫秒时间戳")
    transNo: str = Field(..., description="交易流水号UUID")
    systemCode: str = Field(..., description="调用系统编码")
    policyNo: Optional[str] = Field(None, description="个险为groupPolicyNo复用，个险为planNo复用，条款拆解端后处理添加，跟踪识别用，不作为后端传入参数")
    planIds: Optional[List[int]] = Field(None, description="计划ID列表，响应时会原样回传")
