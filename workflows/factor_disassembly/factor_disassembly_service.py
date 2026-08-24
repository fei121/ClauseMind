"""
Date: 2025-12-11 17:17:43
LastEditTime: 2026-01-21 12:16:04
Description:
Factor Disassembly Service - Main business logic for /disassemble/factor endpoint
Orchestrates the entire factor disassembly workflow with clear separation of concerns
"""
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
import traceback
import os

import json

from config import settings
from utils import logger
from repositories.oss_repository import OSSRepository
from repositories.db_repository import DatabaseRepository
from workflows.policy_disassembly.pipeline import process_plans_parallel, process_plans_parallel_deconstruction
from workflows.document_processing.pipeline import document_understanding, document_understanding_deconstruction

# Import Pydantic models
from models.oldpydantic.request import DeconstructInput
from models.oldpydantic.response import (
    DeconstructAgentResp, DeconstructInfo, DeconstructResult, DeconstructOutput,
    FactorForRepost, LiabInfo, HealthNotice, NonResponsibilityItem
)
from models.pydantic.request import DsRequest
from models.pydantic.response import (
    DsResponse, DeconstructResultDto, PlanResultDto, LiabilityResultDto, PayScopeDto,
    ClaimNatureDto, MedicalTypeDto, HospitalScopeDto, HospitalParamDto,
    FeeCategoryDto, NonResponsibilityDto, RuleDto
)
from models.pydantic.code_tables import (
    MEDICAL_TYPE_KEYWORD_TO_CODE_MAP,
    FEE_CATEGORY_KEYWORD_TO_CODE_MAP,
    ALL_FEE_CATEGORIES
)


