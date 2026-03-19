import secrets
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from app.core.logger import logger
from app.core.storage import storage_manager


@dataclass
class ApiKeyInfo:
    key: str
    name: str = ""
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_used: float = 0
    request_count: int = 0


class ApiKeyManager:
    def __init__(self):
        self._keys: Dict[str, ApiKeyInfo] = {}

    async def init(self):
        data = await storage_manager.load_json("api_keys.json", default={})
        if data and "keys" in data:
            for k, v in data["keys"].items():
                try:
                    self._keys[k] = ApiKeyInfo(**v)
                except Exception:
                    pass
        if not self._keys:
            default = ApiKeyInfo(key="sk-test", name="Default Key")
            self._keys["sk-test"] = default
            await self._save()
            logger.info("Created default API key: sk-test")

    def validate_key(self, key: str) -> Optional[ApiKeyInfo]:
        info = self._keys.get(key)
        if info and info.enabled:
            return info
        return None

    def record_usage(self, key: str):
        info = self._keys.get(key)
        if info:
            info.last_used = time.time()
            info.request_count += 1

    async def create_key(self, name: str = "") -> ApiKeyInfo:
        key = f"sk-{secrets.token_urlsafe(32)}"
        info = ApiKeyInfo(key=key, name=name)
        self._keys[key] = info
        await self._save()
        return info

    async def create_keys_batch(self, names: List[str]) -> List[ApiKeyInfo]:
        result = []
        for name in names:
            info = await self.create_key(name)
            result.append(info)
        return result

    async def update_key(self, key: str, **kwargs) -> bool:
        info = self._keys.get(key)
        if not info:
            return False
        for k, v in kwargs.items():
            if hasattr(info, k) and k != "key":
                setattr(info, k, v)
        await self._save()
        return True

    async def delete_key(self, key: str) -> bool:
        if key in self._keys:
            del self._keys[key]
            await self._save()
            return True
        return False

    async def delete_keys_batch(self, keys: List[str]) -> int:
        count = 0
        for key in keys:
            if key in self._keys:
                del self._keys[key]
                count += 1
        if count:
            await self._save()
        return count

    def get_all(self) -> List[dict]:
        return [
            asdict(v)
            for v in sorted(self._keys.values(), key=lambda x: x.created_at, reverse=True)
        ]

    def get_stats(self) -> dict:
        total = len(self._keys)
        enabled = sum(1 for v in self._keys.values() if v.enabled)
        total_requests = sum(v.request_count for v in self._keys.values())
        return {"total": total, "enabled": enabled, "total_requests": total_requests}

    async def _save(self):
        data = {"keys": {k: asdict(v) for k, v in self._keys.items()}}
        await storage_manager.save_json("api_keys.json", data)


api_key_manager = ApiKeyManager()
