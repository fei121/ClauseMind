"""
Date: 2025-10-13 10:11:53
LastEditTime: 2026-02-26 17:28:16
Description: 对minerU进行二次开发，增强表格处理。
新增表格合并逻辑扩展，当MinerU识别并合并多个表格块时，
自动将属于同一合并表格的多个原始表格块图片作为多图输入传递给Qwen3-VL-235b-a22b-instruct
已迁移到LangFuse Hub管理prompts
"""

import json
import re
import zipfile
import os
import alibabacloud_oss_v2 as oss  # 用于上传 + 生成预签名URL
from alibabacloud_oss_v2.credentials import StaticCredentialsProvider
from utils import logger
import traceback
import time
from typing import Any, Optional, Dict, List, Tuple
import copy
from config import OSS_REGION, OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_BUCKET_NAME, OSS_BASE_PREFIX
from repositories.langfuse_integration import (
    convert_merged_tables_with_langfuse as convert_merged_tables_with_hub,
    convert_single_table_with_langfuse as convert_single_table_with_hub
)
from workflows.document_processing.VLM_markdown_post_processing.pipeline_middle_json_mkcontent import union_make
from mineru.utils.enum_class import MakeMode
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional

# 默认要丢弃的表格关键字
DEFAULT_DISCARD_KEYWORDS = [
    "ICF结构代码", "人身保险伤残评定标准及代码", "伤残评定标准及代码",
    "伤残代码", "伤残条目", "职业代码", "森林木材业", "地质矿产",
    "运输业", "工程施工", "建筑材料", "电子业", "机电产品制造与装配",
    "化学工业", "纺织", "文化体育用品制作", "卫生专业技术", "服务业",
    "商业", "体育"
]

# 默认要职业代码数字范围 (2201001-4101032)
DEFAULT_DISCARD_NUMBER_RANGE = (2201001, 4101032)

# 1. 预编译【数字范围】提取正则 (7位纯数字)
NUMBER_EXTRACT_PATTERN = re.compile(r'(?<!\d)(\d{7})(?!\d)')

# 2. 预编译【身份证】提取正则 (仅支持标准 18 位)
# (?<![\dXx]) : 前面不能是数字或字母 X/x
# [1-9]\d{5}  : 6位地区码（第一位非0）
# (?:18|19|20)\d{2} : 4位年份(18xx, 19xx, 20xx)
# (?:0[1-9]|1[0-2]) : 2位月份(01-12)
# (?:0[1-9]|[12]\d|3[01]) : 2位日期(01-31)
# \d{3}[\dXx] : 3位顺序码 + 1位校验码(数字或X)
# (?![\dXx])  : 后面不能是数字或字母 X/x
IDCARD_PATTERN = re.compile(
    r'(?<![\dXx])'
    r'([1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx])'
    r'(?![\dXx])',
    re.IGNORECASE
)


def _is_number_in_discard_range(num_str: str) -> bool:
    """检查数字字符串是否在丢弃范围内"""
    num = int(num_str)
    return DEFAULT_DISCARD_NUMBER_RANGE[0] <= num <= DEFAULT_DISCARD_NUMBER_RANGE[1]


def _should_discard_by_content(content: str, keywords: List[str] = DEFAULT_DISCARD_KEYWORDS) -> Optional[str]:
    """
    检查内容是否应该被丢弃。
    返回匹配到的 关键字/数字/身份证号，如果不丢弃则返回 None。
    """
    if not isinstance(content, str) or not content:
        return None

    # 第一步：检查普通关键字 (字符串匹配最快，优先拦截)
    for kw in keywords:
        if kw in content:
            return kw

    # 第二步：检查是否包含身份证号 (18位隐私数据拦截)
    idcard_match = IDCARD_PATTERN.search(content)
    if idcard_match:
        return idcard_match.group(1)

    # 第三步：检查业务数字范围 (7位数字)
    for match in NUMBER_EXTRACT_PATTERN.finditer(content):
        num_str = match.group(1)
        if _is_number_in_discard_range(num_str):
            return num_str

    return None

def _log_exception(ctx: str, exc: BaseException, extra: Optional[dict] = None):
    try:
        extra_str = f" | extra={json.dumps(extra, ensure_ascii=False)}" if extra else ""
    except Exception:
        extra_str = f" | extra={extra}"
    logger.error(f"[EXCEPTION] {ctx}: {repr(exc)}{extra_str}\n{traceback.format_exc()}")


