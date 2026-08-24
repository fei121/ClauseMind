import os
import tempfile
from datetime import datetime
from urllib.parse import urlparse, unquote

from config import settings
from infrastructure.http_session import get_session, get_oss_session
from models.oldpydantic.request import DeconstructInput
from models.oldpydantic.response import DeconstructOutput, DeconstructAgentResp
from models.pydantic.request import DsRequest
from models.pydantic.response import DsResponse
from repositories.oss_repository import oss_upload_pdf_and_get_url
from utils import logger
from workflows.factor_disassembly.factor_disassembly_service import FactorDisassemblyService


# 回传条款拆解的结果给app落库并更新拆解状态
def factor_deconstruct_callback(result: DsResponse, policyNo):
    """Send callback to App service system with disassembly result"""
    try:
        # Convert Pydantic model to dict before sending, with json mode for datetime serialization
        result_dict = result.model_dump(mode="json")
        session = get_session()
        response = session.post(settings.APP_DECONSTRUCTION_CALLBACK_URL, json=result_dict)
        logger.info(
            f"条款拆解: policyNo-{policyNo} {response.text} -回传app返回报文为：{result_dict}")
    except Exception as e:
        logger.error(f"条款拆解: policyNo-{policyNo} 回传失败：{e}")


# 回传条款拆解的结果给app落库并更新拆解状态（旧接口）
def factor_disassemble_callback(result: DeconstructOutput, deconstruct_id):
    """Send callback to App service system with disassembly result"""
    try:
        # Convert Pydantic model to dict before sending
        result_dict = result.model_dump()
        session = get_session()
        response = session.post(settings.APP_CALLBACK_URL, json=result_dict)
        logger.info(
            f"条款拆解: deconstructId-{deconstruct_id} -{response.text} -回传app返回报文为：{result_dict}")
    except Exception as e:
        logger.error(f"条款拆解: deconstructId-{deconstruct_id} 回传失败：{e}")


# 提交因子拆解的后台任务（旧接口）
def process_factor_disassembly_background(request_data: DeconstructInput):
    """Background task to process factor disassembly"""
    try:
        # 下载fileUrl文件并上传到OSS获取新的外部链接
        if request_data.fileList:
            with tempfile.TemporaryDirectory() as temp_dir:
                for file_info in request_data.fileList:
                    if file_info.fileUrl:
                        try:
                            # 下载PDF文件
                            session = get_oss_session()
                            response = session.get(file_info.fileUrl, timeout=60)
                            response.raise_for_status()

                            # 检查 fileName 是否为 None，如果是则从 URL 中提取文件名
                            filename = file_info.fileName
                            if not filename:
                                # 从 URL 中提取文件名
                                parsed_url = urlparse(file_info.fileUrl)
                                filename = unquote(os.path.basename(parsed_url.path))
                                # 如果仍然为空，使用默认文件名
                                if not filename:
                                    filename = f"document_{request_data.deconstructId}.pdf"
                                logger.info(
                                    f"条款拆解: deconstructId-{request_data.deconstructId} fileName为空，从URL提取文件名: {filename}")

                            temp_pdf_path = os.path.join(temp_dir, filename)
                            with open(temp_pdf_path, 'wb') as f:
                                f.write(response.content)

                            logger.info(
                                f"条款拆解: deconstructId-{request_data.deconstructId} PDF下载成功: {file_info.fileUrl} -> {temp_pdf_path}")

                            # 上传到OSS并获取新的外部链接
                            new_external_url = oss_upload_pdf_and_get_url(temp_pdf_path)
                            file_info.fileExternalUrl = new_external_url
                            logger.info(
                                f"条款拆解: deconstructId-{request_data.deconstructId} 新外部链接生成成功: {new_external_url}")
                        except Exception as e:
                            logger.error(
                                f"条款拆解: deconstructId-{request_data.deconstructId} 文件处理失败 {file_info.fileUrl}: {e}")
                            raise

        # Pass the Pydantic model directly to the service layer
        service = FactorDisassemblyService()
        result = service.process_disassembly(request_data)
        # Pass DeconstructOutput object to callback (will be dumped inside)
        factor_disassemble_callback(result, request_data.deconstructId)
    except Exception as e:
        logger.error(
            f"Background factor disassembly failed for deconstructId={request_data.deconstructId}: {e}")
        # Create error response as DeconstructOutput object
        error_result = DeconstructOutput(
            deconstructAgentReq=request_data,
            deconstructAgentResp=DeconstructAgentResp(
                msgCode="99999",
                msgInfo=f"拆解失败，失败原因：{str(e)}"
            ),
            code=500,
            message=f'拆解失败，失败原因：{str(e)}'
        )
        factor_disassemble_callback(error_result, request_data.deconstructId)


