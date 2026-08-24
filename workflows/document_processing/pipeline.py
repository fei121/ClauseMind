import hashlib
import json
import os
import shutil
from datetime import datetime, timezone, timedelta

from config import OSS_BASE_PREFIX
from models.oldpydantic.request import DeconstructInput
from models.pydantic.request import DsRequest
from repositories.oss_repository import oss_upload_dir, oss_get_object_metadata, oss_download_dir, oss_prefix_exists, \
    oss_upload_parsed_md_file
from utils import logger
from vectorstore.policy_manager import LOCAL_CACHE_DIR
# Import from nodes
from .nodes.markdown_enhance import markdown_parser
from .nodes.process_pdf_file_list import process_pdf_file_list, process_pdf_file_list_deconstruction
# 向量库构建相关
from .nodes.vectorstore_builder import build_vectorstore_from_chunks


def document_understanding(request_data: DeconstructInput, build_vectorstore: bool = True):
    """
    文档理解主流程（旧接口）

    Args:
        request_data: 拆解请求输入
        build_vectorstore: 是否同时构建向量库 (避免后续重复处理)

    Returns:
        markdown_catalog_with_idx: 解析结果 (包含 chunks)
    """
    file_signatures = []
    file_identity_list = []
    files_without_external_url = []
    for file_info in request_data.fileList:
        if file_info.fileExternalUrl:
            try:
                etag = oss_get_object_metadata(file_info.fileExternalUrl).get('etag', '')
                if etag:
                    file_signatures.append(etag)
                    file_identity = f"{file_info.fileName or ''}:{etag}"
                    file_identity_list.append(file_identity)
                    logger.info(f"获取文件签名: {file_info.fileName} -> {etag}")
                else:
                    logger.warning(f"无法获取文件ETag: {file_info.fileName}")
            except Exception as e:
                logger.error(f"获取文件元数据失败: {file_info.fileName}, error: {e}")
        else:
            files_without_external_url.append(file_info.fileName)
            logger.warning(f"文件缺少外部URL: {file_info.fileName}, fileUrl={file_info.fileUrl}")

    if not file_signatures:
        if files_without_external_url:
            raise ValueError(
                f"Policy {request_data.policyNo} has no valid file signatures. "
                f"Files missing external URL: {', '.join(files_without_external_url)}. "
                f"请检查文件下载/上传流程是否正常执行。"
            )
        else:
            raise ValueError(f"Policy {request_data.policyNo} has no valid file signatures.")

    sorted_identities = sorted(file_identity_list)
    signature = hashlib.md5("|".join(sorted_identities).encode("utf-8")).hexdigest()
    logger.info(f"生成缓存签名: policy={request_data.policyNo}, signature={signature}, files={len(file_identity_list)}")

    local_vs_path = os.path.join(LOCAL_CACHE_DIR, request_data.policyNo, signature)
    remote_vs_prefix = f"{OSS_BASE_PREFIX}/vectorstores/{request_data.policyNo}/{signature}/"

    cached_result = try_reuse_cache(request_data.policyNo, signature, local_vs_path, remote_vs_prefix)
    if cached_result:
        logger.info(f"成功复用缓存，跳过文档处理流程: policy={request_data.policyNo}, signature={signature}")
        return cached_result

    total_text = process_input_files(request_data, signature)

    markdown_catalog_with_idx = markdown_parser(total_text, request_data.policyNo)

    catalog_md_path = markdown_catalog_with_idx.get("catalog_md_path", "")
    parsed_md_path = markdown_catalog_with_idx.get("parsed_md_path", "")
    chunks_json_path = markdown_catalog_with_idx.get("markdown_chunks_with_idx_json_path", "")

    if build_vectorstore:
        chunks = markdown_catalog_with_idx.get("chunks", [])
        if chunks:
            try:
                build_vectorstore_from_chunks(
                    policy_no=request_data.policyNo,
                    chunk_records=chunks,
                    local_vs_path=local_vs_path,
                    remote_vs_prefix=remote_vs_prefix
                )

                metadata = {
                    "policy_id": request_data.policyNo,
                    "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "file_signatures": file_signatures,
                    "file_identities": file_identity_list,
                    "file_names": [f.fileName for f in request_data.fileList if f.fileName],
                    "total_files": len(file_signatures),
                    "cache_signature": signature
                }
                metadata_path = os.path.join(local_vs_path, "metadata.json")
                os.makedirs(local_vs_path, exist_ok=True)
                with open(metadata_path, 'w') as f:
                    json.dump(metadata, f, ensure_ascii=False, indent=2)
                logger.info(f"创建元数据文件: {metadata_path}")

                logger.info(f"向量库构建完成, policy_no={request_data.policyNo}")
            except Exception as e:
                logger.error(f"向量库构建失败: {e}, 将在后续检索时重建")

    if build_vectorstore and remote_vs_prefix and local_vs_path:
        upload_vectorstore_cache(
            policy_no=request_data.policyNo,
            catalog_md_path=catalog_md_path,
            parsed_md_path=parsed_md_path,
            chunks_json_path=chunks_json_path,
            local_vs_path=local_vs_path,
            remote_vs_prefix=remote_vs_prefix
        )

    if parsed_md_path and os.path.exists(parsed_md_path):
        try:
            object_name = f"{request_data.policyNo}_parsed.md"
            oss_upload_parsed_md_file(object_name, parsed_md_path)
            logger.info(f"已上传解析后的Markdown文件到OSS: {object_name}")
        except Exception as e:
            logger.warning(f"上传解析后的Markdown文件到OSS失败: {e}")

    return markdown_catalog_with_idx