@dataclass
class TableGroup:
    """表格组数据结构"""
    group_id: str
    page_idx: int
    para_block_idx: int
    table_parts: List[Tuple[Any, Optional[bytes], str, int, int]]  # (span, image_data, image_filename, page_idx, block_idx)
    is_merged: bool
    bbox_coords: Optional[Tuple[float, float, float, float]]


# 初始化 alibabacloud_oss_v2 客户端
credentials_provider = StaticCredentialsProvider(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
cfg = oss.config.load_default()
cfg.credentials_provider = credentials_provider
cfg.region = OSS_REGION
oss_client = oss.Client(cfg)


def upload_image_to_oss(image_data, filename, expiration=3600):
    """
    上传图片数据到OSS，返回预签名URL。
    :param expiration: 预签名URL过期时间（秒），默认1小时。
    """
    full_object_name = f'{OSS_BASE_PREFIX}/images/{filename}'
    try:
        if not image_data:
            logger.warning(f"[OSS] Empty image data for {filename}")
            return None
        # 使用 alibabacloud_oss_v2 上传
        oss_client.put_object(oss.PutObjectRequest(
            bucket=OSS_BUCKET_NAME,
            key=full_object_name,
            body=image_data
        ))
        logger.info(f"[OSS] Uploaded {full_object_name} ({len(image_data) if image_data else 0} bytes)")
        # 使用同一个 client 实例生成预签名 URL
        # Note: Using getattr with both 'url' and 'URL' to handle potential API variations (same pattern as badcase/oss_IO.py)
        presign_result = oss_client.presign(
            oss.GetObjectRequest(bucket=OSS_BUCKET_NAME, key=full_object_name),
            expires=timedelta(seconds=expiration)
        )
        presigned_url = getattr(presign_result, 'url', None) or getattr(presign_result, 'URL', None)
        if not presigned_url:
            logger.warning(f"[OSS] presign returned empty for {full_object_name}")
        else:
            logger.info(f"[OSS] Presigned URL generated for {filename}")
        return presigned_url
    except Exception as e:
        _log_exception("upload_image_to_oss", e, {"filename": filename, "object": full_object_name, "expiration": expiration})
        return None


def analyze_table_relationships(pages: List[Dict]) -> Dict[str, TableGroup]:
    """
    分析表格之间的合并关系，识别被合并的表格组

    基于 MinerU 的 merge_table 逻辑实现：
    - MinerU 在合并时会将“下一页的第一个表格块”合并到“上一页的最后一个表格块”
    - 被合并掉的表格块会清空 lines，并标记 lines_deleted=True
    - 我们需要找到每条合并链的起点（上一页的“最后一个未被删除的表格块”），
      并把后续页面中“第一个被删除的表格块”恢复图片并一并收集
    """
    table_groups: Dict[str, TableGroup] = {}
    group_counter = 0

    # 记录已处理过的 (page_idx, block_idx) 避免重复
    processed_blocks: set[Tuple[int, int]] = set()

    # 预先为每一页建立 preproc_map，加速从 preproc_blocks 恢复原始内容
    all_preproc_maps: List[Dict[Any, Dict]] = []
    for page in pages:
        preproc_map: Dict[Any, Dict] = {}
        for preproc_block in page.get('preproc_blocks', []) or []:
            if preproc_block.get('type') == 'table' and 'bbox' in preproc_block:
                try:
                    bbox_key = tuple(preproc_block['bbox'])
                    preproc_map[bbox_key] = preproc_block
                except Exception:
                    # 忽略异常的 bbox
                    continue
        all_preproc_maps.append(preproc_map)

    # 页面级遍历，按“每页的最后一个表格”为合并链起点候选
    for p_idx, page in enumerate(pages):
        para_blocks = page.get('para_blocks', []) or []
        if not para_blocks:
            continue

        # 找到本页所有 table 的索引
        table_indices = [i for i, pb in enumerate(para_blocks) if pb.get('type') == 'table']
        if not table_indices:
            continue

        last_table_idx = table_indices[-1]

        # 1) 本页除了“最后一个表格”之外的其他表格，均视为独立表格（不参与跨页合并）
        for b_idx in table_indices[:-1]:
            if (p_idx, b_idx) in processed_blocks:
                continue
            para_block = para_blocks[b_idx]

            # 如果该表格本身被标记为删除（或被恢复的删除），说明它属于前一页的合并链，跳过
            is_deleted_self = False
            for block in para_block.get('blocks', []) or []:
                if block.get('type') == 'table_body' and (block.get('lines_deleted', False) or block.get('_was_lines_deleted', False)):
                    is_deleted_self = True
                    break
            if is_deleted_self:
                continue

            # 收集该独立表格的图片
            start_parts = extract_table_images(
                para_block,
                all_preproc_maps[p_idx],
                p_idx,
                b_idx,
                include_deleted=False
            )
            if start_parts:
                group_id = f"group_{group_counter}"
                table_groups[group_id] = TableGroup(
                    group_id=group_id,
                    page_idx=p_idx,
                    para_block_idx=b_idx,
                    table_parts=start_parts,
                    is_merged=False,
                    bbox_coords=para_block.get('bbox')
                )
                group_counter += 1
                processed_blocks.add((p_idx, b_idx))

        # 2) 处理“最后一个表格”作为潜在的合并链起点
        b_idx = last_table_idx
        if (p_idx, b_idx) in processed_blocks:
            continue
        para_block = para_blocks[b_idx]

        # 如果最后一个表格块自身是删除态，则它被并入了上一页，当前页不作为起点
        is_deleted_self = False
        for block in para_block.get('blocks', []) or []:
            if block.get('type') == 'table_body' and (block.get('lines_deleted', False) or block.get('_was_lines_deleted', False)):
                is_deleted_self = True
                break
        if is_deleted_self:
            continue

        # 作为起点，收集本块图片
        table_parts: List[Tuple[Any, Optional[bytes], str, int, int]] = []
        start_parts = extract_table_images(
            para_block,
            all_preproc_maps[p_idx],
            p_idx,
            b_idx,
            include_deleted=False
        )
        table_parts.extend(start_parts)
        processed_blocks.add((p_idx, b_idx))

        # 按照 MinerU 的逻辑，向“后续页面”查找被合并到它的表格块：
        # 逐页检查“下一页的第一个 para_block 是否是 table 且其 table_body 被标记删除”
        next_page_idx = p_idx + 1
        while next_page_idx < len(pages):
            next_para_blocks = pages[next_page_idx].get('para_blocks', []) or []
            if not next_para_blocks:
                break

            first_block = next_para_blocks[0]
            if first_block.get('type') != 'table':
                break

            # 判断该 block 是否为“被删除（合并到上一页）”的表格
            next_has_deleted = False
            for sub in first_block.get('blocks', []) or []:
                if sub.get('type') == 'table_body' and (sub.get('lines_deleted', False) or sub.get('_was_lines_deleted', False)):
                    next_has_deleted = True
                    break

            if not next_has_deleted:
                break

            # 该表格被合并至起点，从 preproc 恢复其原始图片信息
            deleted_parts = extract_table_images(
                first_block,
                all_preproc_maps[next_page_idx],
                next_page_idx,
                0,
                include_deleted=True
            )
            table_parts.extend(deleted_parts)
            processed_blocks.add((next_page_idx, 0))

            # 继续看下一页是否还有延续的合并块
            next_page_idx += 1

        # 创建表格组
        if table_parts:
            group_id = f"group_{group_counter}"
            table_group = TableGroup(
                group_id=group_id,
                page_idx=p_idx,
                para_block_idx=b_idx,
                table_parts=table_parts,
                is_merged=(len(table_parts) > 1),
                bbox_coords=para_block.get('bbox')
            )
            table_groups[group_id] = table_group
            group_counter += 1

    return table_groups


def extract_table_images(para_block: Dict, preproc_map: Dict[Any, Dict],
                         page_idx: int, block_idx: int, include_deleted: bool = False) -> List[Tuple[Any, Optional[bytes], str, int, int]]:
    """
    从表格 block 中提取图片信息

    Args:
        para_block: 来自 para_blocks 的表格块
        preproc_map: 该页的 preproc_blocks 映射（bbox -> block）
        page_idx: 页面索引
        block_idx: 当前块索引
        include_deleted: 是否包含 lines_deleted 的内容（需要从 preproc 恢复）

    Returns:
        List[Tuple[span, image_data(bytes or None), image_filename(str), page_idx, block_idx]]
    """
    parts: List[Tuple[Any, Optional[bytes], str, int, int]] = []

    for block in para_block.get('blocks', []) or []:
        if block.get('type') != 'table_body':
            continue

        is_deleted = block.get('lines_deleted', False) or block.get('_was_lines_deleted', False)

        if is_deleted and include_deleted:
            # 从 preproc_blocks 恢复原始内容
            bbox = block.get('bbox')
            try:
                bbox_key = tuple(bbox) if bbox is not None else None
            except Exception:
                bbox_key = None

            if bbox_key and bbox_key in preproc_map:
                original_block = preproc_map[bbox_key]
                for orig_sub in original_block.get('blocks', []) or []:
                    if orig_sub.get('type') == 'table_body':
                        for line in orig_sub.get('lines', []) or []:
                            for span in line.get('spans', []) or []:
                                if span.get('type') == 'table' and span.get('image_path'):
                                    parts.append((span, None, span['image_path'], page_idx, block_idx))
        elif (not is_deleted) and (not include_deleted):
            # 直接从当前块读取图片
            for line in block.get('lines', []) or []:
                for span in line.get('spans', []) or []:
                    if span.get('type') == 'table' and span.get('image_path'):
                        parts.append((span, None, span['image_path'], page_idx, block_idx))

    return parts


def collect_table_images_from_zip(z: zipfile.ZipFile, base_dir: str, table_groups: Dict[str, TableGroup]) -> Dict[str, TableGroup]:
    """
    从ZIP文件中收集表格图片数据

    Args:
        z: ZIP文件对象
        base_dir: 基础目录
        table_groups: 表格组字典

    Returns:
        Dict[str, TableGroup]: 更新后的表格组字典，包含图片数据
    """
    for group_id, table_group in table_groups.items():
        updated_parts = []
        for span, _, image_filename, page_idx, block_idx in table_group.table_parts:
            image_path_in_zip = os.path.join(base_dir, "images", image_filename)
            if image_path_in_zip in z.namelist():
                try:
                    with z.open(image_path_in_zip) as img_f:
                        image_data = img_f.read()
                    updated_parts.append((span, image_data, image_filename, page_idx, block_idx))
                except Exception as e:
                    _log_exception("collect_table_image", e, {
                        "group_id": group_id,
                        "image_path": image_path_in_zip
                    })
            else:
                logger.warning(f"[ZIP] Image not found in zip: {image_path_in_zip}")

        table_group.table_parts = updated_parts

    return table_groups


def convert_merged_tables_with_qwen_vl(
    image_urls: List[str],
    prompt_text: Optional[str] = None,
    max_retries: int = 3,
    retry_backoff: float = 1.5,
    debug: bool = False,
) -> str:
    """
    使用 Qwen-VL 模型合并多个表格图像为单个 HTML 表格
    已迁移到 LangFuse Hub 管理 prompts

    Args:
        image_urls: 图片URL列表
        prompt_text: 自定义提示词，如果为None则使用LangFuse Hub管理的默认合并提示词
        max_retries: 最大重试次数
        retry_backoff: 重试退避时间
        debug: 是否开启调试模式

    Returns:
        str: 合并后的HTML表格内容
    """
    if not image_urls:
        return ""

    last_exc = None
    for attempt in range(1, max_retries + 2):
        try:
            if debug:
                logger.info(f"[LLM-MERGE] Calling LangFuse Hub attempt={attempt} images={len(image_urls)}")

            # 使用 LangFuse Hub 管理的函数
            result = convert_merged_tables_with_hub(image_urls, prompt_text)

            if result:
                return result

        except Exception as e:
            last_exc = e
            _log_exception("convert_merged_tables_with_qwen_vl", e, {
                "attempt": attempt,
                "num_images": len(image_urls)
            })

        if attempt <= max_retries:
            sleep_s = retry_backoff ** (attempt - 1)
            if debug:
                logger.info(f"[LLM-MERGE] Retry attempt={attempt+1} after {sleep_s:.2f}s")
            time.sleep(sleep_s)

    logger.error(f"[LLM-MERGE] Exhausted retries. Last error: {repr(last_exc)}")
    return ""


def convert_single_table_with_qwen_vl(
    image_url: str,
    prompt_text: Optional[str] = None,
    max_retries: int = 3,
    retry_backoff: float = 1.5,
    debug: bool = False,
) -> str:
    """
    使用qwen3-vl-235b-a22b-instruct模型转换单个表格图像为文本
    已迁移到 LangFuse Hub 管理 prompts
    """
    if not image_url or not isinstance(image_url, str):
        logger.error(f"[LLM] Invalid image_url: {image_url}")
        return ''

    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_retries + 2):
        try:
            if debug:
                logger.info(f"[LLM] Calling LangFuse Hub attempt={attempt} thread={threading.current_thread().name} url={image_url}")

            # 使用 LangFuse Hub 管理的函数
            result = convert_single_table_with_hub(image_url, prompt_text)

            if result:
                if debug:
                    logger.info(f"大模型输出文本长度={len(result)} 前200个字符={result[:200]}")
                return result

        except Exception as e:
            last_exc = e
            _log_exception("convert_single_table_with_qwen_vl: call_chatgpt_api", e, {
                "attempt": attempt,
                "image_url": image_url[:200],
                "prompt_text_head": (prompt_text[:120] if prompt_text else "default")
            })

        if attempt <= max_retries:
            sleep_s = retry_backoff ** (attempt - 1)
            if debug:
                logger.info(f"[LLM] Retry attempt={attempt+1} after {sleep_s:.2f}s")
            time.sleep(sleep_s)

    if last_exc:
        logger.error(f"[LLM] Exhausted retries. Last error: {repr(last_exc)}")
    return ''


