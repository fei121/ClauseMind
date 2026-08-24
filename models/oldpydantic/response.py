from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime


class BaseResponse(BaseModel):
    """基础响应模型"""
    code: str = Field(default="200", description="响应代码")
    message: str = Field(default="Success", description="响应消息")


class ErrorResponse(BaseResponse):
    """错误响应模型"""
    code: str = Field(default="500", description="错误代码")
    message: str = Field(..., description="错误消息")
    details: Optional[Dict[str, Any]] = Field(default=None, description="错误详情")  # 待确认


class HealthNotice(BaseModel):  # 待确认
    """健康告知模型"""
    healthNoticeName: str = Field(..., description="健康告知名称")
    healthNoticeInfo: str = Field(..., description="健康告知详细内容")


class NonResponsibilityItem(BaseModel):
    """
    单个非责事项模型
    """
    nonResponsibilityName: str = Field(
        ...,
        description="非责任标签名称，例如：诊疗非责标签、药品非责标签"
    )
    nonResponsibilityInfo: str = Field(
        ...,
        description="具体的非责内容详情，通常为使用逗号分隔的字符串，例如：轮椅,眼镜,义肢,义眼,助听器"
    )

# TODO: 确认 FactorForRepost 内容与 FactorInfo 的差异
class FactorForRepost(BaseModel):
    """用于回传的因子模型"""
    factorName: Optional[str] = Field(default=None, description="理算因子名称")
    factorType: Optional[str] = Field(default=None, description="因子类型")
    factorValue: Optional[Union[str, int, float]] = Field(default=None, description="理算因子值")
    accumulateType: Optional[str] = Field(default=None, description="因子累积方式")
    sourceMessage: Optional[str] = Field(default="", description="来源信息")
    relatedLiabList: List[Dict[str, Any]] = Field(default_factory=list, description="相关责任列表")

    model_config = ConfigDict(extra='allow')

class FactorInfo(BaseModel):
    """因子信息模型"""
    factorType: str = Field(..., description="因子类型，示例: 赔付比例")
    factorValue: Any = Field(..., description="因子值，示例: 0.85")
    typeofTreatment: Optional[str] = Field(default=None, description="就诊类型，示例: 专家门诊、急诊、普通门诊、特病门诊")
    compensationCosts: Optional[str] = Field(default=None, description="赔付费用范围，示例: 所有")
    feeRange: Optional[str] = Field(default=None, description="费用范围，示例: 医保范围内")
    extraDescription: Optional[List[str]] = Field(default=None, description="额外描述信息，示例: ['门诊保额1万元，免赔500元', '需先经医保结算，对剩余医保范围内费用按85%赔付', '未持医保卡就诊的，须提供医保结算凭证后按同样规则赔付']")
    traceId: Optional[str] = Field(default=None, description="LangFuse追踪ID，示例: cd2b206eefe6417f3dbc6f63a370c9fe")
    observationId: Optional[str] = Field(default=None, description="LangFuse观察ID，示例: 1082f947c31b80cb")

class LiabInfo(BaseModel):
    """责任信息模型"""
    clauseCode: Optional[str] = Field(default=None, description="条款编号")
    clauseName: Optional[str] = Field(default=None, description="条款名称")
    liabCode: Optional[str] = Field(default=None, description="责任编号")
    liabName: Optional[str] = Field(default=None, description="责任名称")
    structure_tree_leaf: Optional[str] = Field(default=None, description="结构树叶子节点")
    sceneName: Optional[str] = Field(default=None, description="场景名称")
    payScope: Optional[str] = Field(default=None, description="赔付范围")
    payParam: Optional[str] = Field(default=None, description="赔付参数")
    claimNatures: Optional[str] = Field(default=None, description="理赔性质")
    medicalTypes: Optional[str] = Field(default=None, description="医疗类型")
    hospitalScope: Optional[str] = Field(default=None, description="医院范围")
    # feeScope 是 feeScopeItem 组成的列表
    feeScope: Optional[List[FactorInfo]] = Field(default=None, description="费用范围列表")
    nonResponsibilityList: List[NonResponsibilityItem] = Field(default_factory=list, description="责任免除列表")
    tagNames: Optional[str] = Field(default=None, description="标签名称")
    traceId: Optional[str] = Field(default=None, description="Trace ID")
    observationId: Optional[str] = Field(default=None, description="Observation ID")

    model_config = ConfigDict(extra='allow')


