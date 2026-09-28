"""
FastAPI 应用入口。

- /api/*：后端接口（接口文档见 /docs）
- /：前端页面（web/ 目录下的静态文件）
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import convert, palettes, patterns
from app.core.config import settings
from app.core.database import init_db
from app.core.errors import AppError
from app.core.logger import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    logger.info("%s v%s 启动完成，访问 http://%s:%d", settings.app_name, settings.version, settings.host, settings.port)
    yield
    logger.info("服务已停止")


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="把任意图片转换为拼豆（Perler / Hama / MARD 等）图纸：色号网格、用量清单、分板打印 PDF。",
    lifespan=lifespan,
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    """业务异常 -> {"detail": "错误信息"}。"""
    logger.warning("业务异常 %s %s -> %d %s", request.method, request.url.path, exc.status_code, exc.message)
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """参数校验失败时返回可读的中文提示（取第一条错误）。"""
    err = exc.errors()[0]
    field = ".".join(str(p) for p in err.get("loc", []) if p != "body")
    message = f"参数错误 {field}：{err.get('msg')}"
    logger.warning("参数校验失败 %s %s -> %s", request.method, request.url.path, message)
    return JSONResponse({"detail": message}, status_code=422)


@app.middleware("http")
async def log_slow_requests(request: Request, call_next):
    """记录接口耗时，超过 1 秒的请求打 WARNING，便于排查性能问题。"""
    start = time.perf_counter()
    response = await call_next(request)
    cost = (time.perf_counter() - start) * 1000
    if request.url.path.startswith("/api") and cost > 1000:
        logger.warning("慢请求 %s %s 耗时 %.0fms", request.method, request.url.path, cost)
    return response


@app.get("/api/health", tags=["系统"], summary="健康检查")
def health():
    return {"status": "ok", "version": settings.version}


app.include_router(palettes.router)
app.include_router(convert.router)
app.include_router(patterns.router)

# 前端静态文件放最后挂载，避免覆盖 /api 路由
app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