def document_understanding_deconstruction(request_data: DsRequest):
    """
    文档理解主流程

    Args:
        request_data: 拆解请求输入

    Returns:
        total_text: 原始合并文本
        markdown_catalog_with_idx: 解析结果 (包含 chunks)
    """
    # Step 0: 尝试复用缓存
    # 从 fileList 提取文件标识（使用文件名+OSS ETag组合）
    file_signatures = []
    file_identity_list = []  # 用于生成缓存签名：文件名+ETag
    files_without_external_url = []
    for file_info in request_data.productInfo.fileList:
        if file_info.fileExternalUrl:
            try:
                # 从OSS获取文件元数据，提取ETag作为文件签名
                etag = oss_get_object_metadata(file_info.fileExternalUrl).get('etag', '')
                if etag:
                    file_signatures.append(etag)
                    # 组合文件名和ETag，确保不同文件即使ETag相同也不会碰撞
                    file_identity = f"{file_info.fileName or ''}:{etag}"
                    file_identity_list.append(file_identity)
                    logger.info(f"获取文件签名: {file_info.fileName} -> {etag}")
                else:
                    logger.warning(f"无法获取文件ETag: {file_info.fileName}")
            except Exception as e:
                logger.error(f"获取文件元数据失败: {file_info.fileName}, error: {e}")
        else:
            files_without_external_url.append(file_info.fileName)
            logger.warning(f"文件缺少外部URL: {file_info.fileName}, fileUrl={file_info.fileUrl}")

    if not file_signatures:
        if files_without_external_url:
            raise ValueError(f"Policy {request_data.policyNo} has no valid file signatures. "
                           f"Files missing external URL: {', '.join(files_without_external_url)}. "
                           f"请检查文件下载/上传流程是否正常执行。")
        else:
            raise ValueError(f"Policy {request_data.policyNo} has no valid file signatures.")

    # 生成最终缓存签名（在OSS上传之前完成）
    # 使用文件名+ETag组合作为签名，避免不同文件碰巧有相同ETag导致的缓存错误复用
    sorted_identities = sorted(file_identity_list)
    signature = hashlib.md5("|".join(sorted_identities).encode("utf-8")).hexdigest()
    logger.info(f"生成缓存签名: policy={request_data.policyNo}, signature={signature}, files={len(file_identity_list)}")

    local_vs_path = os.path.join(LOCAL_CACHE_DIR, request_data.policyNo, signature)
    remote_vs_prefix = f"{OSS_BASE_PREFIX}/vectorstores/{request_data.policyNo}/{signature}/"

    # 尝试复用缓存：先检查本地，再检查远程
    cached_result = try_reuse_cache(request_data.policyNo, signature, local_vs_path, remote_vs_prefix)
    if cached_result:
        logger.info(f"成功复用缓存，跳过文档处理流程: policy={request_data.policyNo}, signature={signature}")
        return cached_result

    # Step 1: Process PDF files and text（上传mineru结果到oss）
    total_text = process_input_files_deconstruction(request_data, signature)

    # Step 2: Generate Markdown catalog with indexes（上传parsed结果到oss）
    markdown_catalog_with_idx = markdown_parser(total_text, request_data.policyNo)

    # 从解析结果中提取三个文件路径
    catalog_md_path = markdown_catalog_with_idx.get("catalog_md_path", "")
    parsed_md_path = markdown_catalog_with_idx.get("parsed_md_path", "")
    chunks_json_path = markdown_catalog_with_idx.get("markdown_chunks_with_idx_json_path", "")

    # Step 3: 直接利用已生成的 chunks 构建向量库 (避免 policy_manager 重复处理)
    chunks = markdown_catalog_with_idx.get("chunks", [])
    if chunks:
        try:

            build_vectorstore_from_chunks(
                policy_no=request_data.policyNo,
                chunk_records=chunks,
                local_vs_path=local_vs_path,
                remote_vs_prefix=remote_vs_prefix
            )

            # 创建元数据文件（在OSS上传之前）
            metadata = {
                "policy_id": request_data.policyNo,
                "created_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "file_signatures": file_signatures,
                "file_identities": file_identity_list,  # 文件名+ETag组合，用于生成缓存签名
                "file_names": [f.fileName for f in request_data.productInfo.fileList if f.fileName],
                "total_files": len(file_signatures),
                "cache_signature": signature
            }
            metadata_path = os.path.join(local_vs_path, "metadata.json")
            os.makedirs(local_vs_path, exist_ok=True)
            with open(metadata_path, 'w') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            logger.info(f"创建元数据文件: {metadata_path}")

            logger.info(f"向量库构建完成, policy_no={request_data.policyNo}")
        except Exception as e:
            logger.error(f"向量库构建失败: {e}, 将在后续检索时重建")

    # Step 4: Upload vectorstore cache to OSS
    if remote_vs_prefix and local_vs_path:
        upload_vectorstore_cache(
            policy_no=request_data.policyNo,
            catalog_md_path=catalog_md_path,
            parsed_md_path=parsed_md_path,
            chunks_json_path=chunks_json_path,
            local_vs_path=local_vs_path,
            remote_vs_prefix=remote_vs_prefix
        )

    # Step 5: Upload parsed markdown file to OSS
    if parsed_md_path and os.path.exists(parsed_md_path):
        try:
            object_name = f"{request_data.policyNo}_parsed.md"
            oss_upload_parsed_md_file(object_name, parsed_md_path)
            logger.info(f"已上传解析后的Markdown文件到OSS: {object_name}")
        except Exception as e:
            logger.warning(f"上传解析后的Markdown文件到OSS失败: {e}")

    return markdown_catalog_with_idx

