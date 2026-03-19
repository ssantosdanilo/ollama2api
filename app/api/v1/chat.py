import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

from app.core.logger import logger
from app.models.openai_models import ChatCompletionRequest
from app.services.api_keys import api_key_manager
from app.services.ollama_client import OllamaClient
from app.services.request_stats import request_stats
from app.services.request_logger import request_logger

router = APIRouter()


@router.post("/chat/completions")
async def chat_completions(request: Request, body: ChatCompletionRequest):
    # API Key 验证：有 key 配置时强制要求有效 key
    auth = request.headers.get("authorization", "")
    api_key = auth.replace("Bearer ", "").strip() if auth else ""
    client_ip = request.client.host if request.client else ""

    key_info = api_key_manager.validate_key(api_key)
    if not key_info and api_key_manager.get_all():
        return JSONResponse(
            status_code=401,
            content={"error": {"message": "Invalid API key", "type": "auth_error"}},
        )
    if key_info:
        api_key_manager.record_usage(api_key)

    start = time.time()
    try:
        if body.stream:
            result = await OllamaClient.chat(body)

            async def tracked_stream():
                success = True
                try:
                    async for chunk in result:
                        yield chunk
                except Exception:
                    success = False
                    raise
                finally:
                    duration = (time.time() - start) * 1000
                    request_stats.record(body.model, success)
                    request_logger.log(
                        model=body.model, api_key=api_key,
                        status="success" if success else "error",
                        duration_ms=duration, ip=client_ip, stream=True,
                    )

            return StreamingResponse(
                tracked_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            result = await OllamaClient.chat(body)
            duration = (time.time() - start) * 1000
            request_stats.record(body.model, True)
            request_logger.log(
                model=body.model,
                api_key=api_key,
                status="success",
                duration_ms=duration,
                ip=client_ip,
                stream=False,
            )
            return result

    except Exception as e:
        duration = (time.time() - start) * 1000
        request_stats.record(body.model, False)
        request_logger.log(
            model=body.model,
            api_key=api_key,
            status="error",
            error=str(e),
            duration_ms=duration,
            ip=client_ip,
            stream=body.stream or False,
        )
        logger.error(f"Chat error: {e}")
        return JSONResponse(
            status_code=502,
            content={"error": {"message": str(e), "type": "backend_error"}},
        )