def restore_deleted_blocks(middle_json):
    """
    将para_blocks中标记为lines_deleted的blocks替换为preproc_blocks中的原始对象，
    同时保留一个“历史删除”标记用于后续合并链识别。
    我们使用 `_was_lines_deleted` = True 来记录该块原先被 MinerU 标记合并删除。
    """
    for page_info in middle_json['pdf_info']:
        preproc_blocks = page_info.get('preproc_blocks', [])
        para_blocks = page_info.get('para_blocks', [])

        # 为每个preproc_block建立索引,用于快速查找
        # 使用bbox作为key来匹配对应的block
        preproc_map = {}
        for preproc_block in preproc_blocks:
            if 'bbox' in preproc_block:
                try:
                    bbox_key = tuple(preproc_block['bbox'])
                except Exception:
                    continue
                preproc_map[bbox_key] = preproc_block

        # 遍历para_blocks,查找需要替换的blocks
        for p_idx, para_block in enumerate(para_blocks):
            # 检查一级块(如table, image)
            if 'blocks' in para_block:
                for i, sub_block in enumerate(list(para_block['blocks'])):
                    # 如果sub_block被标记为lines_deleted
                    if sub_block.get('lines_deleted', False):
                        bbox = sub_block.get('bbox')
                        try:
                            bbox_key = tuple(bbox) if bbox is not None else None
                        except Exception:
                            bbox_key = None
                        # 从preproc_blocks中查找对应的原始block
                        if bbox_key and bbox_key in preproc_map:
                            # 深拷贝原始block以避免引用问题
                            original_block = copy.deepcopy(preproc_map[bbox_key])
                            # 查找对应的sub_block类型
                            if 'blocks' in original_block:
                                for orig_sub in original_block['blocks']:
                                    if orig_sub.get('type') == sub_block.get('type'):
                                        # 替换为原始的sub_block，并保留历史删除标记
                                        orig_sub['_was_lines_deleted'] = True
                                        # 也保留当前层的 bbox 信息（以便匹配回溯）
                                        if 'bbox' in sub_block:
                                            orig_sub['bbox'] = sub_block['bbox']
                                        para_block['blocks'][i] = orig_sub
                                        break

            # 如果一级 para_block 自身被标记为删除
            if para_block.get('lines_deleted', False):
                bbox2 = para_block.get('bbox')
                try:
                    bbox_key2 = tuple(bbox2) if bbox2 is not None else None
                except Exception:
                    bbox_key2 = None
                if bbox_key2 and bbox_key2 in preproc_map:
                    # 用原始整体块替换，并保留历史删除标记
                    replacement = copy.deepcopy(preproc_map[bbox_key2])
                    replacement['_was_lines_deleted'] = True
                    # 保持原 bbox 以供后续比对
                    if 'bbox' in para_block:
                        replacement['bbox'] = para_block['bbox']
                    para_blocks[p_idx] = replacement

    return middle_json