def upload_vectorstore_cache(
    policy_no: str,
    catalog_md_path: str,
    parsed_md_path: str,
    chunks_json_path: str,
    local_vs_path: str,
    remote_vs_prefix: str
) -> None:
    """
    上传 Vectorstore 缓存文件到 OSS

    Args:
        policy_no: 保单号
        catalog_md_path: 目录文件路径
        parsed_md_path: 解析后的 Markdown 文件路径
        chunks_json_path: chunks JSON 文件路径
        local_vs_path: 本地向量库路径（临时用于复制文件）
        remote_vs_prefix: OSS 远程前缀
    """
    logger.info(f"开始上传 vectorstore 缓存文件到: {remote_vs_prefix}")

    # 复制 catalog、parsed、chunks JSON 和 metadata 文件到向量库目录
    files_to_copy = [
        (catalog_md_path, f"{policy_no}_catalog.md"),
        (parsed_md_path, f"{policy_no}_parsed.md"),
        (chunks_json_path, f"{policy_no}_chunks.json"),
        # (os.path.join(local_vs_path, "metadata.json"), "metadata.json")#已经保存过了
    ]

    for source_path, target_name in files_to_copy:
        if source_path and os.path.exists(source_path):
            target_path = os.path.join(local_vs_path, target_name)
            shutil.copy2(source_path, target_path)
            logger.info(f"已复制: {target_name}")

    # 上传到 OSS
    oss_upload_dir(local_vs_path, remote_vs_prefix)
    logger.info("Vectorstore 缓存文件上传完成")

def process_input_files_deconstruction(request_data: DsRequest, signature: str) -> str:
    """
    处理PDF文件和文本列表

    Args:
        request_data: 拆解请求输入模型
        signature: 缓存签名（基于文件ETag生成）

    Returns:
        合并后的文本内容
    """
    total_text = ""

    # 处理PDF文件
    if request_data.productInfo.fileList:
        logger.info(f"处理{len(request_data.productInfo.fileList)}个PDF文件，policyNo={request_data.policyNo}")
        try:
            merged_pdf_content = process_pdf_file_list_deconstruction(
                request_data.productInfo.fileList,
                request_data.policyNo,
                signature
            )
            total_text += merged_pdf_content + "\n\n"
            logger.info(f"PDF处理完成，policyNo={request_data.policyNo}")
        except Exception as e:
            logger.error(f"PDF处理失败，policyNo={request_data.policyNo}: {e}")
            raise Exception(f"PDF文件处理失败: {e}")

    return total_text


