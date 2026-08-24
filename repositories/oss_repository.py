"""
Date: 2025-09-26 14:54:44
LastEditTime: 2026-01-20 16:36:14
Description:
OSS Repository - Abstracts OSS operations
Wraps badcase/oss_IO.py functions with a clean interface
"""
# 新增导入
import json
import os
import time
from datetime import timedelta
from typing import Tuple, Optional
from urllib.parse import urlparse, unquote

import alibabacloud_oss_v2 as oss
from alibabacloud_oss_v2.credentials import StaticCredentialsProvider
from alibabacloud_oss_v2.exceptions import ServiceError  # 新增 ServiceError

from config import OSS_REGION, OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_BUCKET_NAME, OSS_ENDPOINT
# 始终使用公网 endpoint，确保外部可访问
from config import settings
from utils import logger  # 新增

# The endpoint is supplied by the user; no project bucket is embedded.
logger.info(f"使用 OSS 公网 Endpoint: {OSS_ENDPOINT}")

# 初始化客户端
credentials_provider = StaticCredentialsProvider(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
cfg = oss.config.load_default()
cfg.credentials_provider = credentials_provider
cfg.region = OSS_REGION
cfg.endpoint = OSS_ENDPOINT
client = oss.Client(cfg)

# ===== 新增：根据部署环境动态确定 OSS 基础前缀 =====
from config import OSS_BASE_PREFIX


def get_oss_base_prefix() -> str:
    """返回当前环境下的 OSS 基础前缀。prd -> DemoAssets/clausemind-demo，其它 -> DemoAssets/clausemind-demo-test"""
    return OSS_BASE_PREFIX


# 通用上传函数
def oss_upload_file(object_name: str, folder_prefix: str, *, data: Optional[bytes] = None,
                    local_file_path: Optional[str] = None) -> str:
    """通用上传：将 data 或 local_file_path 上传到 OSS，返回完整对象 key。
    - object_name: 对象名（文件名），不包含前缀
    - folder_prefix: 目录前缀，例如 'DemoAssets/clausemind-demo/mineru-output'
    - data/local_file_path: 二选一；若都提供则优先使用 data
    """
    if not folder_prefix:
        raise ValueError("folder_prefix must be a non-empty string")
    full_object_name = f"{folder_prefix.strip('/')}/{object_name}"
    if data is None:
        if not local_file_path:
            raise ValueError("Either data or local_file_path must be provided")
        with open(local_file_path, 'rb') as f:
            data = f.read()
    result = client.put_object(oss.PutObjectRequest(
        bucket=OSS_BUCKET_NAME,
        key=full_object_name,
        body=data
    ))
    logger.info(f"上传到 '{full_object_name}'，状态码：{getattr(result, 'status_code', 'unknown')}")
    return full_object_name


# 上传文件
def oss_upload_original_md_file(object_name, local_file_path):
    """上传到 mineru-output 目录。"""
    oss_upload_file(object_name, f'{OSS_BASE_PREFIX}/mineru-output', local_file_path=local_file_path)


def oss_upload_parsed_md_file(object_name, local_file_path):
    """上传到 parsed_markdown 目录。"""
    oss_upload_file(object_name, f'{OSS_BASE_PREFIX}/parsed_markdown', local_file_path=local_file_path)


# 新增：上传本地 PDF 到指定前缀并返回可下载的预签名 URL
def oss_generate_presigned_url(key: str, method: str = 'GET', expires_seconds: int = 7200) -> str:
    method = method.upper()
    if method == 'GET':
        request = oss.GetObjectRequest(bucket=OSS_BUCKET_NAME, key=key)
    elif method == 'PUT':
        request = oss.PutObjectRequest(bucket=OSS_BUCKET_NAME, key=key)
    else:
        raise ValueError(f"Unsupported method for presign: {method}")

    result = client.presign(request, expires=timedelta(seconds=expires_seconds))
    url = getattr(result, 'url', None) or getattr(result, 'URL', None)
    if not url:
        raise RuntimeError("Failed to generate presigned URL: empty result")
    return url


# 新增：上传本地 PDF 到指定前缀并返回可下载的预签名 URL
def oss_upload_pdf_and_get_url(local_pdf_path: str, folder: str = f'{OSS_BASE_PREFIX}/pdf',
                               expires_seconds: int = 7200) -> str:
    """上传本地PDF到OSS并返回一个临时可访问的预签名下载 URL。"""
    if not os.path.exists(local_pdf_path):
        raise FileNotFoundError(f"Local PDF not found: {local_pdf_path}")

    base = os.path.basename(local_pdf_path)
    name, ext = os.path.splitext(base)
    if not ext:
        ext = '.pdf'
    # ts = int(time.time())
    # obj_name = f"{name}_{ts}{ext}"
    obj_name = f"{name}{ext}"
    key = oss_upload_file(obj_name, folder, local_file_path=local_pdf_path)
    logger.info(f"PDF 已上传到 OSS: {key}")
    return oss_generate_presigned_url(key, method='GET', expires_seconds=expires_seconds)


# 新增：上传检索日志 JSON 到指定检索日志目录，并返回 key
def oss_upload_retrieval_log(policy_id: str, record: dict, folder: str = f'{OSS_BASE_PREFIX}/retrieval_logs') -> str:
    """将检索日志 JSON 上传到 OSS 检索日志目录，返回对象 key。"""
    ts = int(time.time())
    safe_policy_id = str(policy_id).replace('/', '_')
    filename = f"retrieval_log_{safe_policy_id}_{ts}.json"
    data = json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')
    key = oss_upload_file(filename, folder, data=data)
    logger.info(f"检索日志已上传到 OSS: {key}")
    return key


# 新增：上传检索日志并返回预签名 URL
def oss_upload_retrieval_log_and_get_url(policy_id: str, record: dict,
                                         folder: str = f'{OSS_BASE_PREFIX}/retrieval_logs',
                                         expires_seconds: int = 7200) -> Tuple[str, str]:
    """上传检索日志 JSON 到 OSS，并返回 (key, 临时URL)。"""
    key = oss_upload_retrieval_log(policy_id, record, folder=folder)
    url = oss_generate_presigned_url(key, method='GET', expires_seconds=expires_seconds)
    return key, url


def oss_parsed_markdown_exists(object_name: str) -> bool:
    """
    使用 head_object 检查OSS上是否存在指定对象
    """
    full_object_name = f'{OSS_BASE_PREFIX}/parsed_markdown/{object_name}'
    try:
        client.head_object(oss.HeadObjectRequest(
            bucket=OSS_BUCKET_NAME,
            key=full_object_name
        ))
        return True
    except ServiceError as e:
        # SDK 会抛出 ServiceError；404/NoSuchKey/NotFound 代表对象不存在
        code = getattr(e, "code", "") or getattr(e, "error_code", "")
        status = getattr(e, "status_code", None) or getattr(e, "http_status", None)
        if status == 404 or code in ("NoSuchKey", "NotFound"):
            return False
        logger.warning(f"检查对象是否存在时出现服务端错误，按不存在处理: {e}")
        return False
    except Exception as e:
        # 兜底：任何异常都不抛出，按不存在处理
        logger.warning(f"检查对象是否存在时出现异常，按不存在处理: {e}")
        return False


def oss_original_markdown_exists(object_name: str) -> bool:
    """
    使用 head_object 检查OSS上是否存在指定对象
    """
    full_object_name = f'{OSS_BASE_PREFIX}/mineru-output/{object_name}'
    try:
        client.head_object(oss.HeadObjectRequest(
            bucket=OSS_BUCKET_NAME,
            key=full_object_name
        ))
        return True
    except ServiceError as e:
        # SDK 会抛出 ServiceError；404/NoSuchKey/NotFound 代表对象不存在
        code = getattr(e, "code", "") or getattr(e, "error_code", "")
        status = getattr(e, "status_code", None) or getattr(e, "http_status", None)
        if status == 404 or code in ("NoSuchKey", "NotFound"):
            return False
        logger.warning(f"检查对象是否存在时出现服务端错误，按不存在处理: {e}")
        return False
    except Exception as e:
        # 兜底：任何异常都不抛出，按不存在处理
        logger.warning(f"检查对象是否存在时出现异常，按不存在处理: {e}")
        return False


# 下载文件
def oss_download_original_md_file(object_name, local_file_path):
    full_object_name = f'{OSS_BASE_PREFIX}/mineru-output/{object_name}'
    result = client.get_object(oss.GetObjectRequest(
        bucket=OSS_BUCKET_NAME,
        key=full_object_name
    ))
    with open(local_file_path, 'wb') as f:
        f.write(result.body.read())
    logger.info(f"文件已从 '{full_object_name}' 下载到：{local_file_path}")  # 替换 print，保持原句式


def oss_download_parsed_md_file(object_name, local_file_path):
    full_object_name = f'{OSS_BASE_PREFIX}/parsed_markdown/{object_name}'
    result = client.get_object(oss.GetObjectRequest(
        bucket=OSS_BUCKET_NAME,
        key=full_object_name
    ))
    with open(local_file_path, 'wb') as f:
        f.write(result.body.read())
    logger.info(f"文件已从 '{full_object_name}' 下载到：{local_file_path}")  # 替换 print，保持原句式


def oss_list_files_by_substring(substring: str, suffix: str = ".md",
                                prefix: str = f"{OSS_BASE_PREFIX}/mineru-output/") -> list[str]:
    """
    列出 OSS 目录下文件名包含 substring 的所有文件（返回去掉前缀的对象名）
    - prefix: OSS 前缀路径，默认 DemoAssets/clausemind-demo/mineru-output/
    full_object_name = f'{OSS_BASE_PREFIX}/parsed_markdown/{object_name}'
    """
    matched: list[str] = []
    marker = None
    while True:
        req = oss.ListObjectsRequest(
            bucket=OSS_BUCKET_NAME,
            prefix=prefix,
            marker=marker,
            max_keys=1000
        )
        resp = client.list_objects(req)
        for obj in resp.contents or []:
            key = obj.key  # 完整 key
            basename = os.path.basename(key)
            ok_suffix = True if suffix == "" else basename.endswith(suffix)
            if ok_suffix and substring in basename:
                # 去掉前缀，只保留文件名部分供后续下载函数使用
                matched.append(key[len(prefix):])
        if not resp.is_truncated:
            break
        marker = resp.next_marker
    return matched


def oss_list_parsed_markdown_by_substring(substring: str, suffix: str = ".md",
                                          prefix: str = f"{OSS_BASE_PREFIX}/parsed_markdown/") -> list[str]:
    """
    列出 OSS 目录下文件名包含 substring 的所有文件（返回去掉前缀的对象名）
    - prefix: OSS 前缀路径，默认 DemoAssets/clausemind-demo/parsed_markdown/"
    - suffix: 文件后缀过滤；若传入空字符串 ""，则不进行后缀过滤
    """
    matched: list[str] = []
    marker = None
    while True:
        req = oss.ListObjectsRequest(
            bucket=OSS_BUCKET_NAME,
            prefix=prefix,
            marker=marker,
            max_keys=1000
        )
        resp = client.list_objects(req)
        for obj in resp.contents or []:
            key = obj.key  # 完整 key
            basename = os.path.basename(key)
            ok_suffix = True if suffix == "" else basename.endswith(suffix)
            if ok_suffix and substring in basename:
                # 去掉前缀，只保留文件名部分供后续下载函数使用
                matched.append(key[len(prefix):])
        if not resp.is_truncated:
            break
        marker = resp.next_marker
    return matched


def oss_prefix_exists(prefix: str) -> bool:
    """
    判断某个前缀下是否已有对象
    prefix: 绝对OSS前缀(不含 bucket)
    """
    req = oss.ListObjectsRequest(
        bucket=OSS_BUCKET_NAME,
        prefix=prefix,
        max_keys=1
    )
    resp = client.list_objects(req)
    return bool(resp.contents)


def oss_upload_dir(local_dir: str, remote_prefix: str):
    """
    上传本地目录全部文件到指定 remote_prefix (必须以 / 结尾)
    remote_prefix 需包含 'DemoAssets/clausemind-demo/vectorstores/.../'
    """
    if not remote_prefix.endswith('/'):
        remote_prefix += '/'
    for root, _, files in os.walk(local_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, start=local_dir)
            key = remote_prefix + rel.replace('\\', '/')
            with open(fpath, 'rb') as f:
                data = f.read()
            client.put_object(oss.PutObjectRequest(
                bucket=OSS_BUCKET_NAME,
                key=key,
                body=data
            ))
    logger.info(f"向量库目录已上传到 '{remote_prefix}'")


def oss_download_dir(remote_prefix: str, local_dir: str):
    """
    下载 remote_prefix 下所有文件到本地目录
    """
    if not remote_prefix.endswith('/'):
        remote_prefix += '/'
    os.makedirs(local_dir, exist_ok=True)
    marker = None
    while True:
        req = oss.ListObjectsRequest(
            bucket=OSS_BUCKET_NAME,
            prefix=remote_prefix,
            marker=marker,
            max_keys=1000
        )
        resp = client.list_objects(req)
        for obj in resp.contents or []:
            rel = obj.key[len(remote_prefix):]
            if not rel:  # 目录本身
                continue
            local_path = os.path.join(local_dir, rel)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            get_resp = client.get_object(oss.GetObjectRequest(
                bucket=OSS_BUCKET_NAME,
                key=obj.key
            ))
            with open(local_path, 'wb') as f:
                f.write(get_resp.body.read())
        if not resp.is_truncated:
            break
        marker = resp.next_marker
    logger.info(f"向量库目录已从 '{remote_prefix}' 下载到本地：{local_dir}")



class OSSRepository:
    """Repository for OSS operations"""

    def __init__(self):
        """Initialize OSS repository"""
        self.logger = logger

    def upload_markdown(self, object_name: str, local_path: str) -> None:
        """
        Upload markdown file to OSS

        Args:
            object_name: OSS object name
            local_path: Local file path
        """
        try:
            oss_upload_original_md_file(object_name, local_path)
            self.logger.info(f"Uploaded markdown to OSS: {object_name}")
        except Exception as e:
            self.logger.error(f"Failed to upload markdown to OSS: {e}")
            raise

    def markdown_exists(self, object_name: str) -> bool:
        """
        Check if markdown file exists in OSS

        Args:
            object_name: OSS object name

        Returns:
            True if file exists, False otherwise
        """
        try:
            return oss_parsed_markdown_exists(object_name)
        except Exception as e:
            self.logger.error(f"Failed to check markdown existence in OSS: {e}")
            return False

    def download_markdown(self, object_name: str, local_path: str) -> None:
        """
        Download markdown file from OSS

        Args:
            object_name: OSS object name
            local_path: Local file path to save to
        """
        try:
            oss_download_original_md_file(object_name, local_path)
            self.logger.info(f"Downloaded markdown from OSS: {object_name}")
        except Exception as e:
            self.logger.error(f"Failed to download markdown from OSS: {e}")
            raise

    def upload_pdf_and_get_url(self, local_path: str) -> str:
        """
        Upload PDF to OSS and get presigned URL

        Args:
            local_path: Local PDF file path

        Returns:
            Presigned URL
        """
        try:
            url = oss_upload_pdf_and_get_url(local_path)
            self.logger.info(f"Uploaded PDF to OSS and generated presigned URL")
            return url
        except Exception as e:
            self.logger.error(f"Failed to upload PDF and get URL: {e}")
            raise

    def download_parsed_markdown(self, object_name: str, local_path: str) -> None:
        """
        Download parsed markdown file from OSS

        Args:
            object_name: OSS object name
            local_path: Local file path to save to
        """
        try:
            oss_download_parsed_md_file(object_name, local_path)
            self.logger.info(f"Downloaded parsed markdown from OSS: {object_name}")
        except Exception as e:
            self.logger.error(f"Failed to download parsed markdown from OSS: {e}")
            raise

    def upload_retrieval_log(self, policy_id: str, log_data: dict,
                           folder: str = f'{OSS_BASE_PREFIX}/retrieval_logs',
                           expires_seconds: int = 7200) -> tuple:
        """
        Upload retrieval log to OSS and get URL

        Args:
            policy_id: Policy ID
            log_data: Log data dictionary
            folder: OSS folder path
            expires_seconds: URL expiration time in seconds

        Returns:
            Tuple of (key, url)
        """
        try:
            key, url = oss_upload_retrieval_log_and_get_url(
                policy_id, log_data, folder, expires_seconds
            )
            self.logger.info(f"Uploaded retrieval log to OSS: {key}")
            return key, url
        except Exception as e:
            self.logger.error(f"Failed to upload retrieval log: {e}")
            raise

    def list_files_by_substring(self, substring: str) -> list:
        """
        List OSS files containing substring

        Args:
            substring: Substring to search for

        Returns:
            List of matching file names
        """
        try:
            return oss_list_files_by_substring(substring)
        except Exception as e:
            self.logger.error(f"Failed to list OSS files: {e}")
            return []

    def prefix_exists(self, prefix: str) -> bool:
        """
        Check if OSS prefix exists

        Args:
            prefix: OSS prefix path

        Returns:
            True if prefix exists, False otherwise
        """
        try:
            return oss_prefix_exists(prefix)
        except Exception as e:
            self.logger.error(f"Failed to check OSS prefix existence: {e}")
            return False

    def upload_directory(self, local_dir: str, oss_prefix: str) -> None:
        """
        Upload directory to OSS

        Args:
            local_dir: Local directory path
            oss_prefix: OSS prefix path
        """
        try:
            oss_upload_dir(local_dir, oss_prefix)
            self.logger.info(f"Uploaded directory to OSS: {oss_prefix}")
        except Exception as e:
            self.logger.error(f"Failed to upload directory to OSS: {e}")
            raise

    def download_directory(self, oss_prefix: str, local_dir: str) -> None:
        """
        Download directory from OSS

        Args:
            oss_prefix: OSS prefix path
            local_dir: Local directory path
        """
        try:
            oss_download_dir(oss_prefix, local_dir)
            self.logger.info(f"Downloaded directory from OSS: {oss_prefix}")
        except Exception as e:
            self.logger.error(f"Failed to download directory from OSS: {e}")
            raise


def oss_get_object_metadata(file_url: str) -> dict:
    """
    从 OSS 文件 URL 获取对象元数据

    Args:
        file_url: 对象存储 URL（可以是预签名 URL）。

    Returns:
        dict: 包含 etag、content_type、content_length 等元数据
    """
    try:
        # 解析 URL 获取 object key
        parsed_url = urlparse(file_url)

        # 从 URL 路径中提取 object key（去掉开头的 /），并进行 URL 解码
        # 对于预签名 URL，路径中的中文字符会被 URL 编码，需要解码
        object_key = unquote(parsed_url.path.lstrip('/'))

        # 调用 head_object 获取元数据
        result = client.head_object(oss.HeadObjectRequest(
            bucket=OSS_BUCKET_NAME,
            key=object_key
        ))

        # 提取元数据
        metadata = {
            'etag': getattr(result, 'etag', '').strip('"'),  # ETag 通常包含双引号，需要去除
            'content_type': getattr(result, 'content_type', ''),
            'content_length': getattr(result, 'content_length', 0),
            'last_modified': getattr(result, 'last_modified', None)
        }

        logger.debug(f"获取OSS对象元数据成功: object_key={object_key}, etag={metadata['etag']}")
        return metadata

    except ServiceError as e:
        logger.error(f"获取OSS对象元数据失败 (ServiceError): object_key={object_key}, error: {e}")
        return {}
    except Exception as e:
        logger.error(f"获取OSS对象元数据失败 (Exception): error: {e}")
        return {}


def oss_list_subdirectories(prefix: str) -> list[str]:
    """
    列出 OSS 指定前缀下的所有子目录（返回子目录名称列表）

    Args:
        prefix: OSS 前缀路径，必须以 '/' 结尾，例如 'DemoAssets/clausemind-demo/vectorstores/policy123/'

    Returns:
        list[str]: 子目录名称列表（不包含完整路径）
    """
    if not prefix.endswith('/'):
        prefix += '/'

    subdirs = set()
    marker = None

    while True:
        req = oss.ListObjectsRequest(
            bucket=OSS_BUCKET_NAME,
            prefix=prefix,
            delimiter='/',  # 使用分隔符获取目录结构
            marker=marker,
            max_keys=1000
        )
        resp = client.list_objects(req)

        # 处理 CommonPrefixes（子目录）
        for common_prefix in resp.common_prefixes or []:
            # 从完整路径中提取子目录名称
            full_path = common_prefix.prefix
            if full_path.startswith(prefix) and full_path != prefix:
                # 去掉前缀，获取子目录名称
                relative_path = full_path[len(prefix):].rstrip('/')
                if '/' not in relative_path:  # 只取第一层子目录
                    subdirs.add(relative_path)

        # 处理下一页
        if not resp.is_truncated:
            break
        marker = resp.next_marker

    return sorted(list(subdirs))

# 使用示例
if __name__ == '__main__':
    # 本地文件名
    local_file = 'local_file.md'
    # OSS上的目标路径和文件名
    oss_object_name = 'md.md'
    # 下载后保存的本地文件名
    downloaded_file = 'downloaded_from_oss.md'

    # 1. 创建一个本地测试文件
    with open(local_file, 'w') as f:
        f.write('this is a test file for a specific directory.')

    # 2. 上传文件到指定目录
    logger.info("--- 开始上传 ---")
    oss_upload_original_md_file(oss_object_name, local_file)

    # 3. 从指定目录下载文件
    logger.info("\n--- 开始下载 ---")
    oss_download_original_md_file(oss_object_name, downloaded_file)

    # 4. 列举文件示例
    logger.info("\n--- 列举文件 ---")
    files = oss_list_files_by_substring('test')
    logger.info(f"找到的文件：{files}")

    # 5. 检查对象是否存在
    logger.info("\n--- 检查对象是否存在 ---")
    exists = oss_parsed_markdown_exists("12345.md")
    logger.info(f"对象 '{oss_object_name}' 存在: {exists}")
