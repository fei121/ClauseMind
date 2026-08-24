"""
Pydantic models for disassemble entrance API schemas.
所有数据模型都定义在此文件中。
"""

from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict, Json


# ==================== 基础模型（来自原 common_models.py） ====================

class FileInfo(BaseModel):
    """这部分是asp拆解时上传的文档"""
    fileType: str = Field(..., description="文档类型，如“application/pdf”等")
    fileUrl: str = Field(..., description="内部OSS存储文件链接，永不过期")
    fileExternalUrl: Optional[str] = Field(None, description="外部可访问OSS文件链接，可用于调用OCR服务，过期时长1H")
    fileClass: Optional[List[str]] = Field(None, description="文档类别列表，枚举值：“特别约定、协议、条款”")
    fileName: Optional[str] = Field(None, description="原始文件名")


class TextInfo(BaseModel):
    """这部分是asp拆解时单独输入文本框的内容"""
    textInfo: str = Field(..., description="文本内容")
    textType: str = Field(..., description="文本类型")
    textCode: Optional[str] = Field(default=None, description="文本类型代码")


# class OSSObjets(BaseModel):
#     """OSS 对象引用模型"""
#     policyId: str = Field(..., description="保单ID")
#     mdName: str = Field(..., description="Markdown 文件名")
#     objectPath: str = Field(..., description="OSS 对象路径")


# class PageInfo(BaseModel):
#     """分页信息模型"""
#     page: int = Field(default=1, ge=1, description="当前页码")
#     perPage: int = Field(default=10, ge=1, le=100, description="每页数量")
#     total: Optional[int] = Field(default=None, description="总数量")
#     hasMore: Optional[bool] = Field(default=None, description="是否还有更多")


# ==================== 拆解业务模型 ====================

class Liability(BaseModel):
    """保险责任模型"""
    liabName: Optional[str] = Field(default=None, description="责任名称")
    liabCode: Optional[str] = Field(default=None, description="责任编号")
    liabilityName: Optional[str] = Field(default=None, description="备用责任名称字段")

    model_config = ConfigDict(extra='allow')


class Clause(BaseModel):
    """保险条款模型"""
    clauseName: str = Field(description="条款/险种名称")
    clauseCode: Optional[str] = Field(default=None, description="条款编号")
    liabilityList: List[Liability] = Field(default_factory=list, description="责任列表")

    model_config = ConfigDict(extra='allow')


class Plan(BaseModel):
    """保险计划模型"""
    planNo: Optional[str] = Field(default=None, description="计划编号")
    planName: str = Field(description="计划名称")
    planId: Optional[str] = Field(default=None, description="备用计划ID")
    clauseList: List[Clause] = Field(default_factory=list, description="条款列表")

    model_config = ConfigDict(extra='allow')


class DeconstructInput(BaseModel):
    """拆解请求输入模型"""
    deconstructId: Optional[int] = Field(default=999, description="拆解ID")
    deconstructType: Optional[int] = Field(default=1, description="拆解类型")
    #fixme 99999只是个险占位，后续需要评估个险入参改造，加入个险特定标识等需求
    orgCode: Optional[str] = Field(default="99999", description="机构代码")
    policyNo: Optional[str] = Field(default="99999", description="保单号")
    planList: List[Plan] = Field(default_factory=list, description="计划列表")
    textList: List[TextInfo] = Field(default_factory=list, description="文本列表")
    fileList: List[FileInfo] = Field(default_factory=list, description="文件列表")
    # referResult是JSON字符串，Pydantic会自动转换为DeconstructResult对象
    # referResult: Optional[DeconstructResult] = Field(default=None, description="引用的拆解结果，用于复用拆解")
    referResult: Optional[Json["DeconstructResult"]] = Field(default=None, description="引用的拆解结果，用于复用拆解")
    # deconstructPdfStrings: Optional[List[str]] = Field(default=None, description="普通ocr文本内容列表（废弃，虽传入但不使用的冗余，需后端缺省）")

    model_config = ConfigDict(extra='allow', json_schema_extra={"coerce_numbers_to_str": False})

    def get_first_plan_no(self) -> Optional[str]:
        """获取第一个计划的计划号"""
        return self.planList[0].planNo if self.planList else None

    def has_refer_result(self) -> bool:
        """检查是否有引用结果"""
        return self.referResult is not None

# class Factor(BaseModel):
#     """理算因子模型"""
#     factorName: Optional[str] = Field(default=None, description="理算因子名称")
#     factorType: Optional[str] = Field(default=None, description="因子类型")
#     factorValue: Optional[Union[str, int, float]] = Field(default=None, description="理算因子值")
#     accumulateType: Optional[str] = Field(default=None, description="因子累积方式")
#     sourceMessage: Optional[str] = Field(default="", description="来源信息")
#     factorScene: Optional[str] = Field(default=None, description="备用因子场景")
#     relatedLiabList: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="相关责任列表")
#
#     model_config = ConfigDict(extra='allow')

# class FactorForRepost(BaseModel):
#     """用于回传的因子模型"""
#     factorName: Optional[str] = Field(default=None, description="理算因子名称")
#     factorType: Optional[str] = Field(default=None, description="因子类型")
#     factorValue: Optional[Union[str, int, float]] = Field(default=None, description="理算因子值")
#     accumulateType: Optional[str] = Field(default=None, description="因子累积方式")
#     sourceMessage: Optional[str] = Field(default="", description="来源信息")
#     relatedLiabList: List[Dict[str, Any]] = Field(default_factory=list, description="相关责任列表")
#
#     model_config = ConfigDict(extra='allow')

# class feeScopeItem(BaseModel):
#     """用于回传的因子模型"""
#     factorType: Optional[str] = Field(default=None, description="因子类型")
#     factorValue: Optional[Union[str, int, float]] = Field(default=None, description="理算因子值")
#     typeofTreatment: Optional[str] = Field(default=None, description="就诊类型")
#     compensationCosts: Optional[str] = Field(default=None, description="赔付费用范围")
#     feeRange: Optional[str] = Field(default=None, description="费用范围")
#     extraDescription: Optional[List[str]] = Field(default=None, description="额外描述信息")
#     traceId: Optional[str] = Field(default=None, description="Trace ID")
#     observationId: Optional[str] = Field(default=None, description="Observation ID")
#
#     model_config = ConfigDict(extra='allow')