def suppress_restored_deleted_table_blocks(pages: List[Dict]):
    """
    在渲染为Markdown前，抑制那些曾经被 MinerU 删除、被我们恢复用于识别的表格块，避免重复输出
    同时也过滤掉被关键字标记为 discard 的表格块
    """
    for page in pages:
        para_blocks = page.get('para_blocks', []) or []
        # 需要收集索引后再删除，避免迭代时修改列表
        indices_to_remove = []

        for idx, para_block in enumerate(para_blocks):
            # 1. 过滤被关键字标记为 discard 的表格块
            if para_block.get('type') == 'discarded_table':
                indices_to_remove.append(idx)
                continue

            # 2. 处理 MinerU 合并删除的表格块
            if para_block.get('type') == 'table':
                # 对于表格块，检查是否有被标记为历史删除的子块
                has_restored_deleted_subblock = False
                for sub_block in para_block.get('blocks', []) or []:
                    if sub_block.get('_was_lines_deleted', False):
                        has_restored_deleted_subblock = True
                        break

                if has_restored_deleted_subblock:
                    # 如果表格块内部有被恢复的删除子块，则清空该表格块的 lines
                    # 这样在后续的 Markdown 渲染中就不会重复输出这些内容
                    para_block['blocks'] = [b for b in para_block.get('blocks', []) or [] if not b.get('_was_lines_deleted', False)]

        # 从后往前删除，避免索引错位
        for idx in reversed(indices_to_remove):
            para_blocks.pop(idx)

    return pages


