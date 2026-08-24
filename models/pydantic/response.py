"""
Date: 2026-01-05 10:04:10
LastEditTime: 2026-01-20 16:55:32
Description: 这是默认设置,可以在设置》工具》File Description中进行配置
"""
from typing import List, Optional
from pydantic import BaseModel, Field
from models.pydantic.request import RuleDto, NonResponsibilityDto

# ==========================================
# 响应体相关 DTO (Response)
# ==========================================

class ClaimNatureDto(BaseModel):
    """
    赔付范围 - 事故类型
    """
    id: str = Field(..., description="ID")
    claimNature: str = Field(..., description="事故类型：1-意外, 2-疾病。多个用逗号隔开")
    confidence: str = Field(..., description="置信度百分比")
    position: Optional[dict] = Field(None, description="位置坐标")

class MedicalTypeDto(BaseModel):
    """
    赔付范围 - 治疗类型
    """
    id: str = Field(..., description="ID")
    medicalType: str = Field(..., description="治疗类型码值，多个用逗号隔开")
    confidence: str = Field(..., description="置信度百分比")
    position: Optional[dict] = Field(None, description="位置坐标")

class HospitalParamDto(BaseModel):
    """
    医院范围 - 医院属性配置值
    """
    hospitalArea: Optional[str] = Field(None, description="医院所属地区，省-市-区拼接")
    hospitalLevels: Optional[str] = Field(None, description="级别：0无分级, 1一级, 2二级, 3三级")
    hospitalGrades: Optional[str] = Field(None, description="等级：0无等级, 1特等, 2甲等, 3乙等, 4丙等")
    hospitalNatures: Optional[str] = Field(None, description="机构性质：01合资, 02民营, 03外资, 04公立")
    hospitalOrgTypes: Optional[str] = Field(None, description="机构类别：1医院, 2门诊, 6疾控, 7其他")
    isNssfHospital: Optional[str] = Field(None, description="医保定点：Y是, N否")
    hospitalTypes: Optional[str] = Field(None, description="医院类别码值")

class HospitalScopeDto(BaseModel):
    """
    赔付范围 - 医院范围
    """
    id: str = Field(..., description="ID")
    defDirection: str = Field(..., description="运算符：1-包含, 2-除外")
    defLevel: str = Field(..., description="配置方式：1-按医院属性配置, 2-指定医院")
    hospitalNames: Optional[str] = Field(None, description="医院名称清单，方式为2时必传")
    hospitalParam: Optional[HospitalParamDto] = Field(None, description="医院属性配置，方式为1时必传")
    confidence: str = Field(..., description="置信度百分比")
    position: Optional[str] = Field(None, description="位置坐标")

class FeeCategoryDto(BaseModel):
    """
    赔付范围 - 费用类型
    """
    id: str = Field(..., description="ID")
    feeCategory: str = Field(..., description="费用类型码值，多个用逗号隔开")
    confidence: str = Field(..., description="置信度百分比")
    position: Optional[str] = Field(None, description="位置坐标")

class PayScopeDto(BaseModel):
    """
    责任层 - 赔付范围
    """
    id: str = Field(..., description="情形序号，从1开始自增")
    claimNature: ClaimNatureDto = Field(..., description="事故类型")
    medicalType: MedicalTypeDto = Field(..., description="治疗类型")
    hospitalScopes: List[HospitalScopeDto] = Field(..., description="医院范围列表")
    feeCategory: FeeCategoryDto = Field(..., description="费用类型")
    invoiceRules: List[RuleDto] = Field(..., description="发票规则")
    sceneRules: Optional[List[RuleDto]] = Field(None, description="场景规则")
    payParam: Optional[List[RuleDto]] = Field(None, description="判责参数")

class LiabilityResultDto(BaseModel):
    """
    拆解结果（责任层）
    """
    id: int = Field(..., description="责任ID")
    liabCode: str = Field(..., description="责任代码")
    liabName: str = Field(..., description="责任名称")
    payScopeList: List[PayScopeDto] = Field(..., description="赔付范围列表")
    nonResponsibilityList: Optional[List[NonResponsibilityDto]] = Field(None, description="责免信息列表")
    sessionId: Optional[str] = Field(None, description="追踪会话ID，用于日志追踪")

class PlanResultDto(BaseModel):
    """
    拆解结果（计划条款层）
    """
    id: int = Field(..., description="计划ID")
    planCode: str = Field(..., description="计划代码")
    planName: str = Field(..., description="计划名称")
    planVersion: str = Field(..., description="计划版本")
    clauseCode: str = Field(..., description="条款代码")
    clauseName: str = Field(..., description="条款名称")
    liabilityResultList: List[LiabilityResultDto] = Field(..., description="拆解结果（责任层）列表")
    nonResponsibilityList: Optional[List[NonResponsibilityDto]] = Field(None, description="责免信息列表（计划级别，一般不使用，责免信息放在责任层级）")

class DeconstructResultDto(BaseModel):
    """
    拆解结果根对象
    """
    id: int = Field(..., description="ID")
    orgCode: str = Field(..., description="机构代码")
    policyType: str = Field("1", description="保单类型：1-个险, 2-团险")
    groupPolicyNo: Optional[str] = Field(None, description="团单号码")
    planResultList: List[PlanResultDto] = Field(..., description="计划拆解结果列表")

class DsResponse(BaseModel):
    """
    ds_response 返回体根对象
    """
    transNo: str = Field(..., description="交易流水号")
    transDate: int = Field(..., description="请求返回系统时间，毫秒时间戳")
    systemCode: str = Field(..., description="调用系统编码")
    msgCode: str = Field(..., description="响应结果代码，00000-正常")
    msgInfo: Optional[str] = Field(None, description="响应结果描述")
    deconstructResult: Optional[DeconstructResultDto] = Field(None, description="拆解结果数据")
    planIds: Optional[List[int]] = Field(None, description="计划ID列表，原样回传请求中的值")