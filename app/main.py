from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from app.routers.factor_disassembly import (
    process_factor_deconstruction_background,
    process_factor_disassembly_background,
)
from models.pydantic.request import DsRequest
from models.oldpydantic.request import DeconstructInput
import models.oldpydantic  # Trigger model_rebuild()
from utils import logger
from infrastructure.thread_pool_manager import thread_pool_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动：预创建常用线程池（避免首次请求时的延迟）
    thread_pool_manager.get_pool("llm_cpu")
    thread_pool_manager.get_pool("io_bound")
    logger.info("[Lifespan] 线程池初始化完成")

    yield  # 应用运行期间

    # 关闭：优雅关闭线程池
    thread_pool_manager.shutdown_all(wait=True)
    logger.info("[Lifespan] 线程池已关闭")


app = FastAPI(lifespan=lifespan)


@app.get('/health')
async def health_check():
    return {'status': 'healthy'}


@app.post('/disassemble/factor')
async def factor_disassemble(request: DeconstructInput, background_tasks: BackgroundTasks):
    """
    Factor disassembly endpoint - legacy version
    """
    background_tasks.add_task(process_factor_disassembly_background, request)
    logger.info(f'条款拆解: deconstructId-{str(request.deconstructId)} 报文已接收，正在进行拆解')
    logger.info(f"接收到的报文: {request.model_dump()}")
    return {'code': '200', 'message': f'{str(request.deconstructId)}-报文已接收，正在进行拆解'}


@app.post('/deconstruct/request')
async def request_deconstruct(request: DsRequest, background_tasks: BackgroundTasks):
    """
    Request disassembly endpoint - New Pydantic models
    """
    background_tasks.add_task(process_factor_deconstruction_background, request)
    logger.info(f'请求体拆解: transNo-{str(request.transNo)} 报文已接收，正在进行拆解')
    logger.info(f"接收到的报文: {request.model_dump()}")
    return {'code': '200', 'message': f'{str(request.transNo)}-报文已接收，正在进行拆解'}
