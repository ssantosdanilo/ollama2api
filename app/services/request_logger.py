import asyncio
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import List

from app.core.config import settings
from app.core.storage import storage_manager


@dataclass
class RequestLog:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    model: str = ""
    backend: str = ""
    api_key_preview: str = ""
    status: str = ""
    error: str = ""
    duration_ms: float = 0
    ip: str = ""
    stream: bool = False


class RequestLogger:
    def __init__(self):
        self._logs: List[RequestLog] = []
        self._save_task: asyncio.Task = None
        self._dirty = False

    async def init(self):
        data = await storage_manager.load_json("request_logs.json", default={})
        if data and "logs" in data:
            for item in data["logs"]:
                try:
                    self._logs.append(RequestLog(**item))
                except Exception:
                    pass
        self._save_task = asyncio.create_task(self._periodic_save())

    async def _periodic_save(self):
        while True:
            await asyncio.sleep(60)
            try:
                await self.save()
            except Exception:
                pass

    def log(
        self,
        model: str = "",
        backend: str = "",
        api_key: str = "",
        status: str = "success",
        error: str = "",
        duration_ms: float = 0,
        ip: str = "",
        stream: bool = False,
    ):
        preview = ""
        if api_key:
            preview = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else api_key

        entry = RequestLog(
            model=model,
            backend=backend,
            api_key_preview=preview,
            status=status,
            error=error[:200] if error else "",
            duration_ms=round(duration_ms, 1),
            ip=ip,
            stream=stream,
        )
        self._logs.append(entry)
        self._dirty = True

        max_entries = settings.max_log_entries
        if len(self._logs) > max_entries:
            self._logs = self._logs[-max_entries:]

    def get_logs(self, limit: int = 50, offset: int = 0) -> dict:
        total = len(self._logs)
        logs = list(reversed(self._logs))
        page = logs[offset : offset + limit]
        return {
            "total": total,
            "logs": [asdict(l) for l in page],
        }

    async def clear(self):
        self._logs.clear()
        self._dirty = True
        await self.save()

    async def save(self):
        if not self._dirty:
            return
        await storage_manager.save_json("request_logs.json", {"logs": [asdict(l) for l in self._logs]})
        self._dirty = False

    async def shutdown(self):
        if self._save_task:
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
        await self.save()


request_logger = RequestLogger()
