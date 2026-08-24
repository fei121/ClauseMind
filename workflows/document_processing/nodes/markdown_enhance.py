# python
"""
Date: 2025-08-22 14:46:13
LastEditTime: 2026-02-06 12:53:11
Description: Markdown 解析/目录生成/切块与标题修复 (重构版)
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

from langchain_text_splitters import MarkdownHeaderTextSplitter

from repositories.langfuse_integration import (
    html_table_to_markdown_with_langfuse,
    catalog_generator_with_langfuse
)
from utils import logger
from .markdown_repair import repair_markdown

# --- 常量定义 ---
HEADER_KEYS = ['#', '##', '###', '####', '#####', '######', '#######', '########', '#########', '##########', '###########', '############']
TABLE_PATTERN = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)

# --- 核心工具函数 ---

def replace_all_html_tables(md_text: str, max_workers: int = 100) -> str:
    """并发处理 HTML 表格转 Markdown"""
    if not md_text or "</table>" not in md_text.lower():
        return md_text

    matches = list(TABLE_PATTERN.finditer(md_text))
    if not matches:
        return md_text

    logger.info(f"发现 {len(matches)} 个 HTML 表格，开始并发转换...")

    # 辅助函数：单个转换
    def convert_single(match_obj):
        return html_table_to_markdown(match_obj.group(0))

    replacements = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(convert_single, m): i for i, m in enumerate(matches)}

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                replacements[idx] = future.result()
            except Exception as e:
                logger.error(f"表格转换异常 (idx={idx}): {e}")
                replacements[idx] = matches[idx].group(0)  # 保持原样

    # 逆序替换以避免索引偏移
    result = list(md_text)
    for i in reversed(range(len(matches))):
        m = matches[i]
        replacement = replacements.get(i, m.group(0))
        result[m.start():m.end()] = list(replacement)

    return "".join(result)


def html_table_to_markdown(table_text: str) -> str:
    """调用 LLM 转换 HTML 表格，失败则返回原文本"""
    if not table_text.strip():
        return table_text
    try:
        text = html_table_to_markdown_with_langfuse(
            table_text=table_text,
            session_id=None
        )
        if text and isinstance(text, str) and text.strip():
            return f"\n\n{text.strip()}\n\n"
    except Exception as e:
        logger.warning(f"Langfuse 表格转换失败: {e}")

    return table_text


def add_placeholder_to_empty_sections(markdown_text: str, placeholder: str = "本节无正文内容") -> str:
    """为空标题补充占位符"""
    headers = list(re.finditer(r"^(#+ .*)$", markdown_text, re.MULTILINE))
    if not headers:
        return markdown_text

    parts = [markdown_text[:headers[0].start()]]
    for i, h in enumerate(headers):
        parts.append(h.group(0))
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(markdown_text)
        block = markdown_text[start:end]

        parts.append(f"\n\n{placeholder}\n\n" if not block.strip() else block)

    return "".join(parts)


def fix_empty_headers(markdown_text: str, placeholder: str = "未命名章节") -> str:
    """
    将空标题（如 "# " 或 "## " 只有 # 符号无文字）替换为带默认文本的标题

    Args:
        markdown_text: 原始 markdown 文本
        placeholder: 空标题的默认填充文本

    Returns:
        处理后的 markdown 文本
    """
    lines = markdown_text.splitlines()
    for i, line in enumerate(lines):
        # 匹配标题行：# 开头，后面只有空格没有实际文字
        match = re.match(r'^(#+)\s*$', line)
        if match:
            level = match.group(1)
            lines[i] = f"{level} {placeholder}"
    return "\n".join(lines)


# --- 原有的目录生成函数（保留兼容）---
# --- 核心工具函数：目录修复 (重构精简版) ---

def extract_headers_with_lines(markdown_text: str) -> List[Dict]:
    """提取标题及其所在的行号"""
    headers = []
    # 匹配以 # 开头的行
    for line_idx, line in enumerate(markdown_text.splitlines()):
        match = re.match(r'^(#+)\s+(.+)$', line)
        if match:
            headers.append({
                "line_idx": line_idx,
                "original_text": line,  # 完整行： ## 标题
                "level": match.group(1), # ##
                "content": match.group(2).strip() # 标题
            })
    return headers


def batch_fix_headers(headers: List[Dict], batch_size: int = 200, overlap: int = 20) -> Tuple[List[str], List[str]]:
    """
    分批修复标题，返回修复后的标题内容列表和层级列表
    使用滑动窗口，重叠部分作为上下文衔接上下批次

    Returns:
        fixed_contents: 修复后的标题内容列表（纯文本，不含[索引]）
        fixed_levels: 修复后的标题层级列表（如 '#', '##' 等）
    """
    fixed_contents = [h['content'] for h in headers]
    fixed_levels = [h['level'] for h in headers]
    fixed_indices = set()

    step = batch_size - overlap
    if step <= 0:
        step = batch_size

    total_headers = len(headers)

    for i in range(0, total_headers, step):
        batch_end = min(i + batch_size, total_headers)
        current_batch_indices = list(range(i, batch_end))

        if not current_batch_indices:
            break

        update_start = i if i == 0 else i + overlap
        update_end = batch_end

        # 构建输入
        llm_input_lines = []
        for idx in current_batch_indices:
            llm_input_lines.append(f"{fixed_levels[idx]} {fixed_contents[idx]} [{idx}]")

        input_text = "\n".join(llm_input_lines)

        try:
            result_text = _call_catalog_llm(input_text)

            # 解析结果，匹配格式：## 标题内容 [123]
            for line in result_text.strip().splitlines():
                match = re.match(r'^(#+)\s+(.+?)\s*\[(\d+)\]$', line.strip())
                if match:
                    new_level, clean_content, idx_str = match.groups()
                    parsed_idx = int(idx_str)

                    if update_start <= parsed_idx < update_end and parsed_idx not in fixed_indices:
                        fixed_contents[parsed_idx] = clean_content.strip()
                        fixed_levels[parsed_idx] = new_level
                        fixed_indices.add(parsed_idx)

            logger.info(f"批次 [{i}:{batch_end}] 处理完成，更新范围 [{update_start}:{update_end}]")

        except Exception as e:
            logger.error(f"批次修复失败 (idx范围 {i}-{batch_end}): {e}")

    return fixed_contents, fixed_levels


def fix_markdown_headers(markdown_text: str) -> Tuple[str, str]:
    """
    主修复流程
    Returns:
        fixed_markdown_text: 修复标题后的完整 Markdown 文本
        catalog_content: 修复后的目录文本（包含层级、标题、索引）用于生成 catalog.md
    """
    logger.info(">>> 开始修复 Markdown 标题...")

    headers_info = extract_headers_with_lines(markdown_text)
    if not headers_info:
        return markdown_text, ""

    # 修复 (获取标题内容和层级列表)
    fixed_contents, fixed_levels = batch_fix_headers(headers_info)

    # 应用回填 & 生成目录
    lines = markdown_text.splitlines()
    catalog_lines = []

    for idx, (info, new_content, new_level) in enumerate(zip(headers_info, fixed_contents, fixed_levels)):
        # 更新原始文本行 (使用修复后的层级)
        lines[info['line_idx']] = f"{new_level} {new_content}"
        # 生成目录行 (带索引，用于 catalog.md)
        catalog_lines.append(f"{new_level} {new_content} [{idx}]")

    fixed_text = "\n".join(lines)
    catalog_str = "\n".join(catalog_lines)

    return fixed_text, catalog_str


def build_header_md_text(docs) -> str:
    """从切片中提取原始 Header 信息"""
    lines = []
    for i, d in enumerate(docs):
        md = d.metadata or {}
        for level in HEADER_KEYS:
            if md.get(level):
                lines.append(f"{level} {md[level]}")
                break
    return "\n".join(lines)


def _call_catalog_llm(text_input: str) -> str:
    """封装 LLM 调用"""
    try:
        res = catalog_generator_with_langfuse(text_input)
        return res.strip() if isinstance(res, str) else ""
    except Exception as e:
        logger.error(f"LLM 调用异常: {e}")
        return ""


# 用于清理标题中可能存在的索引标记 [123]，让结构路径更干净
def clean_header_text(text: str) -> str:
    """去除标题末尾的索引标记，如 '第一章 [10]' -> '第一章'"""
    # 移除 [数字] 或 [数字-数字]
    return re.sub(r'\s*\[\d+(?:-\d+)?\]$', '', text).strip()


# 用于解析文件标记 FILE: filename | file_class
FILE_MARKER_PATTERN = re.compile(r'^FILE:\s*([^|]+?)(?:\s*\|\s*(.+))?$')


def parse_file_marker(header_content: str) -> Optional[Dict[str, str]]:
    """
    解析文件标记，提取文件名和文件类型

    Args:
        header_content: 标题内容，格式如 'FILE: filename.md | class1, class2' 或 'FILE: filename.md'

    Returns:
        包含 'file_name' 和 'file_class' 的字典，若不是文件标记则返回 None
    """
    match = FILE_MARKER_PATTERN.match(header_content.strip())
    if match:
        file_name = match.group(1).strip()
        file_class = match.group(2).strip() if match.group(2) else None
        return {
            "file_name": file_name,
            "file_class": file_class
        }
    return None


def extract_file_sections(markdown_text: str) -> List[Dict[str, Any]]:
    """
    提取markdown文本中的文件分区信息

    Args:
        markdown_text: 合并后的markdown文本

    Returns:
        文件分区列表，每个元素包含 start_line, end_line, file_name, file_class
    """
    lines = markdown_text.splitlines()
    sections = []
    current_section = None

    for line_idx, line in enumerate(lines):
        # 匹配一级标题
        match = re.match(r'^#\s+(.+)$', line)
        if match:
            header_content = match.group(1).strip()
            file_info = parse_file_marker(header_content)
            if file_info:
                # 结束上一个分区
                if current_section is not None:
                    current_section['end_line'] = line_idx - 1
                    sections.append(current_section)
                # 开始新分区
                current_section = {
                    'start_line': line_idx,
                    'end_line': None,
                    'file_name': file_info['file_name'],
                    'file_class': file_info['file_class']
                }

    # 处理最后一个分区
    if current_section is not None:
        current_section['end_line'] = len(lines) - 1
        sections.append(current_section)

    return sections


def get_file_info_for_content(content: str, file_sections: List[Dict[str, Any]], lines: List[str]) -> Dict[str, Optional[str]]:
    """
    根据内容在原始文本中的位置，确定其所属的文件来源

    Args:
        content: chunk 的内容
        file_sections: 文件分区列表
        lines: 原始文本的行列表

    Returns:
        包含 file_name 和 file_class 的字典
    """
    if not file_sections or not content.strip():
        return {"file_name": None, "file_class": None}

    # 取内容的第一行作为定位依据
    content_first_line = content.strip().splitlines()[0].strip() if content.strip() else ""

    # 在原始文本中查找这一行
    full_text = "\n".join(lines)
    content_pos = full_text.find(content_first_line)

    if content_pos == -1:
        # 如果找不到，返回最后一个分区的信息（降级处理）
        if file_sections:
            return {
                "file_name": file_sections[-1].get('file_name'),
                "file_class": file_sections[-1].get('file_class')
            }
        return {"file_name": None, "file_class": None}

    # 计算内容所在的行号
    line_idx = full_text[:content_pos].count('\n')

    # 查找该行所属的文件分区
    for section in file_sections:
        if section['start_line'] <= line_idx <= section['end_line']:
            return {
                "file_name": section['file_name'],
                "file_class": section['file_class']
            }

    # 如果没找到对应分区，返回第一个分区（内容可能在第一个FILE标记之前）
    if file_sections:
        return {
            "file_name": file_sections[0].get('file_name'),
            "file_class": file_sections[0].get('file_class')
        }

    return {"file_name": None, "file_class": None}


# --- 主流程 ---


def markdown_parser(total_text: str, policy_no: str, output_dir: Optional[str] = None) -> dict[str, str | list[
    Any]] | None:
    """
    主解析流程：预处理 -> 标题修复 -> 切块 -> 结构化 -> 输出
    """
    out_path = Path(output_dir) if output_dir else Path.cwd()
    out_path.mkdir(parents=True, exist_ok=True)

    logger.info(f">>> 开始处理文档 policy_no: {policy_no}")

    # 1. 预处理
    try:
        # 1.1 先转换 HTML 表格（避免表格内容被 repair_markdown 误处理）
        text_step1 = replace_all_html_tables(total_text)
        # 1.2 再执行 repair_markdown（此时表格已经是 Markdown 格式）
        text_step2 = repair_markdown(text_step1)
        # 1.3 修复空标题
        text_step3 = fix_empty_headers(text_step2)
        preprocessed_text = add_placeholder_to_empty_sections(text_step3)
    except Exception as e:
        logger.error(f"预处理阶段错误: {e}, 降级使用原始文本")
        preprocessed_text = total_text

    # 2. 修复标题并在预处理后重新提取文件分区信息
    logger.info(">>> 开始在原始 Markdown 上修复标题...")
    try:
        fixed_text, catalog_content = fix_markdown_headers(preprocessed_text)
        logger.info("标题修复完成")

        # 2.1 预处理后重新提取文件分区信息（使用修复后的文本）
        file_sections = extract_file_sections(fixed_text)
        original_lines = fixed_text.splitlines()
        logger.info(f"识别到 {len(file_sections)} 个文件分区")
    except Exception as e:
        logger.error(f"标题修复失败: {e}，继续使用预处理后的文本")
        fixed_text = preprocessed_text
        # 降级：如果修复失败，尝试提取原始目录
        catalog_content = "\n".join([f"{h['level']} {h['content']} [{i}]"
                                     for i, h in enumerate(extract_headers_with_lines(preprocessed_text))])
        # 降级：在预处理文本上提取文件分区
        file_sections = extract_file_sections(preprocessed_text)
        original_lines = preprocessed_text.splitlines()

    # 3. 切块
    # LangChain 的 split_text 会自动将各级标题放入 metadata，例如 {'#': '一级标题', '##': '二级标题'}
    # 注意：fixed_text 中的标题是纯净的（不含 [index]），split_text 得到的 metadata 也是纯净的
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[(h, h) for h in HEADER_KEYS],
        strip_headers=False
    )
    docs_initial = splitter.split_text(fixed_text)

    if not docs_initial:
        logger.error("文档切块为空，流程终止。")
        return None

    # 4. 保存目录文件 (使用 fix_markdown_headers 返回的包含索引的准确目录)
    catalog_path = out_path / f"{policy_no}_catalog.md"
    catalog_path.write_text(catalog_content, encoding="utf-8")

    # 5. 结构化处理 & 生成 Chunks
    chunk_records = []
    rewritten_contents = []

    for idx, doc in enumerate(docs_initial):
        # --- 核心简化逻辑开始 ---
        # 此时 doc.metadata 类似于: {'#': '总则 [0]', '##': '定义 [5]'}
        # 我们按照 HEADER_KEYS 的顺序 ('#', '##'...) 依次提取值，即可还原路径

        path_parts = []
        # 按层级顺序提取，由于 splitter 保留了层级结构在 metadata
        for level_key in HEADER_KEYS:
            if level_key in doc.metadata:
                header_content = doc.metadata[level_key]
                # 双重保险：虽然 fixed_text 应该是干净的，但再次 clean 防止残留
                path_parts.append(clean_header_text(header_content))

        structure_path = "/".join(path_parts)
        structure_level = len(path_parts)
        # --- 核心简化逻辑结束 ---

        # 获取文件来源信息
        file_info = get_file_info_for_content(doc.page_content, file_sections, original_lines)

        metadata = doc.metadata.copy()
        metadata.update({
            "structure_path": structure_path,
            "structure_level": structure_level,
            "original_index": idx, # 记录切片顺序
            "file_name": file_info.get("file_name"),  # 文件名
            "file_class": file_info.get("file_class")  # 文件类型
        })

        chunk_records.append({
            "index": idx,
            "page_content": doc.page_content,
            "metadata": metadata
        })
        rewritten_contents.append(doc.page_content)

    # 6. 保存最终结果
    parsed_md_text = "\n\n".join(rewritten_contents)
    parsed_md_path = out_path / f"{policy_no}_parsed.md"
    parsed_md_path.write_text(parsed_md_text, encoding="utf-8")

    chunks_json_path = out_path / f"{policy_no}_chunks.json"
    with open(chunks_json_path, 'w', encoding='utf-8') as f:
        json.dump(chunk_records, f, ensure_ascii=False, indent=2)

    logger.info(f"<<< 文档处理完成: {policy_no}, 生成 Chunks: {len(chunk_records)}")

    return {
        "catalog_md_path": str(catalog_path),
        "parsed_md_path": str(parsed_md_path),
        "markdown_chunks_with_idx_json_path": str(chunks_json_path),
        "chunks": chunk_records
    }