class FactorDisassemblyService:
    """
    Service for factor disassembly operations
    Handles the complete workflow from PDF processing to factor extraction
    """

    def __init__(self):
        """Initialize the factor disassembly service"""
        self.logger = logger
        self.oss_repo = OSSRepository()
        self.db_repo = DatabaseRepository()

    def _process_new_disassembly(self, request_data: DeconstructInput) -> DeconstructOutput:
        """
        处理新的拆解请求

        Args:
            request_data: 拆解请求输入模型

        Returns:
            DeconstructOutput Pydantic 模型对象
        """
        self.logger.info(f"正在拆解 deconstructId={request_data.deconstructId}, policyNo={request_data.policyNo}")

        markdown_catalog_with_idx = document_understanding(request_data)

        plan_results = process_plans_parallel(
            markdown_catalog_with_idx,
            request_data.planList,
            request_data.policyNo,
            request_data.orgCode
        )


        # 构建最终响应
        repost_resp = build_final_repost_json(
            plan_results,
            request_data.planList,
            request_data.orgCode,
            request_data.policyNo,
            request_data.deconstructId,
            request_data.deconstructType
        )

        # 生成赔付因子
        # todo：因子部分暂缓重构
        # if fee_scope_keywords:
        #     # Directly pass Pydantic model to generate_fee_scope
        #     repost_resp = generate_fee_scope(repost_resp, request_data.policyNo, fee_scope_keywords)
        #
        # 保存到数据库（如果需要）
        self._save_to_database_if_needed(request_data, plan_results, repost_resp)

        # def cleanup_temp_files(structured_data: Dict[str, str]):
        #     """
        #     清理临时文件
        #     """
        #     for key, path in structured_data.items():
        #         if key.endswith('_path') and os.path.exists(path):
        #             try:
        #                 os.remove(path)
        #             except:
        #                 pass
        #
        # cleanup_temp_files(markdown_catalog_with_idx)

        self.logger.info(f"Successfully completed disassembly for deconstructId={request_data.deconstructId}")

        # 使用 DeconstructOutput 模型包装返回结果
        output = DeconstructOutput(
            deconstructAgentReq=request_data,
            deconstructAgentResp=repost_resp,
            code=200,
            message='拆解成功'
        )

        return output

    def process_deconstruction(self, request_data: DsRequest) -> DsResponse:
        """
        处理新的拆解请求

        Args:
            request_data: 拆解请求输入模型

        Returns:
            DsResponse 响应对象
        """
        self.logger.info(f"正在拆解 PolicyNo={request_data.policyNo}, policyType属于{'团险' if request_data.productInfo.policyType == '2' else '个险'}")

        markdown_catalog_with_idx = document_understanding_deconstruction(request_data)

        plan_results, middle_results_dict = process_plans_parallel_deconstruction(
            markdown_catalog_with_idx,
            request_data.productInfo.planList,
            request_data.policyNo,
            request_data.productInfo.orgCode
        )

        # 构建最终响应
        deconstruct_result = build_final_repost_json_deconstruction(
            plan_results,
            request_data
        )

        self.logger.info(f"Successfully completed disassembly for policyNo={request_data.policyNo}")

        # 生成赔付因子
        # todo：因子部分暂缓重构
        # if fee_scope_keywords:
        #     # Directly pass Pydantic model to generate_fee_scope
        #     repost_resp = generate_fee_scope(repost_resp, request_data.policyNo, fee_scope_keywords)
        #



        # def cleanup_temp_files(structured_data: Dict[str, str]):
        #     """
        #     清理临时文件
        #     """
        #     for key, path in structured_data.items():
        #         if key.endswith('_path') and os.path.exists(path):
        #             try:
        #                 os.remove(path)
        #             except:
        #                 pass
        #
        # cleanup_temp_files(markdown_catalog_with_idx)

        # self.logger.info(f"Successfully completed disassembly for deconstructId={request_data.deconstructId}")

        # 使用 DeconstructOutput 模型包装返回结果
        # output = DeconstructOutput(
        #     deconstructAgentReq=request_data,
        #     deconstructAgentResp=repost_resp,
        #     code=200,
        #     message='拆解成功'
        # )

        output = DsResponse(
            transNo=request_data.transNo,
            transDate=int(datetime.now().timestamp() * 1000),  # 转换为毫秒时间戳
            msgCode="00000",
            msgInfo="拆解成功",
            systemCode=request_data.systemCode,
            deconstructResult=deconstruct_result,
            planIds=request_data.planIds
        )

        # 保存到数据库（如果需要）
        self._save_to_database_if_needed(request_data, middle_results_dict, output)
        return output

    def process_disassembly(self, request_data: DeconstructInput) -> DeconstructOutput:
        """
        因子拆解处理的主要入口点参数：
        request_data：请求数据字典或DeconstructInput
        模型返回：DeconstructOutput Pydantic模型对象
        """

        try:
            # 如果提供了 referResult，尝试复用已有的结果
            if self._should_reuse_result(request_data):
                reuse_result = self._try_reuse_existing_result(request_data)
                if reuse_result:
                    return reuse_result

            # 处理新的拆解操作 - 返回 DeconstructOutput 对象
            return self._process_new_disassembly(request_data)

        except Exception as e:
            self.logger.error(f"Factor disassembly failed for deconstructId={request_data.deconstructId}: {e}")
            traceback.print_exc()
            # 使用错误处理程序，现在它返回 DeconstructOutput 对象
            return self._create_error_response(request_data, str(e))

    # def process_deconstruction(self, request_data: DsRequest) -> DsResponse:
    #     """
    #     因子拆解处理的主要入口点参数：
    #     request_data：请求数据字典或DeconstructInput
    #     模型返回：DeconstructOutput Pydantic模型对象
    #     """
    #     try:
    #         return self._process_new_deconstruction(request_data)
    #
    #     except Exception as e:
    #         self.logger.error(f"Factor disassembly failed for deconstructId={request_data.systemCode}: {e}")
    #         traceback.print_exc()


    def _should_reuse_result(self, request_data: DeconstructInput) -> bool:
        """
        检查是否应该尝试复用现有结果

        Args:
            request_data: 拆解请求输入模型

        Returns:
            如果应该尝试复用返回 True，否则返回 False
        """
        return request_data.has_refer_result() and bool(request_data.get_first_plan_no())

    def _try_reuse_existing_result(self, request_data: DeconstructInput) -> Optional[DeconstructOutput]:
        """
        尝试复用现有的拆解结果

        Args:
            request_data: 拆解请求输入模型

        Returns:
            复用的 DeconstructOutput 对象（如果成功），否则返回 None
        """
        try:
            plan_no = request_data.get_first_plan_no()
            if not plan_no:
                self.logger.warning("未找到计划号，无法复用现有结果")
                return None

            # 使用Pydantic模型直接访问属性
            if request_data.referResult:
                # 只要 referResult 不为 None，deconstructInfo 一定存在（因为有 default_factory）
                new_health_notice_list = request_data.referResult.deconstructInfo.healthNoticeList
                new_non_responsibility_list = request_data.referResult.deconstructInfo.nonResponsibilityList
            else:
                # 处理 referResult 为空的情况
                new_health_notice_list = []
                new_non_responsibility_list = []

            self.logger.info(f"尝试复用结果: deconstructId={request_data.deconstructId}, plan_no={plan_no}")
            db_result = self.db_repo.find_result_by_plan_no(plan_no)

            if db_result:
                record_id = db_result['id']
                deconstruct_data_dict = db_result['deconstruct_result']

                # 将数据库中的字典转换为 DeconstructResult Pydantic 模型
                try:
                    deconstruct_result = DeconstructResult.model_validate(deconstruct_data_dict)

                    # 更新健康告知和责任免除列表
                    deconstruct_result.deconstructInfo.healthNoticeList = new_health_notice_list
                    deconstruct_result.deconstructInfo.nonResponsibilityList = new_non_responsibility_list

                    # 调用 _create_success_response 获取 DeconstructAgentResp 对象
                    response = self._create_success_response(request_data, [deconstruct_result])

                    # 使用 DeconstructOutput 模型包装最终返回结果
                    output = DeconstructOutput(
                        deconstructAgentReq=request_data,
                        deconstructAgentResp=response,
                        code=200,
                        message='拆解成功'
                    )

                    self.logger.info(f"成功复用现有结果: deconstructId={request_data.deconstructId}, record_id={record_id}")
                    return output

                except Exception as validation_error:
                    self.logger.error(f"数据库结果验证失败: {validation_error}，将继续执行新的拆解")
                    return None

        except Exception as e:
            self.logger.warning(f"复用现有结果失败: {e}，将继续执行新的拆解")

        return None

    def _convert_to_serializable(self, obj: Any) -> Any:
        """
        递归将包含Pydantic模型的对象转换为可JSON序列化的格式

        Args:
            obj: 待转换的对象，可能包含Pydantic模型、字典、列表等

        Returns:
            可JSON序列化的对象
        """
        from pydantic import BaseModel

        if isinstance(obj, BaseModel):
            return obj.model_dump()
        elif isinstance(obj, dict):
            return {k: self._convert_to_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._convert_to_serializable(item) for item in obj]
        else:
            return obj

    def _save_to_database_if_needed(self, request_data, plan_results: Dict[str, Any],
                                   repost_json) -> None:
        """
        如果处于测试环境，将中间结果保存到数据库

        Args:
            request_data: 拆解请求输入模型
            # db_disassemble_info: 中间拆解信息（DBDisassembleItem对象列表）
            repost_json: 响应JSON字典
        """
        try:
            if settings.should_save_to_database:
                # request_dict = request_data.model_dump()
                # db_info_list = [item.model_dump() for item in db_disassemble_info]

                import json
                # 将plan_results中的Pydantic模型转换为可序列化的格式
                serializable_plan_results = self._convert_to_serializable(plan_results)

                self.db_repo.db_insert_disassemble_result(
                    json.dumps(request_data.model_dump(), ensure_ascii=False),
                    json.dumps(serializable_plan_results, ensure_ascii=False),  # prompts_json
                    json.dumps(repost_json.model_dump(), ensure_ascii=False)
                )

                self.logger.info("中间结果已保存到数据库")
        except Exception as e:
            self.logger.error(f"保存中间结果失败: {e}")

    def _create_success_response(self, request_data: DeconstructInput,
                                result_list: List[DeconstructResult]) -> DeconstructAgentResp:
        """
        使用 Pydantic 模型创建成功响应

        Args:
            request_data: Request data 作为 DeconstructInput 模型
            result_list: Result list 作为 DeconstructResult 模型

        Returns:
            DeconstructAgentResp Pydantic 模型
        """
        return DeconstructAgentResp(
            msgCode="00000",
            msgInfo="团单理算因子拆解成功",
            orgCode=request_data.orgCode,
            policyNo=request_data.policyNo,
            deconstructId=request_data.deconstructId,
            deconstructType=request_data.deconstructType,
            deconstructResultList=result_list
        )

    def _create_error_response(self, request_data: DeconstructInput,
                              error_message: str) -> DeconstructOutput:
        """
        创建错误响应

        Args:
            request_data: 拆解请求输入模型
            error_message: 错误消息

        Returns:
            DeconstructOutput Pydantic model object
        """
        # 使用 DeconstructOutput 模型包装错误响应
        return DeconstructOutput(
            deconstructAgentReq=request_data,
            deconstructAgentResp=DeconstructAgentResp(
                msgCode="99999",
                msgInfo=f"拆解失败，失败原因：{error_message}"
            ),
            code=500,
            message=f'拆解失败，失败原因：{error_message}'
        )

