from loguru import logger

# 配置项目日志地址，便于运维监控
# try:
# Configure a local log path through the application environment when needed.
# except Exception as e:
#     logger.warning(f"无法添加生产环境日志文件路径: {e}")
logger.add('log.log', rotation='100 MB', retention='1 week')