def filter_and_discard_tables(table_groups: Dict[str, TableGroup], pages: List[Dict], keywords: List[str]) -> Dict[
    str, TableGroup]:
    """
    根据关键字过滤表格。

    [修正点] 根据提供的 layout.json 样本，表格的文本内容存在于 span['html'] 字段中，
    而非 content 或 text。因此需要合并检查 html, content, text 三个字段。
    """
    if not keywords:
        return table_groups

    kept_groups = {}
    discarded_count = 0

    for group_id, group in table_groups.items():
        # 提取该表格组中所有部分的文本内容
        group_text_content = ""

        # 遍历组成该表格的所有切片（防止跨页表格关键字只出现在第二页的情况）
        for part in group.table_parts:
            # part[0] 是 span 对象
            span = part[0]

            # [关键修改] 获取所有可能的文本来源
            # 1. html: MinerU 生成的表格结构通常在这里，包含具体的单元格文字
            # 2. content/text: 某些版本的 MinerU 或纯文本块可能在这里
            raw_html = span.get('html', '')
            raw_content = span.get('content', '')
            raw_text = span.get('text', '')

            # 将它们拼接到一起进行检查
            # 强转 str 避免 None 报错
            group_text_content += f"{str(raw_html)} {str(raw_content)} {str(raw_text)}"

        # 检查关键字（包括普通关键字和数字范围）
        hit_keyword = _should_discard_by_content(group_text_content, keywords)

        if hit_keyword:
            discarded_count += 1
            logger.info(f"[FILTER] Discarding table {group_id} due to keyword: '{hit_keyword}'")

            # 从 pages 数据结构中清除该表格，防止 Markdown 生成时残留
            processed_blocks = set()
            for part in group.table_parts:
                p_idx = part[3]
                b_idx = part[4]

                if (p_idx, b_idx) in processed_blocks:
                    continue

                if p_idx < len(pages):
                    para_blocks = pages[p_idx].get('para_blocks', [])
                    if b_idx < len(para_blocks):
                        target_block = para_blocks[b_idx]

                        # [副作用处理] 标记为丢弃，清空内容
                        # union_make 函数通常会忽略 unknown type 或空内容的 block
                        target_block['type'] = 'discarded_table'
                        target_block['lines'] = []
                        target_block['blocks'] = []
                        target_block['_discard_reason'] = f"keyword: {hit_keyword}"

                        processed_blocks.add((p_idx, b_idx))
        else:
            kept_groups[group_id] = group

    if discarded_count > 0:
        logger.info(f"[FILTER] Total discarded: {discarded_count}, Remaining: {len(kept_groups)}")

    return kept_groups

