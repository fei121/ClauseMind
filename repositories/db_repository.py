"""
Database Repository - Abstracts database operations
Handles database queries and inserts for the factor disassembly service
"""
import json
import traceback

import pymysql
from typing import Optional, Dict, Any
from utils import logger
import os
from dotenv import load_dotenv
from infrastructure.db_utils import db_manager
load_dotenv()

# Database configuration
DB_CONFIG = {
    "host": os.environ.get('APP_TEST_DB_HOST'),
    "user": os.environ.get('APP_TEST_DB_USER'),
    "password": os.environ.get('APP_TEST_DB_PASSWORD'),
    "database": os.environ.get('APP_TEST_DB_NAME'),
    "port": int(os.environ.get('APP_TEST_DB_PORT', 3306))
}


class DatabaseRepository:
    """Repository for database operations"""

    def __init__(self):
        """Initialize database repository"""
        self.logger = logger
        self.db_config = DB_CONFIG

    def _get_connection(self):
        """Get database connection"""
        return pymysql.connect(**self.db_config)

    def find_result_by_plan_no(self, plan_no: str) -> Optional[Dict[str, Any]]:
        """
        Find disassembly result by plan number

        Args:
            plan_no: Plan number to search for

        Returns:
            Dictionary with id and deconstruct_result, or None if not found
        """
        conn = None
        cursor = None
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            like_pattern = f"%{plan_no}%"
            select_query = """
                           SELECT id, deconstruct_result
                           FROM app_deconstruct_output
                           WHERE deconstruct_result LIKE %s
                           ORDER BY id DESC LIMIT 1 \
                           """
            cursor.execute(select_query, (like_pattern,))
            row = cursor.fetchone()

            if row:
                record_id, deconstruct_result_json_str = row
                try:
                    deconstruct_data = json.loads(deconstruct_result_json_str)
                    return {
                        "id": record_id,
                        "deconstruct_result": deconstruct_data
                    }
                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to decode JSON from database record {record_id}: {e}")
                    return None

            return None

        except Exception as e:
            self.logger.error(f"Failed to find result by plan number: {e}")
            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except Exception as e:
                    self.logger.warning(f"关闭数据库游标失败: {e}")
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    self.logger.warning(f"关闭数据库连接失败: {e}")
    #
    # def update_deconstruct_result(self, record_id: int, deconstruct_result: Dict[str, Any]) -> None:
    #     """
    #     Update deconstruction result in database
    #
    #     Args:
    #         record_id: Database record ID
    #         deconstruct_result: Updated deconstruction result dictionary
    #     """
    #     conn = None
    #     cursor = None
    #     try:
    #         conn = self._get_connection()
    #         cursor = conn.cursor()
    #
    #         update_sql = "UPDATE app_deconstruct_output SET deconstruct_result = %s WHERE id = %s"
    #         cursor.execute(update_sql, (json.dumps(deconstruct_result, ensure_ascii=False), record_id))
    #         conn.commit()
    #
    #         self.logger.info(f"Successfully updated deconstruct result for record {record_id}")
    #
    #     except Exception as e:
    #         self.logger.error(f"Failed to update deconstruct result: {e}")
    #         raise
    #     finally:
    #         if cursor:
    #             try:
    #                 cursor.close()
    #             except:
    #                 pass
    #         if conn:
    #             try:
    #                 conn.close()
    #             except:
    #                 pass
    def db_insert_disassemble_result(
            self,
            input_json,
            prompts_json,
            output_json
    ):
        sql = """INSERT INTO demo_disassemble_service_middle_info
        (input_json, prompts_json, output_json)
        VALUES (:input_json, :prompts_json, :output_json)"""

        params = {
            "input_json": input_json,
            "prompts_json": prompts_json,
            "output_json": output_json
        }
        try:
            db_manager.execute_insert(sql, params=params)
        except Exception as e:
            logger.info(f"条款拆解：拆解中间结果插入数据库异常，{str(e)}")
            print("堆栈跟踪信息:")
            traceback.print_exc()


    def db_insert_result(
            self,
            report_no,
            request_json,
            prompt,
            agent_name,
            response_json,
            audit_memo,
            text_messages,
            tool_messages,
            time_cost,
    ):
        sql = """INSERT INTO demo_one_agent_multiple_tools
                 (report_no, request_json, prompt, agent_name, response_json, audit_memo, text_messages, tool_messages, \
                  time_cost)
                 VALUES (:report_no, :request_json, :prompt, :agent_name, :response_json, :audit_memo, :text_messages, \
                         :tool_messages, :time_cost)"""

        params = {
            "report_no": report_no,
            "request_json": request_json,
            "prompt": prompt,
            "agent_name": agent_name,
            "response_json": response_json,
            "audit_memo": audit_memo,
            "text_messages": text_messages,
            "tool_messages": tool_messages,
            "time_cost": time_cost
        }

        db_manager.execute_insert(sql, params=params)
