"""
Date: 2025-09-24 16:50:20
LastEditTime: 2026-01-20 16:36:14
Description: 这是默认设置,可以在设置》工具》File Description中进行配置
"""
import io
import os
import tempfile
import threading
import time
import zipfile

# 导入HTTP Session管理
from infrastructure.http_session import get_session
from infrastructure.redis_client import get_redis_client
from utils import logger
from workflows.document_processing.VLM_markdown_post_processing.generate_markdown_from_layout_json import \
    process_enhanced_layout_json

# 从环境变量读取 MinerU API tokens
token1_wz_wx = os.getenv("MINERU_TOKEN_1")
token2_wz_gh = os.getenv("MINERU_TOKEN_2")
# 过滤掉 None 和空字符串的 token
TOKENS = [t for t in [token2_wz_gh, token1_wz_wx] if t and t.strip()]


class _MemoryTokenStore:
    """Redis 不可用时，仅用于本进程内的 MinerU token 轮转。"""

    _values = {}
    _lock = threading.Lock()

    def get(self, key):
        with self._lock:
            return self._values.get(key)

    def set(self, key, value):
        with self._lock:
            self._values[key] = value
        return True


class MineruPDFParserAPI:
    API_URL = "https://mineru.net/api/v4/extract/task"

    def __init__(self):
        self.tokens = TOKENS
        if not self.tokens:
            logger.error("No valid Mineru API tokens found in environment variables (MINERU_TOKEN_1, MINERU_TOKEN_2). API calls will fail.")
        else:
            logger.info(f"Initialized MineruPDFParserAPI with {len(self.tokens)} token(s)")

        self.api_url = self.API_URL

        # 生产环境默认使用共享 Redis；本地体验可关闭，自动改用进程内轮转。
        use_redis = os.getenv("MINERU_USE_REDIS", "true").lower() in {"1", "true", "yes", "on"}
        if use_redis:
            try:
                self.redis_client = get_redis_client()
            except Exception as exc:
                logger.warning(f"Redis unavailable for MinerU token rotation, using memory fallback: {exc}")
                self.redis_client = _MemoryTokenStore()
        else:
            logger.info("MINERU_USE_REDIS is disabled; using in-memory token rotation")
            self.redis_client = _MemoryTokenStore()
        # 新增：记录“上次使用的 token 索引”
        self.redis_last_token_key = "mineru_api:last_token_idx"

    def _get_token(self):
        """读取上次使用索引，按序选择下一个；只有1个token时固定返回0。"""
        n = len(self.tokens)
        if n == 0:
            logger.error("No tokens available for API calls")
            return None, None

        last_idx_raw = self.redis_client.get(self.redis_last_token_key)
        try:
            last_idx = int(last_idx_raw) if last_idx_raw is not None else None
        except Exception:
            last_idx = None

        # 越界/非法记录视为无记录
        if last_idx is not None and (last_idx < 0 or last_idx >= n):
            last_idx = None

        if n == 1:
            next_idx = 0
        elif last_idx is None:
            next_idx = 0
        else:
            next_idx = (last_idx + 1) % n  # 排除刚用过的那个，选择下一个（环形）

        token = self.tokens[next_idx]
        logger.debug(f"Selected token index {next_idx} (last_idx: {last_idx})")
        return next_idx, token

    def _submit_task(self, pdf_external_url: str, token: str) -> str:
        header = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        data = {
            "url": pdf_external_url,
            "language": "ch",
            "backend": "pipeline",
        }
        session = get_session()
        res = session.post(self.api_url, headers=header, json=data)
        res.raise_for_status()
        task_id = res.json()["data"]["task_id"]
        logger.info(f"Submitted task {task_id} for url: {pdf_external_url}")
        return task_id

    def _poll_status(self, task_id: str, token: str, token_index: int):
        """状态轮询（pending状态每2分钟，running状态每20秒）"""
        status_url = f"{self.api_url}/{task_id}"
        header = {"Authorization": f"Bearer {token}"}
        session = get_session()
        while True:
            res = session.get(status_url, headers=header)
            res.raise_for_status()
            data = res.json()["data"]
            state = data.get("state")

            logger.info(f"Task {task_id} state: {state}")

            if state == "done":
                # 不再统计页数用量
                return data["full_zip_url"]
            elif state in ["converting", "running"]:
                time.sleep(10)
            elif state == "pending":
                time.sleep(10)
            elif state in ["failed"]:
                raise RuntimeError(f"Task {task_id} failed with message: {data.get('err_msg')}")
            else:
                time.sleep(60)  # Fallback for unknown states

    def _download_and_extract_md(self, zip_url: str) -> str:
        logger.info(f"Downloading result from {zip_url}")
        session = get_session()
        res = session.get(zip_url)
        res.raise_for_status()
        zip_bytes = res.content

        # 1) 尝试使用 layout.json 生成增强版 Markdown（作为最终结果）
        tmp_zip_path = tmp_md_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_zip:
                tmp_zip.write(zip_bytes)
                tmp_zip_path = tmp_zip.name
            with tempfile.NamedTemporaryFile(delete=False, suffix=".md") as tmp_md:
                tmp_md_path = tmp_md.name

            logger.info("Processing layout.json to generate enhanced Markdown...")
            enhanced_md = process_enhanced_layout_json(tmp_zip_path, tmp_md_path)
            # 若函数返回空字符串/None，则视为失败，走回退
            if enhanced_md and isinstance(enhanced_md, str) and enhanced_md.strip():
                logger.info(f"Enhanced Markdown generated via layout.json, length: {len(enhanced_md)}")
                return enhanced_md
            else:
                logger.warning("Enhanced Markdown is empty, falling back to original full.md in zip.")
        except Exception as e:
            logger.warning(f"Failed to process layout.json, falling back to original full.md. Reason: {e}")
        finally:
            # 清理临时文件
            try:
                if tmp_zip_path and os.path.exists(tmp_zip_path):
                    os.remove(tmp_zip_path)
                if tmp_md_path and os.path.exists(tmp_md_path):
                    os.remove(tmp_md_path)
            except Exception:
                pass

        # 2) 回退：直接读取 zip 中的 full.md
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            if "full.md" in z.namelist():
                with z.open("full.md") as md_file:
                    content = md_file.read().decode('utf-8')
                    logger.info(f"Successfully extracted original full.md, content length: {len(content)}")
                    return content
            else:
                raise FileNotFoundError("full.md not found in the downloaded zip file (fallback failed).")

    def parse_pdf_from_url(self, pdf_external_url: str) -> str:
        """方法接收 pdf_external_ur"""
        token_index, token = self._get_token()
        if token_index is None or not token:
            raise RuntimeError("No available tokens.")

        # 在每次 API 调用前记录本次将要使用的 token 索引，确保下次轮替时排除
        try:
            self.redis_client.set(self.redis_last_token_key, str(token_index))
        except Exception as e:
            logger.warning(f"Failed to write last token index to Redis: {e}")

        try:
            task_id = self._submit_task(pdf_external_url, token)
            zip_url = self._poll_status(task_id, token, token_index)
            markdown_content = self._download_and_extract_md(zip_url)
            return markdown_content
        except Exception as e:
            logger.error(f"Failed to process PDF from URL {pdf_external_url}: {e}")
            raise

#测试
if __name__ == "__main__":
    parser = MineruPDFParserAPI()

    test_url = "https://example.com/policy.pdf"
    try:
        md_content = parser.parse_pdf_from_url(test_url)
        print(md_content[:500])  # 打印前500个字符预览
    except Exception as e:
        print(f"Error: {e}")
