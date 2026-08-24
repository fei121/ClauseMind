# 这个代码封装了和数据库的连接与交互，并求外暴露了交互接口
from sqlalchemy import create_engine, text
from config import (
    APP_DB, APP_HOST, APP_PORT, APP_USER, APP_PASSWORD,
    KB_DB, KB_HOST, KB_PORT, KB_USER, KB_PASSWORD
)
from utils import logger
from typing import Optional, List, Dict, Any, Union
from contextlib import contextmanager
import pandas as pd

class DatabaseManager:
    def __init__(self):
        self.app_engine = None
        self.kb_engine = None
        self._init_engines()

    def _init_engines(self):
        """初始化数据库引擎"""
        try:
            # 打印连接参数（注意隐藏密码）
            logger.info(f"【数据库连接】正在连接App service数据库: {APP_HOST}:{APP_PORT}/{APP_DB}")
            logger.info(f"【数据库连接】正在连接KB数据库: {KB_HOST}:{KB_PORT}/{KB_DB}")

            # 初始化App service数据库引擎
            app_connection_string = f"mysql+pymysql://{APP_USER}:{APP_PASSWORD}@{APP_HOST}:{APP_PORT}/{APP_DB}"
            self.app_engine = create_engine(app_connection_string, pool_recycle=3600)

            # 初始化KB数据库引擎
            kb_connection_string = f"mysql+pymysql://{KB_USER}:{KB_PASSWORD}@{KB_HOST}:{KB_PORT}/{KB_DB}"
            self.kb_engine = create_engine(kb_connection_string, pool_recycle=3600)

            logger.info("【数据库连接】数据库引擎初始化成功")
        except Exception as e:
            logger.error(f"【数据库连接】数据库引擎初始化失败: {str(e)}")
            raise e

    @contextmanager
    def get_connection(self, db_type: str = 'app'):
        """
        获取数据库连接的上下文管理器
        :param db_type: 数据库类型，'app' 或 'kb'
        :return: 数据库连接
        """
        engine = self.app_engine if db_type.lower() == 'app' else self.kb_engine
        if not engine:
            raise ValueError(f"【数据库连接】数据库引擎未初始化: {db_type}")

        connection = None
        try:
            connection = engine.connect()
            yield connection
        except Exception as e:
            logger.error(f"【数据库连接】数据库连接错误: {str(e)}")
            raise e
        finally:
            if connection:
                connection.close()

    # 数据查询逻辑
    def execute_search(self, query: str, params: Optional[Dict] = None, db_type: str = 'app') -> List[Dict[str, Any]]:
        """
        执行查询语句
        :param query: SQL查询语句
        :param params: 查询参数
        :param db_type: 数据库类型，'app' 或 'kb'
        :return: 查询结果列表
        """
        with self.get_connection(db_type) as conn:
            try:
                result = conn.execute(text(query), params or {})
                return [dict(row) for row in result]
            except Exception as e:
                logger.error(f"【数据库查询】查询执行错误: {str(e)}")
                raise e

    # 数据插入、更新逻辑
    def execute_insert(self, query: str, params: Optional[Dict] = None, db_type: str = 'app') -> int:
        """
        执行更新语句
        :param query: SQL更新语句
        :param params: 更新参数
        :param db_type: 数据库类型，'app' 或 'kb'
        :return: 受影响的行数
        """
        with self.get_connection(db_type) as conn:
            try:
                result = conn.execute(text(query), params or {})
                conn.commit()
                return result.rowcount
            except Exception as e:
                conn.rollback()
                logger.error(f"【数据库插入】插入执行错误: {str(e)}")
                raise e

    # 批量插入逻辑，暂时不需要
    def execute_batch(self, query: str, params_list: List[Dict], db_type: str = 'app') -> int:
        """
        批量执行SQL语句
        :param query: SQL语句
        :param params_list: 参数列表
        :param db_type: 数据库类型，'app' 或 'kb'
        :return: 受影响的行数
        """
        with self.get_connection(db_type) as conn:
            try:
                result = conn.execute(text(query), params_list)
                conn.commit()
                return result.rowcount
            except Exception as e:
                conn.rollback()
                logger.error(f"【数据库批量执行】批量执行错误: {str(e)}")
                raise e
    def execute_query(
            self,
            query: str,
            params: Optional[dict] = None,  # 修改为字典类型
            return_type: str = "list_of_dicts",
            key_column: Optional[str] = None,
            db_type: str = 'app'
    ) -> Union[List[Dict], Dict, pd.DataFrame]:
        with self.get_connection(db_type) as conn:
            try:
                result = conn.execute(text(query), params or {})
                if return_type == "pandas":
                    result = pd.read_sql(query, conn, params=params)
                else:
                    rows = result.all()
                    columns = result.keys()  # 获取列名

                    if return_type == "list_of_dicts":
                        # 将元组列表转换为字典列表
                        result = [dict(zip(columns, row)) for row in rows]
                    elif return_type == "dict_of_dicts":
                        if not key_column:
                            raise ValueError("key_column must be specified for dict_of_dicts")
                        result = {row[columns.index(key_column)]: dict(zip(columns, row)) for row in rows}
                    else:
                        raise ValueError(f"Unsupported return_type: {return_type}")
                return result
            except Exception as e:
                logger.error(f"查询失败: {str(e)}")
                raise e
            finally:
                if return_type != "pandas":
                    logger.info("查询成功")
# 创建全局数据库管理器实例
db_manager = DatabaseManager()