# 提交因子拆解的后台任务（新接口）
def process_factor_deconstruction_background(request_data: DsRequest):
    """Background task to process factor deconstruction"""
    try:
        # 根据policyType设置policyNo字段
        if hasattr(request_data, 'productInfo'):
            policy_type = request_data.productInfo.policyType
            if policy_type == "2":
                # 如果是团险(policyType=2)，提取groupPolicyNo复制到policyNo
                if request_data.productInfo.groupPolicyNo:
                    request_data.policyNo = request_data.productInfo.groupPolicyNo
            elif policy_type == "1":
                # 如果是个险(policyType=1)，使用planList中的所有planCode合并作为policyNo
                if request_data.productInfo.planList and len(request_data.productInfo.planList) > 0:
                    plan_codes = list(set([plan.planCode for plan in request_data.productInfo.planList]))
                    request_data.policyNo = "individual_" + "_".join(plan_codes)

        # 下载fileUrl文件并上传到OSS获取新的外部链接
        if request_data.productInfo and request_data.productInfo.fileList:
            with tempfile.TemporaryDirectory() as temp_dir:
                for file_info in request_data.productInfo.fileList:
                    if file_info.fileUrl:
                        try:
                            # 下载PDF文件
                            session = get_oss_session()
                            response = session.get(file_info.fileUrl, timeout=60)
                            response.raise_for_status()

                            filename = file_info.fileName
                            temp_pdf_path = os.path.join(temp_dir, filename)
                            with open(temp_pdf_path, 'wb') as f:
                                f.write(response.content)

                            logger.info(
                                f"条款拆解: policyNo-{request_data.policyNo} PDF下载成功: {file_info.fileUrl} -> {temp_pdf_path}")

                            # 上传到OSS并获取新的外部链接
                            new_external_url = oss_upload_pdf_and_get_url(temp_pdf_path)
                            file_info.fileExternalUrl = new_external_url
                            logger.info(
                                f"条款拆解: policyNo-{request_data.policyNo} 新外部链接生成成功: {new_external_url}")
                        except Exception as e:
                            logger.error(
                                f"条款拆解: policyNo-{request_data.policyNo} 文件处理失败 {file_info.fileUrl}: {e}")
                            raise

        # Pass the Pydantic model directly to the service layer
        service = FactorDisassemblyService()
        result = service.process_deconstruction(request_data)
        # todo：提交前启用回调接口
        factor_deconstruct_callback(result, request_data.policyNo)
    except Exception as e:
        logger.error(
            f"Background factor disassembly failed for policyNo={request_data.policyNo}: {e}")
        # Create error response as DeconstructOutput object
        error_result = DsResponse(
            transNo=request_data.transNo,
            transDate=int(datetime.now().timestamp() * 1000),  # 转换为毫秒时间戳
            systemCode=request_data.systemCode,
            msgCode="99999",
            msgInfo=f"Error: 拆解失败，失败原因：{str(e)}",
            deconstructResult=None,
            planIds=request_data.planIds
        )
        # todo：提交前启用回调接口
        factor_deconstruct_callback(error_result, request_data.policyNo)