class DeconstructInfo(BaseModel):
    """拆解信息模型"""
    healthNoticeList: List[HealthNotice] = Field(default_factory=list, description="健康告知列表")
    factorList: List[FactorForRepost] = Field(default_factory=list, description="因子列表")
    liabInfoList: List[LiabInfo] = Field(default_factory=list, description="责任信息列表")
    tagNames: Optional[str] = Field(default=None, description="标签名称")
    feeScope: Optional[str] = Field(default=None, description="费用范围")
    factorRecallDict : Optional[Dict[str, Any]] = Field(default=None, description="因子召回字典")

    model_config = ConfigDict(extra='allow')

class DeconstructResult(BaseModel):
    """拆解结果模型"""
    planNo: Optional[str] = Field(default=None, description="计划编号")
    planName: Optional[str] = Field(default=None, description="计划名称")
    deconstructInfo: DeconstructInfo = Field(default_factory=DeconstructInfo, description="拆解信息")
    # factorRecallDict : Optional[Dict[str, Any]] = Field(default=None, description="因子召回字典")

    model_config = ConfigDict(extra='allow')

class DeconstructAgentResp(BaseModel):
    """拆解响应模型"""
    msgCode: str = Field(default="00000", description="消息代码")
    msgInfo: str = Field(default="团单理算因子拆解成功", description="消息信息")
    orgCode: Optional[str] = Field(default=None, description="机构代码")
    policyNo: Optional[str] = Field(default=None, description="保单号")
    deconstructId: Optional[Union[int, str]] = Field(default=None, description="拆解ID")
    deconstructType: Optional[Union[int, str]] = Field(default=None, description="拆解类型")
    deconstructResultList: List[DeconstructResult] = Field(default_factory=list, description="拆解结果列表")

    model_config = ConfigDict(extra='allow')

class DeconstructOutput(BaseModel):
    """拆解端点输出模型"""
    deconstructAgentReq: "DeconstructInput" = Field(default=None, description="原始请求")
    deconstructAgentResp: DeconstructAgentResp = Field(default_factory=DeconstructAgentResp, description="拆解响应")
    code: int = Field(default=200, description="响应代码")
    message: str = Field(default="拆解成功", description="响应消息")

    model_config = ConfigDict(extra='allow')

# ==================== # 中间处理结构 ====================
class LiabilityTemp(BaseModel):
    """临时责任模型（中间处理使用）"""
    责任名称: str = Field(description="责任名称")
    责任编号: Optional[str] = Field(default=None, description="责任编号")
    赔付说明: str = Field(default="", description="赔付说明")
    赔付参数: Optional[str] = Field(default=None, description="赔付参数")
    责任保额: str = Field(default="", description="责任保额")
    理算因子列表: List[Dict[str, Any]] = Field(default_factory=list, description="理算因子列表")
    traceId: Optional[str] = Field(default=None, description="Trace ID")
    observationId: Optional[str] = Field(default=None, description="Observation ID")

    model_config = ConfigDict(extra='allow')


class ClauseTemp(BaseModel):
    """临时条款模型（中间处理使用）"""
    条款_险种: str = Field(description="条款/险种")
    条款编号: Optional[str] = Field(default=None, description="条款编号")
    责任列表: List[LiabilityTemp] = Field(default_factory=list, description="责任列表")

    model_config = ConfigDict(extra='allow')


class PlanTemp(BaseModel):
    """临时计划模型（中间处理使用）"""
    计划名称: str = Field(description="计划名称")
    计划编号: Optional[str] = Field(default=None, description="计划编号")
    计划内容: List[ClauseTemp] = Field(default_factory=list, description="计划内容")

    model_config = ConfigDict(extra='allow')


# 数据库落库结构
class DBDisassembleItem(BaseModel):
    """数据库存储项模型"""
    计划名称: Optional[str] = Field(default=None, description="计划名称")
    计划编号: Optional[str] = Field(default=None, description="计划编号")
    条款_险种: Optional[str] = Field(default=None, description="条款/险种")
    条款编号: Optional[str] = Field(default=None, description="条款编号")
    责任名称: Optional[str] = Field(default=None, description="责任名称")
    责任编号: Optional[str] = Field(default=None, description="责任编号")
    赔付说明: Optional[str] = Field(default=None, description="赔付说明")
    赔付参数: Optional[str] = Field(default=None, description="赔付参数")
    赔付prompt: Optional[str] = Field(default=None, description="赔付prompt")
    traceId: Optional[str] = Field(default=None, description="Trace ID")
    observationId: Optional[str] = Field(default=None, description="Observation ID")

    model_config = ConfigDict(extra='allow')