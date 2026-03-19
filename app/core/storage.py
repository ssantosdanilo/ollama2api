import asyncio
import json
import os
import tempfile

import aiofiles

from app.core.config import settings


class StorageManager:
    def __init__(self):
        self._dir = settings.storage_path
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def init(self):
        os.makedirs(self._dir, exist_ok=True)

    async def _get_lock(self, filename: str) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(filename)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[filename] = lock
            return lock

    async def save_json(self, filename: str, data):
        path = os.path.join(self._dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        lock = await self._get_lock(filename)
        async with lock:
            fd, tmp_path = tempfile.mkstemp(prefix=f".{filename}.", dir=os.path.dirname(path))
            os.close(fd)
            try:
                async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
                    await f.write(payload)
                os.replace(tmp_path, path)
            finally:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

    async def load_json(self, filename: str, default=None):
        path = os.path.join(self._dir, filename)
        if not os.path.exists(path):
            return default if default is not None else {}
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                return json.loads(await f.read())
        except Exception:
            return default if default is not None else {}

    async def close(self):
        pass


storage_manager = StorageManager()