def build_final_repost_json(
    plan_results: Dict[str, Any],
    plan_list: List,
    orgCode: str,
    policyNo: str,
    deconstructId: Union[int, str],
    deconstructType: Union[int, str]
) -> DeconstructAgentResp:
    """生成最终回传 JSON - 直接从 plan_results 和 plan_list 构建"""
    repost_resp = DeconstructAgentResp(
        msgCode="00000",
        msgInfo="团单理算因子拆解成功",
        orgCode=orgCode,
        policyNo=policyNo,
        deconstructId=deconstructId,
        deconstructType=deconstructType,
        deconstructResultList=[]
    )

    # 处理每个计划
    for plan in plan_list or []:
        factorList: List[FactorForRepost] = []
        liabInfoList: List[LiabInfo] = []

        # 使用 Pydantic 模型直接访问属性
        plan_name = plan.planName
        plan_no = plan.planNo

        # 初始化计划级别的健康告知列表
        health_notice_list = []

        # 处理每个条款
        for clause in plan.clauseList or []:
            clause_name = clause.clauseName
            clause_code = clause.clauseCode

            # 处理每个责任
            for liability in clause.liabilityList or []:
                liab_name = liability.liabName
                liab_code = liability.liabCode

                # 构建查找键
                search_key = f"{plan_name}_{clause_name}_{liab_name}"

                # 从 plan_results 获取结果
                pr = plan_results.get(search_key, {}) if isinstance(plan_results, dict) else {}

                # 处理理算因子列表
                for factor in pr.get('factor', []) or []:
                    factor_item = FactorForRepost(
                        factorName=factor.get("理算因子名称", ""),
                        factorType=factor.get("因子类型", ""),
                        factorValue=factor.get("理算因子值", ""),
                        accumulateType=factor.get("因子累积方式", ""),
                        sourceMessage="",
                        relatedLiabList=[{
                            "clauseCode": clause_code,
                            "liabCode": liab_code,
                            "relatedScenes": ",".join(factor.get("理算因子限定场景", []))
                        }]
                    )
                    factorList.append(factor_item)

                # 责任免除列表（每个责任可以有自己的nonResponsibilityList）
                non_responsibility_list = []
                for item in pr.get('nonResponsibilityList', []) or []:
                    non_responsibility_list.append(NonResponsibilityItem(
                        nonResponsibilityName=item.get('nonResponsibilityName', ''),
                        nonResponsibilityInfo=item.get('nonResponsibilityInfo', '')
                    ))

                # 责任信息
                liab_info_item = LiabInfo(
                    clauseCode=clause_code,
                    liabCode=liab_code,
                    liabName=liab_name,
                    structure_tree_leaf=search_key,
                    sceneName="",
                    payScope=pr.get('scope', ''),
                    payParam=pr.get('pay_param', ''),
                    claimNatures="",
                    medicalTypes="",
                    hospitalScope="",
                    feeScope=None,
                    nonResponsibilityList=non_responsibility_list,
                    tagNames=""
                )

                # 添加 trace/observation IDs 如果可用
                trace_id = pr.get('traceId')
                observation_id = pr.get('observationId')
                if trace_id:
                    liab_info_item.traceId = trace_id
                if observation_id:
                    liab_info_item.observationId = observation_id

                liabInfoList.append(liab_info_item)

                # 从第一个责任结果中提取健康告知列表（仅提取一次）
                if not health_notice_list and pr.get('healthNoticeList'):
                    health_notice_list = pr.get('healthNoticeList', [])

        deconstruct_info = DeconstructInfo(
            healthNoticeList=health_notice_list,
            factorList=factorList,
            liabInfoList=liabInfoList,
            tagNames="",
            feeScope=""
        )

        deconstruct_result = DeconstructResult(
            planNo=plan_no,
            deconstructInfo=deconstruct_info
        )

        repost_resp.deconstructResultList.append(deconstruct_result)

    return repost_resp

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
def build_final_repost_json_deconstruction(
    plan_results: Dict[str, Any],
    request_data: DsRequest
) -> DeconstructResultDto:
    """
    生成最终回传 JSON - 新格式 DeconstructResultDto

    Args:
        plan_results: process_plans_parallel_deconstruction 返回的结果字典
        request_data: 原始请求对象 DsRequest

    Returns:
        DeconstructResultDto 对象
    """
    try:
        plan_result_list: List[PlanResultDto] = []

        # 检查必要的数据是否存在
        if not request_data.productInfo or not request_data.productInfo.planList:
            logger.error("build_final_repost_json_deconstruction: Missing productInfo or planList")
            # 返回空但有效的结果
            return DeconstructResultDto(
                id=request_data.productInfo.id if request_data.productInfo else 1,
                orgCode=request_data.productInfo.orgCode if request_data.productInfo else "",
                policyType=request_data.productInfo.policyType if request_data.productInfo else "1",
                groupPolicyNo=request_data.productInfo.groupPolicyNo if request_data.productInfo else None,
                planResultList=[]
            )

        for plan in request_data.productInfo.planList:
            liability_result_list: List[LiabilityResultDto] = []

            # 处理每个责任
            for liability in plan.liabilityList:
                # 构建查找键
                search_key = f"{plan.planName}_{plan.clauseName}_{liability.liabName}"

                # 从 plan_results 获取结果
                pr = plan_results.get(search_key, {}) if isinstance(plan_results, dict) else {}

                # 获取 pay_scope 数据 - format_kb_results_to_legacy_format_deconstruction 直接返回 List[PayScopeDto]
                # 注意：pay_scope 中的置信度已由转换函数根据字段级置信度评估结果设置
                pay_scope_list = pr.get('pay_scope', [])

                # 获取 pay_param 数据 - format_kb_results_to_legacy_format_deconstruction 直接返回 List[RuleDto]
                # 注意：pay_param 中的置信度已由转换函数根据字段级置信度评估结果设置
                pay_param_rules = pr.get('pay_param', [])

                # 获取责任层级的责免信息 - format_kb_results_to_legacy_format_deconstruction 直接返回 List[NonResponsibilityDto]
                non_resp_list = pr.get('nonResponsibilityList', []) or []

                # 将 pay_param 规则添加到每个 PayScopeDto 的 payParam 中
                if pay_param_rules and isinstance(pay_param_rules, list):
                    for pay_scope in pay_scope_list:
                        if hasattr(pay_scope, 'payParam'):
                            pay_scope.payParam = pay_param_rules

                # 计算动态 ID：从 1 开始，如果已有规则则累加
                existing_rules_count = 0
                for pay_scope in pay_scope_list:
                    if hasattr(pay_scope, 'sceneRules') and pay_scope.sceneRules:
                        existing_rules_count = max(existing_rules_count, len(pay_scope.sceneRules))

                # 规则1：如果责任名包含"生育"，添加 sceneRules 规则
                if "生育" in liability.liabName:
                    existing_rules_count += 1
                    birth_rule = RuleDto(
                        id=str(existing_rules_count),
                        ruleType="E1_002",
                        ruleParams='{"P1":"1","P2":"女性生育"}',
                        confidence="1",
                        position=None
                    )
                    for pay_scope in pay_scope_list:
                        if hasattr(pay_scope, 'sceneRules'):
                            # 如果已有 sceneRules，追加；否则创建新列表
                            if pay_scope.sceneRules is None:
                                pay_scope.sceneRules = []
                            pay_scope.sceneRules.append(birth_rule)

                # 规则2：如果责任名包含"自费"，添加 sceneRules 规则
                if "自费" in liability.liabName:
                    existing_rules_count += 1
                    self_pay_rule = RuleDto(
                        id=str(existing_rules_count),
                        ruleType="E1_999",
                        ruleParams="自费",
                        confidence="1",
                        position=None
                    )
                    for pay_scope in pay_scope_list:
                        if hasattr(pay_scope, 'sceneRules'):
                            # 如果已有 sceneRules，追加；否则创建新列表
                            if pay_scope.sceneRules is None:
                                pay_scope.sceneRules = []
                            pay_scope.sceneRules.append(self_pay_rule)

                # 创建 LiabilityResultDto - 责免信息放在责任层级
                liability_result = LiabilityResultDto(
                    id=liability.id,
                    liabCode=liability.liabCode,
                    liabName=liability.liabName,
                    payScopeList=pay_scope_list,
                    nonResponsibilityList=non_resp_list,
                    sessionId=pr.get('session_id')
                )
                liability_result_list.append(liability_result)

            # 创建 PlanResultDto
            plan_result = PlanResultDto(
                id=plan.id,
                planCode=plan.planCode,
                planName=plan.planName,
                planVersion=plan.planVersion,
                clauseCode=plan.clauseCode,
                clauseName=plan.clauseName,
                liabilityResultList=liability_result_list,
                nonResponsibilityList=None
            )
            plan_result_list.append(plan_result)

        # 创建 DeconstructResultDto
        deconstruct_result = DeconstructResultDto(
            id=request_data.productInfo.id,
            orgCode=request_data.productInfo.orgCode,
            policyType=request_data.productInfo.policyType,
            groupPolicyNo=request_data.productInfo.groupPolicyNo,
            planResultList=plan_result_list
        )

        return deconstruct_result

    except Exception as e:
        logger.error(f"Error in build_final_repost_json_deconstruction: {e}")
        traceback.print_exc()
        # 返回空但有效的结果，避免返回 None
        return DeconstructResultDto(
            id=request_data.productInfo.id if request_data.productInfo else 1,
            orgCode=request_data.productInfo.orgCode if request_data.productInfo else "",
            policyType=request_data.productInfo.policyType if request_data.productInfo else "1",
            groupPolicyNo=request_data.productInfo.groupPolicyNo if request_data.productInfo else None,
            planResultList=[]
        )

