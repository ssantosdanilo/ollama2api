import asyncio
import time
from collections import defaultdict
from datetime import datetime

from app.core.logger import logger
from app.core.storage import storage_manager


class RequestStats:
    def __init__(self):
        self._hourly = {}
        self._daily = {}
        self._dirty = False
        self._save_task: asyncio.Task = None

    async def init(self):
        data = await storage_manager.load_json("request_stats.json", default={})
        self._hourly = data.get("hourly", {})
        self._daily = data.get("daily", {})
        self._cleanup()
        self._save_task = asyncio.create_task(self._periodic_save())
        logger.info("Request stats initialized")

    async def _periodic_save(self):
        while True:
            try:
                await asyncio.sleep(60)
                await self.save()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    def record(self, model: str, success: bool):
        now = datetime.now()
        h_key = now.strftime("%Y-%m-%d %H")
        d_key = now.strftime("%Y-%m-%d")

        for store, key in [(self._hourly, h_key), (self._daily, d_key)]:
            if key not in store:
                store[key] = {"total": 0, "success": 0, "failed": 0, "models": {}}
            store[key]["total"] += 1
            if success:
                store[key]["success"] += 1
            else:
                store[key]["failed"] += 1
            store[key]["models"][model] = store[key]["models"].get(model, 0) + 1

        self._dirty = True

    def get_summary(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        today_stats = self._daily.get(today, {"total": 0, "success": 0, "failed": 0, "models": {}})

        all_total = sum(d["total"] for d in self._daily.values())
        all_success = sum(d["success"] for d in self._daily.values())
        all_failed = sum(d["failed"] for d in self._daily.values())

        model_dist = defaultdict(int)
        for d in self._daily.values():
            for m, c in d.get("models", {}).items():
                model_dist[m] += c

        return {
            "today": today_stats,
            "all_time": {"total": all_total, "success": all_success, "failed": all_failed},
            "model_distribution": dict(model_dist),
        }

    def get_hourly(self, hours: int = 24) -> list:
        now = datetime.now()
        result = []
        keys = sorted(self._hourly.keys(), reverse=True)[:hours]
        for k in reversed(keys):
            result.append({"time": k, **self._hourly[k]})
        return result

    def get_daily(self, days: int = 7) -> list:
        result = []
        keys = sorted(self._daily.keys(), reverse=True)[:days]
        for k in reversed(keys):
            result.append({"date": k, **self._daily[k]})
        return result

    def _cleanup(self):
        now = time.time()
        cutoff_h = datetime.fromtimestamp(now - 7 * 86400).strftime("%Y-%m-%d %H")
        cutoff_d = datetime.fromtimestamp(now - 30 * 86400).strftime("%Y-%m-%d")
        self._hourly = {k: v for k, v in self._hourly.items() if k >= cutoff_h}
        self._daily = {k: v for k, v in self._daily.items() if k >= cutoff_d}

    async def save(self):
        if self._dirty:
            await storage_manager.save_json(
                "request_stats.json",
                {"hourly": self._hourly, "daily": self._daily},
            )
            self._dirty = False

    async def shutdown(self):
        if self._save_task:
            self._save_task.cancel()
            try:
                await self._save_task
            except asyncio.CancelledError:
                pass
        await self.save()


request_stats = RequestStats()