def process_enhanced_layout_json(
    zip_path: str,
    output_md_path: str,
    img_bucket_path: str = 'https://example.com/demo-assets/images',
    max_workers: int = 100,
    debug_mode: bool = False,
    custom_merge_prompt: Optional[str] = None,
    custom_single_prompt: Optional[str] = None,
    discard_keywords: Optional[List[str]] = DEFAULT_DISCARD_KEYWORDS
) -> str:
    """
    增强的表格处理函数，支持表格合并逻辑及关键字过滤

    Args:
        zip_path: ZIP文件路径
        output_md_path: 输出Markdown文件路径
        img_bucket_path: 图片存储桶路径
        max_workers: 最大并发线程数
        debug_mode: 是否开启调试模式
        custom_merge_prompt: 自定义合并表格提示词
        custom_single_prompt: 自定义单个表格提示词
        discard_keywords: 要丢弃的表格关键字列表（可选）

    Returns:
        str: 生成的Markdown内容
    """
    if debug_mode:
        logger.info(f"[RUN] Enhanced process_layout_json zip={zip_path} output={output_md_path} max_workers={max_workers}")

    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            layout_json_path = next((name for name in z.namelist() if name.endswith('layout.json')), None)
            if not layout_json_path:
                raise FileNotFoundError("layout.json not found in zip")

            with z.open(layout_json_path) as f:
                layout_data = json.load(f)

            # 恢复被删除的块
            layout_data = restore_deleted_blocks(layout_data)

            base_dir = os.path.dirname(layout_json_path)
            pages = layout_data.get('pdf_info', [])

            if not isinstance(pages, list):
                logger.error(f"[DATA] 'pdf_info' is not list, type={type(pages)}")
                pages = []

            # 分析表格关系并分组
            table_groups = analyze_table_relationships(pages)
            logger.info(f"[ANALYSIS] Found {len(table_groups)} table groups")

            # 收集表格图片数据
            table_groups = collect_table_images_from_zip(z, base_dir, table_groups)

            # 关键字过滤逻辑
            if discard_keywords:
                table_groups = filter_and_discard_tables(table_groups, pages, discard_keywords)

            # 分离合并表格组和独立表格
            merged_groups = {}
            standalone_tables = {}

            for group_id, table_group in table_groups.items():
                if table_group.is_merged and len(table_group.table_parts) > 1:
                    merged_groups[group_id] = table_group
                elif len(table_group.table_parts) == 1:
                    standalone_tables[group_id] = table_group
                else:
                    logger.warning(f"[ANALYSIS] Table group {group_id} has unexpected configuration: merged={table_group.is_merged}, parts={len(table_group.table_parts)}")

            logger.info(f"[TASK] Processed: {len(merged_groups)} merged groups, {len(standalone_tables)} standalone tables")

            # 处理函数定义
            def process_merged_group(table_group: TableGroup):
                """处理合并表格组"""
                try:
                    # 上传所有图片并收集URL
                    image_urls = []
                    debug_info = {"group_id": table_group.group_id}

                    for span, image_data, image_filename, _, _ in table_group.table_parts:
                        if image_data:
                            public_url = upload_image_to_oss(image_data, image_filename)
                            if public_url:
                                image_urls.append(public_url)
                                debug_info[f"image_{image_filename}"] = public_url

                    if not image_urls:
                        return table_group.group_id, None, RuntimeError("No valid image URLs")

                    # 调用VLM合并表格
                    merged_html = convert_merged_tables_with_qwen_vl(
                        image_urls,
                        custom_merge_prompt,
                        debug=debug_mode
                    )

                    # 更新第一个span的HTML，保持lines_deleted状态用于后续处理
                    if merged_html and table_group.table_parts:
                        first_span = table_group.table_parts[0][0]
                        first_span['html'] = merged_html
                        if debug_mode:
                            first_span['_debug_merged_from'] = [part[2] for part in table_group.table_parts]
                            first_span['_debug_merge_info'] = debug_info

                    return table_group.group_id, merged_html, None

                except Exception as e:
                    _log_exception("process_merged_group", e, {"group_id": table_group.group_id})
                    return table_group.group_id, None, e

            def process_standalone_table(table_group: TableGroup):
                """处理独立表格"""
                try:
                    span, image_data, image_filename, _, _ = table_group.table_parts[0]

                    if not image_data:
                        return table_group.group_id, False, RuntimeError("No image data")

                    public_url = upload_image_to_oss(image_data, image_filename)
                    if public_url:
                        converted_text = convert_single_table_with_qwen_vl(
                            public_url,
                            custom_single_prompt,
                            debug=debug_mode
                        )
                        if converted_text:
                            span['html'] = converted_text
                            if debug_mode:
                                span['_debug_single_table'] = {
                                    "filename": image_filename,
                                    "url": public_url
                                }
                        return table_group.group_id, bool(converted_text), None
                    return table_group.group_id, False, RuntimeError("Upload failed")

                except Exception as e:
                    _log_exception("process_standalone_table", e, {"group_id": table_group.group_id})
                    return table_group.group_id, False, e

            # 并发处理所有表格
            errors = []
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                futures = {}

                # 合并表格任务
                for group_id, table_group in merged_groups.items():
                    future = executor.submit(process_merged_group, table_group)
                    futures[future] = ("merged", group_id)

                # 独立表格任务
                for group_id, table_group in standalone_tables.items():
                    future = executor.submit(process_standalone_table, table_group)
                    futures[future] = ("standalone", group_id)

                # 收集结果
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        task_type, group_id = futures[future]

                        if task_type == "merged":
                            _, merged_html, err = result
                            if err:
                                errors.append((f"merged_group_{group_id}", repr(err)))
                        else:  # standalone
                                _, success, err = result
                                if err:
                                    errors.append((f"standalone_table_{group_id}", repr(err)))

                    except Exception as e:
                        task_info = futures.get(future, ("unknown", "unknown"))
                        _log_exception("future.result", e, {"task": task_info})

            if errors:
                logger.error(f"[TASK] {len(errors)} tasks failed. Samples: {errors[:5]}")

            # 在渲染为Markdown前，抑制那些曾经被 MinerU 删除、被我们恢复用于识别的表格块，避免重复输出
            suppress_restored_deleted_table_blocks(pages)

            # 生成最终Markdown
            try:
                markdown_content = union_make(pages, MakeMode.MM_MD, img_bucket_path)
            except Exception as e:
                _log_exception("union_make", e, {"pages_len": len(pages), "img_bucket_path": img_bucket_path})
                raise

            # 写入文件
            try:
                out_dir = os.path.dirname(os.path.abspath(output_md_path)) or "."
                if out_dir and not os.path.exists(out_dir):
                    os.makedirs(out_dir, exist_ok=True)
                with open(output_md_path, 'w', encoding='utf-8') as f:
                    f.write(markdown_content)
            except Exception as e:
                _log_exception("write_markdown_output", e, {"output_md_path": output_md_path})
                raise

            return markdown_content

    except Exception as e:
        _log_exception("process_enhanced_layout_json(top-level)", e, {"zip_path": zip_path})
        raise


