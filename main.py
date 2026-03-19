"""
Ollama2API

- 兼容 OpenAI 的 /v1/chat/completions 接口
- Ollama 后端池负载均衡 + 健康检查
- 管理后台 /admin
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import RedirectResponse

from app.api.admin import router as admin_router
from app.api.v1.chat import router as chat_router
from app.api.v1.models import router as models_router
from app.api.proxy import router as proxy_router
from app.core.config import settings, runtime_config
from app.core.logger import logger
from app.core.storage import storage_manager
from app.services.backend_manager import backend_manager
from app.services.health_checker import health_checker
from app.services.request_stats import request_stats
from app.services.request_logger import request_logger
from app.services.api_keys import api_key_manager
from app.services.scanner import scanner_service
from app.services.proxy_manager import proxy_manager
from app.services.ollama_client import OllamaClient

try:
    if sys.platform != "win32":
        import uvloop
        uvloop.install()
        logger.info("[Ollama2API] uvloop 已启用")
    else:
        logger.info("[Ollama2API] Windows: 使用默认 asyncio 事件循环")
except Exception:
    logger.info("[Ollama2API] uvloop 未安装，使用默认 asyncio 事件循环")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[Ollama2API] 正在启动...")

    await storage_manager.init()
    logger.info("[Ollama2API] 存储管理器已初始化")

    await runtime_config.init()
    logger.info("[Ollama2API] 运行时配置已初始化")

    await backend_manager.init()
    logger.info("[Ollama2API] 后端管理器已初始化")

    await request_stats.init()
    logger.info("[Ollama2API] 请求统计已初始化")

    await request_logger.init()
    logger.info("[Ollama2API] 请求日志已初始化")

    await api_key_manager.init()
    logger.info("[Ollama2API] API Key管理器已初始化")

    await health_checker.init()
    logger.info("[Ollama2API] 健康检查已启动")

    await scanner_service.init()
    logger.info("[Ollama2API] 扫描服务已初始化")

    await proxy_manager.init()
    logger.info("[Ollama2API] 代理管理器已初始化")

    await OllamaClient.init()

    logger.info("[Ollama2API] 启动完成")
    yield

    logger.info("[Ollama2API] 正在关闭...")
    await proxy_manager.shutdown()
    await scanner_service.shutdown()
    await health_checker.shutdown()
    await OllamaClient.shutdown()
    await request_stats.shutdown()
    await request_logger.shutdown()
    await backend_manager.shutdown()
    await storage_manager.close()
    logger.info("[Ollama2API] 关闭完成")


app = FastAPI(
    title=settings.app_name,
    description="Ollama 转 OpenAI 兼容 API 反代（负载均衡 + 健康检查）",
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(chat_router, prefix="/v1")
app.include_router(models_router, prefix="/v1")
app.include_router(admin_router)
app.include_router(proxy_router, prefix="/api/proxy")


@app.get("/")
async def root():
    return RedirectResponse(url="/admin/login")


@app.get("/health")
async def health_check_endpoint():
    stats = backend_manager.get_stats()
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "backends": stats,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
