"""
Date: 2025-12-12 16:50:27
LastEditTime: 2026-01-29 13:39:45
Description: 这是默认设置,可以在设置》工具》File Description中进行配置
"""
import hashlib
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote

from infrastructure.http_session import get_oss_session
from models.oldpydantic.request import FileInfo
from models.pydantic.request import FileDto, convert_file_class_code_to_text
from repositories.oss_repository import oss_original_markdown_exists, oss_download_original_md_file, \
    oss_upload_original_md_file, oss_upload_pdf_and_get_url
from utils import logger
from .extract_links import extract_links_pypdf2
from .mineru_pdf_parser import MineruPDFParserAPI


def process_pdf_file_list_deconstruction(file_list: List[FileDto], prefix: str = "policy", signature: str = None) -> str:
    """
    处理PDF文件列表，下载、转换、合并为单个markdown文件，并上传到OSS
    - 增加了缓存检查逻辑：如果基于输入PDF URL生成的MD文件已存在于OSS，则直接下载并返回内容。

    Args:
        file_list: List[FileInfo] - Pydantic模型列表，每个模型包含PDF文件信息
        prefix: 合并文件的前缀，也用作OSS上的对象名基础
        signature: 可选的缓存签名（基于文件ETag生成），用于确保缓存唯一性

    Returns:
        合并后的markdown内容
    """
    # 丢弃不是pdf格式的文件并警告
    non_pdf_files = [
        file_info for file_info in file_list
        if file_info.fileFormat != "application/pdf" and file_info.fileUrl
    ]

    if non_pdf_files:
        for file_info in non_pdf_files:
            logger.warning(
                f"文件 '{file_info.fileName}' 格式为 '{file_info.fileFormat}'，不是PDF格式，将被丢弃。URL: {file_info.fileUrl}")

    # 筛选PDF文件列表
    initial_pdf_files = [
        file_info for file_info in file_list
        if file_info.fileFormat == "application/pdf" and file_info.fileUrl
    ]

    if not initial_pdf_files:
        raise ValueError("文件列表中没有找到有效的PDF文件URL")

    # 签名参数是必需的，用于确保不同文件组合不会误用缓存
    if not signature:
        raise ValueError("signature 参数是必需的，用于确保缓存唯一性。请从 pipeline 传入基于文件ETag生成的签名。")

    # 使用 signature 生成唯一的缓存文件名
    merged_md_filename = f"{prefix}_merged_{len(initial_pdf_files)}_files_{signature}.md"

    # 检查OSS中是否已存在合并后的markdown文件
    if oss_original_markdown_exists(merged_md_filename):
        logger.info(f"发现已处理的Markdown文件 '{merged_md_filename}'，从OSS下载。")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                local_md_path = os.path.join(temp_dir, merged_md_filename)
                oss_download_original_md_file(merged_md_filename, local_md_path)
                with open(local_md_path, 'r', encoding='utf-8') as f:
                    return f.read()
        except Exception as e:
            logger.warning(f"从OSS下载缓存文件失败: {e}。将继续执行转换流程。")

    def calculate_md5(file_path: str) -> str:
        """计算文件的MD5哈希值"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloaded_pdfs = []  # (pdf_path, pdf_external_url, file_class, original_name)
            new_pdf_urls = set()
            processed_hashes = set()  # 用于存储已处理文件的MD5哈希值

            # 1. 先下载所有初始PDF
            logger.info(f"开始下载 {len(initial_pdf_files)} 个初始PDF文件...")
            for i, file_info in enumerate(initial_pdf_files, 1):
                try:
                    pdf_url = file_info.fileUrl
                    # 直接访问可选字段，Pydantic确保字段存在（值为None或实际值）
                    pdf_external_url = file_info.fileExternalUrl
                    file_class = file_info.fileClass

                    # 获取原始文件名 - 优先使用fileName，否则从URL提取
                    original_name = file_info.fileName if file_info.fileName else extract_filename_from_url(pdf_url)

                    # 下载PDF文件
                    pdf_path = download_pdf_from_url(pdf_url, temp_dir)

                    # 计算MD5并去重
                    file_hash = calculate_md5(pdf_path)
                    if file_hash in processed_hashes:
                        logger.info(f"跳过重复的PDF文件 (URL: {pdf_url})")
                        os.remove(pdf_path)  # 删除重复下载的文件
                        continue

                    processed_hashes.add(file_hash)
                    downloaded_pdfs.append((pdf_path, pdf_external_url, file_class, original_name))
                except Exception as e:
                    logger.error(f"下载第 {i} 个初始PDF文件失败 (URL: {file_info.fileUrl}): {e}")
                    raise ValueError(f"下载初始PDF失败: {e}") from e
            # 2. 检查是否成功下载了文件
            if not downloaded_pdfs:
                raise ValueError("没有成功下载任何PDF文件")

            logger.info(f"成功下载 {len(downloaded_pdfs)} 个PDF文件，准备进行转换...")

            # 从已下载的PDF中提取链接
            logger.info("开始从已下载的PDF中提取新链接...")
            for pdf_path, _, _, _ in downloaded_pdfs:
                try:
                    links = extract_links_pypdf2(pdf_path)
                    for link in links:
                        new_pdf_urls.add(link)
                except Exception as e:
                    logger.warning(f"从 {pdf_path} 提取链接失败: {e}")

            # 3. 下载从链接中找到的新PDF
            if new_pdf_urls:
                logger.info(f"发现 {len(new_pdf_urls)} 个新PDF链接，开始下载...")
                for i, pdf_url in enumerate(new_pdf_urls, 1):
                    try:
                        # 提取原始文件名
                        original_name = extract_filename_from_url(pdf_url)
                        pdf_path = download_pdf_from_url(pdf_url, temp_dir)

                        # 计算MD5并去重
                        file_hash = calculate_md5(pdf_path)
                        if file_hash in processed_hashes:
                            logger.info(f"跳过重复的PDF文件 (URL: {pdf_url})")
                            os.remove(pdf_path)
                            continue

                        processed_hashes.add(file_hash)
                        # 使用原始 URL 作为 pdf_external_url
                        downloaded_pdfs.append((pdf_path, pdf_url, "附件下载", original_name))  # 新下载的PDF
                    except Exception as e:
                        logger.error(f"下载第 {i} 个新PDF文件失败 (URL: {pdf_url}): {e}")
                        # 选择性地忽略失败的链接下载
                        # raise ValueError(f"下载新PDF失败: {e}") from e

            # 4. 并行转换所有下载的PDF为Markdown
            md_paths = []
            logger.info(f"开始并行转换总共 {len(downloaded_pdfs)} 个PDF文件到Markdown...")
            md_file_classes = {}  # 存储每个markdown路径对应的file_class
            md_original_names = {}  # 存储每个markdown路径对应的原始文件名

            def convert_single_pdf(args: Tuple[int, str, Optional[str], Optional[str], str]) -> Tuple[int, Optional[str], Optional[str], Optional[str]]:
                """转换单个PDF，返回 (index, md_path, file_class, original_name)"""
                idx, pdf_path, pdf_external_url, file_class, original_name = args
                try:
                    md_path = convert_pdf_to_markdown(pdf_path, temp_dir, pdf_external_url=pdf_external_url)
                    return idx, md_path, file_class, original_name
                except Exception as e:
                    logger.error(f"处理第 {idx} 个PDF文件失败 (Path: {pdf_path}): {e}")
                    raise

            # 准备任务参数
            convert_tasks = [
                (i, pdf_path, pdf_external_url, file_class, original_name)
                for i, (pdf_path, pdf_external_url, file_class, original_name) in enumerate(downloaded_pdfs, 1)
            ]

            # 并行执行转换
            max_workers = min(5, len(convert_tasks))  # 最多5个并发，避免过载
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(convert_single_pdf, task): task for task in convert_tasks}
                for future in as_completed(futures):
                    idx, md_path, file_class, original_name = future.result()
                    if md_path:
                        md_paths.append(md_path)
                        # 存储file_class和原始文件名，以markdown路径为key
                        if file_class:
                            md_file_classes[md_path] = convert_file_class_code_to_text(file_class)
                        if original_name:
                            # 将PDF文件名转换为markdown文件名格式（替换.pdf为.md）
                            if original_name.lower().endswith('.pdf'):
                                original_name = original_name[:-4] + '.md'
                            md_original_names[md_path] = original_name

            if not md_paths:
                raise ValueError("所有PDF文件都未能成功转换为Markdown")

            # 5. 合并所有markdown文件并返回内容
            # 注意：这里不使用 merge_markdown_files 返回的文件名，而是使用前面生成的确定性文件名
            merged_md_path, _ = merge_markdown_files(md_paths, prefix, temp_dir, md_file_classes, md_original_names)
            with open(merged_md_path, 'r', encoding='utf-8') as f:
                merged_content = f.read()

            if not merged_content.strip():
                raise ValueError("合并后的markdown内容为空")

            # 6. 上传合并后的文件到OSS
            logger.info(f"准备将合并后的文件上传到OSS，对象名为: {merged_md_filename}")
            oss_upload_original_md_file(merged_md_filename, merged_md_path)
            logger.info("文件成功上传到OSS")

            return merged_content

    except Exception as e:
        logger.error(f"处理PDF文件列表时发生严重错误: {e}")
        # 重新抛出异常，以便上层调用者可以捕获
        raise


def process_pdf_file_list(file_list: List[FileInfo], prefix: str = "policy", signature: str = None) -> str:
    """
    处理PDF文件列表（旧接口），下载、转换、合并为单个markdown文件，并上传到OSS
    - 增加了缓存检查逻辑：如果基于输入PDF URL生成的MD文件已存在于OSS，则直接下载并返回内容。

    Args:
        file_list: List[FileInfo] - Pydantic模型列表，每个模型包含PDF文件信息
        prefix: 合并文件的前缀，也用作OSS上的对象名基础
        signature: 可选的缓存签名（基于文件ETag生成），用于确保缓存唯一性

    Returns:
        合并后的markdown内容
    """
    initial_pdf_files = [
        file_info for file_info in file_list
        if file_info.fileType == "application/pdf" and file_info.fileUrl
    ]

    if not initial_pdf_files:
        raise ValueError("文件列表中没有找到有效的PDF文件URL")

    if not signature:
        raise ValueError("signature 参数是必需的，用于确保缓存唯一性。请从 pipeline 传入基于文件ETag生成的签名。")

    merged_md_filename = f"{prefix}_merged_{len(initial_pdf_files)}_files_{signature}.md"

    if oss_original_markdown_exists(merged_md_filename):
        logger.info(f"发现已处理的Markdown文件 '{merged_md_filename}'，从OSS下载。")
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                local_md_path = os.path.join(temp_dir, merged_md_filename)
                oss_download_original_md_file(merged_md_filename, local_md_path)
                with open(local_md_path, 'r', encoding='utf-8') as f:
                    return f.read()
        except Exception as e:
            logger.warning(f"从OSS下载缓存文件失败: {e}。将继续执行转换流程。")

    def calculate_md5(file_path: str) -> str:
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            downloaded_pdfs = []
            new_pdf_urls = set()
            processed_hashes = set()

            logger.info(f"开始下载 {len(initial_pdf_files)} 个初始PDF文件...")
            for i, file_info in enumerate(initial_pdf_files, 1):
                try:
                    pdf_url = file_info.fileUrl
                    pdf_external_url = file_info.fileExternalUrl
                    file_class = file_info.fileClass

                    original_name = file_info.fileName if file_info.fileName else extract_filename_from_url(pdf_url)

                    pdf_path = download_pdf_from_url(pdf_url, temp_dir)

                    file_hash = calculate_md5(pdf_path)
                    if file_hash in processed_hashes:
                        logger.info(f"跳过重复的PDF文件 (URL: {pdf_url})")
                        os.remove(pdf_path)
                        continue

                    processed_hashes.add(file_hash)
                    downloaded_pdfs.append((pdf_path, pdf_external_url, file_class, original_name))
                except Exception as e:
                    logger.error(f"下载第 {i} 个初始PDF文件失败 (URL: {file_info.fileUrl}): {e}")
                    raise ValueError(f"下载初始PDF失败: {e}") from e

            if not downloaded_pdfs:
                raise ValueError("没有成功下载任何PDF文件")

            logger.info(f"成功下载 {len(downloaded_pdfs)} 个PDF文件，准备进行转换...")

            logger.info("开始从已下载的PDF中提取新链接...")
            for pdf_path, _, _, _ in downloaded_pdfs:
                try:
                    links = extract_links_pypdf2(pdf_path)
                    for link in links:
                        new_pdf_urls.add(link)
                except Exception as e:
                    logger.warning(f"从 {pdf_path} 提取链接失败: {e}")

            if new_pdf_urls:
                logger.info(f"发现 {len(new_pdf_urls)} 个新PDF链接，开始下载...")
                for i, pdf_url in enumerate(new_pdf_urls, 1):
                    try:
                        original_name = extract_filename_from_url(pdf_url)
                        pdf_path = download_pdf_from_url(pdf_url, temp_dir)

                        file_hash = calculate_md5(pdf_path)
                        if file_hash in processed_hashes:
                            logger.info(f"跳过重复的PDF文件 (URL: {pdf_url})")
                            os.remove(pdf_path)
                            continue

                        processed_hashes.add(file_hash)
                        downloaded_pdfs.append((pdf_path, pdf_url, "附件下载", original_name))
                    except Exception as e:
                        logger.error(f"下载第 {i} 个新PDF文件失败 (URL: {pdf_url}): {e}")

            # 并行转换所有下载的PDF为Markdown
            md_paths = []
            logger.info(f"开始并行转换总共 {len(downloaded_pdfs)} 个PDF文件到Markdown...")
            md_file_classes = {}
            md_original_names = {}

            def convert_single_pdf_old(args: Tuple[int, str, Optional[str], Optional[str], str]) -> Tuple[int, Optional[str], Optional[str], Optional[str]]:
                """转换单个PDF，返回 (index, md_path, file_class, original_name)"""
                idx, pdf_path, pdf_external_url, file_class, original_name = args
                try:
                    md_path = convert_pdf_to_markdown(pdf_path, temp_dir, pdf_external_url=pdf_external_url)
                    return idx, md_path, file_class, original_name
                except Exception as e:
                    logger.error(f"处理第 {idx} 个PDF文件失败 (Path: {pdf_path}): {e}")
                    raise

            # 准备任务参数
            convert_tasks = [
                (i, pdf_path, pdf_external_url, file_class, original_name)
                for i, (pdf_path, pdf_external_url, file_class, original_name) in enumerate(downloaded_pdfs, 1)
            ]

            # 并行执行转换
            max_workers = min(5, len(convert_tasks))  # 最多5个并发，避免过载
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(convert_single_pdf_old, task): task for task in convert_tasks}
                for future in as_completed(futures):
                    idx, md_path, file_class, original_name = future.result()
                    if md_path:
                        md_paths.append(md_path)
                        if file_class:
                            md_file_classes[md_path] = convert_file_class_code_to_text(file_class)
                        if original_name:
                            if original_name.lower().endswith('.pdf'):
                                original_name = original_name[:-4] + '.md'
                            md_original_names[md_path] = original_name

            if not md_paths:
                raise ValueError("所有PDF文件都未能成功转换为Markdown")

            merged_md_path, _ = merge_markdown_files(md_paths, prefix, temp_dir, md_file_classes, md_original_names)
            with open(merged_md_path, 'r', encoding='utf-8') as f:
                merged_content = f.read()

            if not merged_content.strip():
                raise ValueError("合并后的markdown内容为空")

            logger.info(f"准备将合并后的文件上传到OSS，对象名为: {merged_md_filename}")
            oss_upload_original_md_file(merged_md_filename, merged_md_path)
            logger.info("文件成功上传到OSS")

            return merged_content

    except Exception as e:
        logger.error(f"处理PDF文件列表时发生严重错误: {e}")
        raise

def extract_filename_from_url(url: str) -> Optional[str]:
    """
    从URL中提取原始文件名

    Args:
        url: 文件URL

    Returns:
        解码后的文件名，如果无法提取则返回None
    """
    try:
        parsed = urlparse(url)
        # 获取路径部分，去掉查询参数
        path = parsed.path
        if path:
            # 获取最后一个路径段作为文件名
            filename = os.path.basename(path)
            # URL解码（处理中文等特殊字符）
            filename = unquote(filename)
            if filename:
                return filename
    except Exception as e:
        logger.warning(f"无法从URL提取文件名: {url}, 错误: {e}")
    return None


def download_pdf_from_url(pdf_url: str, temp_dir: str) -> str:
    """
    从URL下载PDF文件

    Args:
        pdf_url: PDF文件的下载URL
        temp_dir: 临时文件目录

    Returns:
        下载的PDF文件路径
    """
    try:
        # 使用OSS专用Session
        session = get_oss_session()
        response = session.get(pdf_url, timeout=30)
        response.raise_for_status()

        # 生成临时文件名
        timestamp = str(int(time.time()))
        temp_pdf_path = os.path.join(temp_dir, f"temp_pdf_{timestamp}_{hash(pdf_url) % 10000}.pdf")

        with open(temp_pdf_path, 'wb') as f:
            f.write(response.content)

        logger.info(f"PDF下载成功 -> {temp_pdf_path}")
        return temp_pdf_path

    except Exception as e:
        logger.error(f"PDF下载失败，错误: {str(e)}")
        raise

def _convert_pdf_to_markdown_locally(pdf_path: str, temp_dir: str) -> str:
    """使用 PDFium 提取可搜索 PDF 的文本，供无需 MinerU 的本地体验模式使用。"""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_path)
    try:
        page_texts = [page.get_textpage().get_text_range() for page in document]
    finally:
        document.close()

    raw_text = "\n".join(page_texts).strip()
    if not raw_text:
        raise ValueError("本地 PDF 文本提取结果为空；扫描件请改用 MinerU 模式")

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    markdown_lines = []
    paragraph = []

    def flush_paragraph():
        if paragraph:
            markdown_lines.append("".join(paragraph))
            paragraph.clear()

    for index, line in enumerate(lines):
        is_heading = index == 0 or re.match(r"^第[一二三四五六七八九十百零0-9]+条", line) or line == "特别约定"
        if is_heading:
            flush_paragraph()
            markdown_lines.append(f"{'#' if index == 0 else '##'} {line}")
        else:
            paragraph.append(line)
    flush_paragraph()

    markdown_content = "\n\n".join(markdown_lines) + "\n"
    temp_md_path = os.path.join(temp_dir, f"local_converted_{int(time.time())}.md")
    with open(temp_md_path, "w", encoding="utf-8") as output:
        output.write(markdown_content)
    logger.info(f"本地 PDF 转 Markdown 成功: {pdf_path} -> {temp_md_path}")
    return temp_md_path


def convert_pdf_to_markdown(pdf_path: str, temp_dir: str, pdf_external_url: str = None) -> str:
    """
    将PDF文件转换为markdown格式，带重试机制

    Args:
        pdf_path: PDF文件路径
        temp_dir: 临时文件目录
        pdf_external_url: PDF的外部可访问URL

    Returns:
        转换后的markdown文件路径
    """
    if os.getenv("PDF_PARSER_MODE", "mineru").lower() == "local":
        return _convert_pdf_to_markdown_locally(pdf_path, temp_dir)

    max_retries = 3
    base_delay = 5  # 基础延迟5秒

    for attempt in range(max_retries):
        try:
            # 若未提供外部URL，则上传到OSS并生成一个临时可访问的预签名URL
            if not pdf_external_url:
                try:
                    pdf_external_url = oss_upload_pdf_and_get_url(pdf_path)
                    logger.info(f"为本地PDF生成预签名URL: {pdf_external_url}")
                except Exception as e:
                    logger.error(f"生成PDF外部URL失败（上传到OSS或预签名失败）: {e}")
                    raise

            # 使用KB项目的MinerU解析器（惰性导入，避免顶层导入失败导致 NameError）

            timestamp = str(int(time.time()))
            temp_md_path = os.path.join(temp_dir, f"temp_converted_{timestamp}_attempt{attempt}.md")
            mineru_parser = MineruPDFParserAPI()
            # 调用MinerU解析PDF（APIPDFParser 需要 pdf_external_url）
            markdown_content = mineru_parser.parse_pdf_from_url(pdf_external_url=pdf_external_url)

            with open(temp_md_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)

            logger.info(f"PDF转markdown成功: {pdf_path} -> {temp_md_path}")
            return temp_md_path

        except RuntimeError as e:
            error_msg = str(e)
            # 检查是否是解析失败的错误，如果是则进行重试
            if "parsing failed" in error_msg and attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)  # 指数退避：5秒、10秒、20秒
                logger.warning(
                    f"PDF解析失败 (attempt {attempt + 1}/{max_retries}): {error_msg}. "
                    f"等待 {delay} 秒后重试..."
                )
                time.sleep(delay)
                continue
            else:
                logger.error(f"PDF转markdown失败: {pdf_path}, 错误: {error_msg} (尝试 {attempt + 1}/{max_retries})")
                raise
        except Exception as e:
            logger.error(f"PDF转markdown失败: {pdf_path}, 错误: {str(e)} (尝试 {attempt + 1}/{max_retries})")
            raise

    # 如果所有重试都失败，抛出最后一次的异常
    raise RuntimeError(f"PDF解析失败，已尝试 {max_retries} 次仍未成功")

def merge_markdown_files(md_paths: List[str], prefix: str, temp_dir: str,
                         pdf_file_classes: Dict[str, str] = None,
                         md_original_names: Dict[str, str] = None) -> tuple[str, str]:
    """
    合并多个markdown文件

    Args:
        md_paths: markdown文件路径列表
        prefix: 合并文件的前缀
        temp_dir: 临时文件目录
        pdf_file_classes: 可选，字典，映射markdown路径到file_class字符串
        md_original_names: 可选，字典，映射markdown路径到原始文件名

    Returns:
        (合并后的markdown文件路径, 文件名)
    """
    try:
        timestamp = str(int(time.time()))
        merged_md_filename = f"{prefix}_merged_{timestamp}.md"
        merged_md_path = os.path.join(temp_dir, merged_md_filename)

        merged_content = ""
        total_text = ""

        for i, md_path in enumerate(md_paths, 1):
            if md_path and os.path.exists(md_path):
                with open(md_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 添加FILE标记，包含file_name和file_class（如果有）
                    # 优先使用原始文件名，如果没有则使用临时文件名
                    file_name = (md_original_names or {}).get(md_path) or os.path.basename(md_path)
                    file_class = (pdf_file_classes or {}).get(md_path)

                    if file_class:
                        # 将文件类型代码映射为中文文本
                        file_class_text = convert_file_class_code_to_text(file_class)
                        # 格式: FILE: file_name | class (中文文本)
                        file_marker = f"FILE: {file_name} | {file_class_text}"
                    else:
                        # 格式: FILE: file_name
                        file_marker = f"FILE: {file_name}"

                    total_text += f"# {file_marker}\n\n{content}\n\n"
            else:
                logger.warning(f"markdown文件不存在或为None: {md_path}")

        # 写入合并后的文件
        with open(merged_md_path, 'w', encoding='utf-8') as f:
            f.write(total_text)

        logger.info(f"markdown文件合并成功: {merged_md_path}")
        return merged_md_path, merged_md_filename

    except Exception as e:
        logger.error(f"markdown文件合并失败: {str(e)}")
        raise