# 示例使用函数
def example_usage():
    """使用示例"""
    output_md_path = "enhanced_output.md"
    zip_path = "examples/mineru_output_example.zip"
    # 自定义提示词（可选）
    custom_merge_prompt = """
    请将以上多个表格图片合并为一个完整的HTML表格：
    1. 保持表格结构和数据的完整性
    2. 确保表头对齐和列宽一致
    3. 处理跨页的行合并
    4. 仅输出HTML表格代码，不包含任何说明文字
    5. 丢弃个人信息表和职业分类清单的图片表格不转换
    """

    custom_single_prompt = """
    请将此表格图片转换为HTML格式，保持原始格式和结构。
    """

    try:
        result = process_enhanced_layout_json(
            zip_path=zip_path,
            output_md_path=output_md_path,
            debug_mode=True,
            custom_merge_prompt=custom_merge_prompt,
            custom_single_prompt=custom_single_prompt,
            max_workers=10,
            discard_keywords=DEFAULT_DISCARD_KEYWORDS  # 使用默认关键字过滤
        )
        print(f"Processing completed. Output length: {len(result)} characters")

    except Exception as e:
        logger.error(f"Processing failed: {e}")


if __name__ == "__main__":
    example_usage()
    # text= """<table><tr><td>被保险人</td><td>性别</td><td>年龄</td><td>证件号码</td><td>保费</td><td>保险责任开始日</td><td>关系</td><td>主被保险人证件号</td><td>职业代码(职业类别)</td></tr><tr><td rowspan="2">于娜保险合同专用章陈洁仁</td><td>女</td><td>32</td><td>321282199207084021</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>321282199207084021</td><td>2502006(三类)</td></tr><tr><td>男</td><td>44</td><td>320502198010082013</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>320502198010082013</td><td>2502006(三类)</td></tr><tr><td>王晔</td><td>女</td><td>44</td><td>320502198012260549</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>320502198012260549</td><td>2502006(三类)</td></tr><tr><td>田融</td><td>男</td><td>38</td><td>32050219870216225X</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>32050219870216225X</td><td>2502006(三类)</td></tr><tr><td>司柳化</td><td>女</td><td>40</td><td>61012419841114396X</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>61012419841114396X</td><td>2502006(三类)</td></tr><tr><td>周琼瑶</td><td>女</td><td>32</td><td>610425199302130702</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>610425199302130702</td><td>2502006(三类)</td></tr><tr><td>李慧雪</td><td>女</td><td>33</td><td>320502199112283021</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>320502199112283021</td><td>2502006(三类)</td></tr><tr><td>顾学良</td><td>男</td><td>42</td><td>321023198209221215</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>321023198209221215</td><td>2502006(三类)</td></tr><tr><td>袁梦怡</td><td>女</td><td>32</td><td>32050319930415102X</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>32050319930415102X</td><td>2502006(三类)</td></tr><tr><td>丁增先</td><td>男</td><td>46</td><td>340122197811187213</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>340122197811187213</td><td>2502006(三类)</td></tr><tr><td>宋莹莹</td><td>女</td><td>41</td><td>320830198402092220</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>320830198402092220</td><td>2502006(三类)</td></tr><tr><td>童琳杰</td><td>女</td><td>28</td><td>511181199702180428</td><td>425.00</td><td>2025-06-01</td><td>本人</td><td>511181199702180428<"""
    # print(_should_discard_by_content(text, DEFAULT_DISCARD_KEYWORDS))