def process_input_files(request_data: DeconstructInput, signature: str) -> str:
    """
    处理PDF文件和文本列表（旧接口）

    Args:
        request_data: 拆解请求输入模型
        signature: 缓存签名（基于文件ETag生成）

    Returns:
        合并后的文本内容
    """
    total_text = ""

    if request_data.fileList:
        logger.info(f"处理{len(request_data.fileList)}个PDF文件，deconstructId={request_data.deconstructId}")
        try:
            merged_pdf_content = process_pdf_file_list(
                request_data.fileList,
                request_data.policyNo,
                signature
            )
            total_text += merged_pdf_content + "\n\n"
            logger.info(f"PDF处理完成，deconstructId={request_data.deconstructId}")
        except Exception as e:
            logger.error(f"PDF处理失败，deconstructId={request_data.deconstructId}: {e}")
            raise Exception(f"PDF文件处理失败: {e}")

    for text_item in request_data.textList:
        text_type = text_item.textType
        text_info = text_item.textInfo or ""

        if text_type == '特别约定':
            total_text += "# 特别约定如下：\n" + text_info
        elif text_type == '协议':
            total_text += "# 协议补充如下：\n" + text_info

    return total_text

def try_reuse_cache(policy_no: str, signature: str, local_vs_path: str, remote_vs_prefix: str) -> dict | None:
    """
    尝试复用缓存的解析结果

    检查逻辑：
    1. 检查本地缓存是否有效（包含必要的解析文件）
    2. 如果本地无效，检查远程OSS缓存是否存在
    3. 如果远程存在，下载缓存文件
    4. 加载并返回缓存的解析结果

    Args:
        policy_no: 保单号
        signature: 缓存签名
        local_vs_path: 本地向量库路径
        remote_vs_prefix: 远程OSS前缀

    Returns:
        如果成功复用缓存，返回解析结果字典；否则返回 None
    """
    cache_hit = False

    # 1. 检查本地缓存是否有效
    if os.path.exists(local_vs_path):
        # 检查是否存在缓存的解析文件
        catalog_path = os.path.join(local_vs_path, f"{policy_no}_catalog.md")
        parsed_path = os.path.join(local_vs_path, f"{policy_no}_parsed.md")
        chunks_path = os.path.join(local_vs_path, f"{policy_no}_chunks.json")

        if all(os.path.exists(p) for p in [catalog_path, parsed_path, chunks_path]):
            logger.info(f"本地缓存命中: {local_vs_path}")
            cache_hit = True
        else:
            logger.info(f"本地缓存不完整，缺少解析文件: {local_vs_path}")

    # 2. 如果本地没有，检查并下载远程缓存
    if not cache_hit:
        logger.info(f"检查远程缓存: {remote_vs_prefix}")
        if oss_prefix_exists(remote_vs_prefix):
            logger.info(f"远程缓存命中，开始下载: {remote_vs_prefix}")

            # 清理本地残留（如果存在）
            if os.path.exists(local_vs_path):
                shutil.rmtree(local_vs_path)

            # 下载整个缓存目录
            try:
                oss_download_dir(remote_vs_prefix, local_vs_path)

                # 验证下载的缓存是否完整
                catalog_path = os.path.join(local_vs_path, f"{policy_no}_catalog.md")
                parsed_path = os.path.join(local_vs_path, f"{policy_no}_parsed.md")
                chunks_path = os.path.join(local_vs_path, f"{policy_no}_chunks.json")

                if all(os.path.exists(p) for p in [catalog_path, parsed_path, chunks_path]):
                    logger.info(f"远程缓存下载成功: {local_vs_path}")
                    cache_hit = True
                else:
                    logger.warning(f"下载的远程缓存不完整")
            except Exception as e:
                logger.error(f"下载远程缓存失败: {e}")

    # 3. 如果缓存命中，加载并返回结果
    if cache_hit:
        try:
            # 读取 chunks 数据
            chunks_path = os.path.join(local_vs_path, f"{policy_no}_chunks.json")
            with open(chunks_path, 'r', encoding='utf-8') as f:
                chunks = json.load(f)

            result = {
                "policy_no": policy_no,
                "signature": signature,
                "cache_path": local_vs_path,
                "reuse_cached": True,
                "chunks": chunks,
                "catalog_md_path": os.path.join(local_vs_path, f"{policy_no}_catalog.md"),
                "parsed_md_path": os.path.join(local_vs_path, f"{policy_no}_parsed.md"),
                "markdown_chunks_with_idx_json_path": chunks_path
            }

            logger.info(f"成功加载缓存的解析结果: {len(chunks)} chunks")
            return result

        except Exception as e:
            logger.error(f"加载缓存结果失败: {e}")
            return None

    # 没有可用的缓存
    return None
