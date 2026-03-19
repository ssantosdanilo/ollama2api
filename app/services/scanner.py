import asyncio
import ipaddress
import json
import os
import shutil
import tempfile
import time

import aiohttp

from app.core.config import settings
from app.core.constants import TARGET_MODELS
from app.core.logger import logger
from app.services.backend_manager import backend_manager


class ScannerService:

    # IP 段从 data/known_ranges.json 加载，启动时自动读取
    # 格式: [{"name": "示例段", "country": "XX", "start": "1.2.0.0", "end": "1.2.255.255", "description": "描述"}]
    KNOWN_RANGES = []

    @classmethod
    def _load_known_ranges(cls):
        """从 data/known_ranges.json 加载自定义扫描范围"""
        path = os.path.join(settings.storage_path, "known_ranges.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cls.KNOWN_RANGES = json.load(f)
                logger.info(f"[Scanner] 加载 {len(cls.KNOWN_RANGES)} 个扫描范围")
            except Exception as e:
                logger.warning(f"[Scanner] known_ranges.json 加载失败: {e}")

    def __init__(self):
        self._load_known_ranges()
        self._scanning = False
        self._stop_requested = False
        self._progress = {"total": 0, "scanned": 0, "found": 0, "running": False}
        self._history_path = os.path.join(settings.storage_path, "scan_history.json")
        self._history = {"scanned_ranges": [], "last_cleanup": 0}
        self._cleanup_task = None
        self._masscan_path = shutil.which("masscan")
        self._session: aiohttp.ClientSession | None = None

    async def init(self):
        self._load_history()
        connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300, enable_cleanup_closed=True)
        self._session = aiohttp.ClientSession(connector=connector)
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"[Scanner] 初始化完成, masscan: {'可用' if self._masscan_path else '不可用(回退纯Python)'}")

    def _load_history(self):
        if os.path.exists(self._history_path):
            try:
                with open(self._history_path, "r", encoding="utf-8") as f:
                    self._history = json.load(f)
            except Exception:
                pass

    def _save_history(self):
        os.makedirs(os.path.dirname(self._history_path), exist_ok=True)
        with open(self._history_path, "w", encoding="utf-8") as f:
            json.dump(self._history, f, indent=2, ensure_ascii=False)

    def is_range_scanned(self, start_ip: str, end_ip: str) -> bool:
        for r in self._history.get("scanned_ranges", []):
            if r["start"] == start_ip and r["end"] == end_ip:
                return True
        return False

    async def scan_range(self, start_ip: str, end_ip: str, force: bool = False):
        if self._scanning:
            return {"error": "扫描正在进行中"}

        if not force and self.is_range_scanned(start_ip, end_ip):
            return {"error": "该IP段已扫描过，使用 force=true 强制重扫"}

        self._scanning = True
        self._stop_requested = False
        try:
            start = int(ipaddress.IPv4Address(start_ip))
            end = int(ipaddress.IPv4Address(end_ip))
        except ValueError as e:
            self._scanning = False
            return {"error": f"IP格式错误: {e}"}

        total = end - start + 1
        self._progress = {"total": total, "scanned": 0, "found": 0, "running": True}
        found_ips = []

        if self._masscan_path:
            logger.info(f"[Scanner] 使用 masscan 扫描 {start_ip}-{end_ip} ({total} IP)")
            alive_ips = await self._masscan_scan(start_ip, end_ip)
            self._progress["scanned"] = total
            if alive_ips and not self._stop_requested:
                self._progress["total"] = total + len(alive_ips)
                sem = asyncio.Semaphore(settings.scanner_concurrency)

                async def verify_ip(ip):
                    if self._stop_requested:
                        return
                    async with sem:
                        ok = await self._probe(ip)
                    self._progress["scanned"] += 1
                    if ok:
                        found_ips.append(ip)
                        self._progress["found"] += 1

                await asyncio.gather(*[verify_ip(ip) for ip in alive_ips], return_exceptions=True)
        else:
            logger.info(f"[Scanner] masscan 不可用，使用纯 Python 扫描 {total} IP")
            sem = asyncio.Semaphore(settings.scanner_concurrency)

            async def check_ip(ip_int):
                if self._stop_requested:
                    return
                ip = str(ipaddress.IPv4Address(ip_int))
                async with sem:
                    result = await self._probe(ip)
                self._progress["scanned"] += 1
                if result:
                    found_ips.append(ip)
                    self._progress["found"] += 1

            await asyncio.gather(*[check_ip(i) for i in range(start, end + 1)], return_exceptions=True)

        stopped = self._stop_requested

        usable = 0
        if found_ips:
            result = await backend_manager.add_backends_batch(found_ips)
            usable = result.get("added", 0)

        record = {
            "start": start_ip, "end": end_ip,
            "scanned_at": time.time(),
            "found": len(found_ips), "usable": usable,
            "found_ips": found_ips,
            "method": "masscan" if self._masscan_path else "python",
        }
        self._history["scanned_ranges"] = [
            r for r in self._history["scanned_ranges"]
            if not (r["start"] == start_ip and r["end"] == end_ip)
        ]
        self._history["scanned_ranges"].append(record)
        self._save_history()

        self._scanning = False
        self._stop_requested = False
        self._progress["running"] = False
        logger.info(f"[Scanner] {'已停止' if stopped else '扫描完成'}: {start_ip}-{end_ip}, 发现 {len(found_ips)}, 入库 {usable}")
        return {"found": len(found_ips), "usable": usable, "stopped": stopped}

    async def _masscan_scan(self, start_ip: str, end_ip: str) -> list[str]:
        """Phase 1: Use masscan to quickly find IPs with port 11434 open."""
        port = settings.default_ollama_port
        tmpdir = tempfile.mkdtemp(prefix="ollama_scan_")
        targets_file = os.path.join(tmpdir, "targets.txt")
        output_file = os.path.join(tmpdir, "output.txt")
        try:
            with open(targets_file, "w") as f:
                f.write(f"{start_ip}-{end_ip}\n")
            cmd = [
                self._masscan_path, "-iL", targets_file,
                "-p", str(port), "--rate", str(settings.masscan_rate),
                "-oL", output_file, "--wait", "3",
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"[Scanner] masscan 退出码 {proc.returncode}: {stderr.decode()[:200]}")
                return []
            alive_ips = []
            if os.path.exists(output_file):
                with open(output_file) as f:
                    for line in f:
                        if line.startswith("open"):
                            parts = line.strip().split()
                            if len(parts) >= 4:
                                alive_ips.append(parts[3])
            logger.info(f"[Scanner] masscan 发现 {len(alive_ips)} 个开放端口")
            return alive_ips
        except Exception as e:
            logger.error(f"[Scanner] masscan 执行失败: {e}")
            return []
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def _probe(self, ip: str) -> bool:
        """探测单个 IP 是否运行目标模型的 Ollama"""
        port = settings.default_ollama_port
        url = f"http://{ip}:{port}/api/tags"
        timeout = aiohttp.ClientTimeout(total=settings.scanner_timeout, connect=settings.scanner_timeout)
        try:
            session = self._session
            if not session:
                return False
            async with session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                models = [m.get("name", "").split(":")[0] for m in data.get("models", [])]
                return any(m in TARGET_MODELS for m in models)
        except Exception:
            return False

    def stop_scan(self):
        if self._scanning:
            self._stop_requested = True
            return True
        return False

    def get_progress(self) -> dict:
        return dict(self._progress)

    def get_history(self) -> list:
        return sorted(
            self._history.get("scanned_ranges", []),
            key=lambda x: x.get("scanned_at", 0),
            reverse=True,
        )

    def get_stats(self) -> dict:
        ranges = self._history.get("scanned_ranges", [])
        return {
            "total_ranges": len(ranges),
            "total_found": sum(r.get("found", 0) for r in ranges),
            "total_usable": sum(r.get("usable", 0) for r in ranges),
        }

    def get_recommended_ranges(self) -> list:
        scanned = {(r["start"], r["end"]) for r in self._history.get("scanned_ranges", [])}
        result = []
        for r in self.KNOWN_RANGES:
            is_scanned = (r["start"], r["end"]) in scanned
            est = self.estimate_scan(r["start"], r["end"])
            result.append({**r, "scanned": is_scanned, **est})
        return result

    def estimate_scan(self, start_ip: str, end_ip: str) -> dict:
        try:
            total = int(ipaddress.IPv4Address(end_ip)) - int(ipaddress.IPv4Address(start_ip)) + 1
        except ValueError:
            return {"ip_count": 0, "estimated_seconds": 0}
        if self._masscan_path:
            estimated_seconds = (total / settings.masscan_rate) + 10  # masscan + wait + verify overhead
        else:
            estimated_seconds = (total / settings.scanner_concurrency) * settings.scanner_timeout
        return {"ip_count": total, "estimated_seconds": round(estimated_seconds), "method": "masscan" if self._masscan_path else "python"}

    def get_system_report(self) -> dict:
        backends = backend_manager.get_all()
        stats = backend_manager.get_stats()
        scan_stats = self.get_stats()
        online = [b for b in backends if b["status"] == "online"]
        offline = [b for b in backends if b["status"] == "offline"]
        model_dist = {}
        for b in online:
            for m in (b.get("models") or []):
                short = m.split(":")[0]
                if short in TARGET_MODELS:
                    model_dist[short] = model_dist.get(short, 0) + 1
        failed_dist = {}
        for b in backends:
            for m in (b.get("failed_models") or []):
                short = m.split(":")[0]
                if short in TARGET_MODELS:
                    failed_dist[short] = failed_dist.get(short, 0) + 1
        return {
            "backend_stats": stats,
            "model_distribution": model_dist,
            "failed_model_distribution": failed_dist,
            "scan_stats": scan_stats,
            "scanning": self._scanning,
            "online_count": len(online),
            "offline_count": len(offline),
            "masscan_available": bool(self._masscan_path),
        }

    async def auto_scan_recommended(self):
        """Auto-scan all unscanned recommended ranges sequentially."""
        if self._scanning or getattr(self, "_auto_scanning", False):
            return {"error": "扫描正在进行中"}
        self._auto_scanning = True

        unscanned = [r for r in self.KNOWN_RANGES if not self.is_range_scanned(r["start"], r["end"])]
        if not unscanned:
            self._auto_scanning = False
            return {"total": 0, "message": "所有推荐地区已扫描完毕"}

        self._auto_queue = {"total": len(unscanned), "current": 0, "current_name": "", "results": []}
        try:
            for r in unscanned:
                self._auto_queue["current"] += 1
                self._auto_queue["current_name"] = r["name"]
                result = await self.scan_range(r["start"], r["end"])
                self._auto_queue["results"].append({"name": r["name"], "country": r["country"], **result})
        finally:
            self._auto_scanning = False

        self._auto_queue["current_name"] = "完成"
        total_found = sum(r.get("found", 0) for r in self._auto_queue["results"])
        total_usable = sum(r.get("usable", 0) for r in self._auto_queue["results"])
        return {"total": len(unscanned), "found": total_found, "usable": total_usable}

    def get_auto_progress(self) -> dict:
        return getattr(self, "_auto_queue", {"total": 0, "current": 0, "current_name": ""})

    async def cleanup_offline(self) -> int:
        threshold = time.time() - settings.cleanup_offline_hours * 3600
        backends = backend_manager.get_all()
        removed = 0
        for b in backends:
            if b["status"] == "offline" and b.get("last_check", 0) > 0 and b["last_check"] < threshold:
                await backend_manager.remove_backend(b["key"])
                removed += 1
        self._history["last_cleanup"] = time.time()
        self._save_history()
        logger.info(f"[Scanner] 清理完成: 删除 {removed} 个离线后端")
        return removed

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(3600)
            try:
                await self.cleanup_offline()
            except Exception as e:
                logger.error(f"[Scanner] 定时清理出错: {e}")

    def get_smart_recommendations(self) -> dict:
        """Score unscanned ranges based on historical hit rates by provider/country."""
        history = self._history.get("scanned_ranges", [])
        scanned_set = {(r["start"], r["end"]) for r in history}

        # Calc hit rates by provider and country
        provider_hits, provider_total = {}, {}
        country_hits, country_total = {}, {}
        for r in self.KNOWN_RANGES:
            provider = r["name"].split()[0]
            country = r["country"]
            hist = next((h for h in history if h["start"] == r["start"] and h["end"] == r["end"]), None)
            if hist:
                found = hist.get("found", 0)
                provider_hits[provider] = provider_hits.get(provider, 0) + found
                provider_total[provider] = provider_total.get(provider, 0) + 1
                country_hits[country] = country_hits.get(country, 0) + found
                country_total[country] = country_total.get(country, 0) + 1

        provider_rate = {p: provider_hits[p] / max(provider_total[p], 1) for p in provider_hits}
        country_rate = {c: country_hits[c] / max(country_total[c], 1) for c in country_hits}

        # Score unscanned ranges
        recommendations = []
        for r in self.KNOWN_RANGES:
            if (r["start"], r["end"]) in scanned_set:
                continue
            provider = r["name"].split()[0]
            country = r["country"]
            stars = r.get("description", "").count("⭐")
            p_rate = provider_rate.get(provider, 0)
            c_rate = country_rate.get(provider, 0)
            score = p_rate * 50 + country_rate.get(country, 0) * 30 + stars * 15 + (10 if provider in provider_hits else 0)

            reasons = []
            if p_rate > 0:
                reasons.append(f"{provider}历史命中率{p_rate:.1f}")
            if country_rate.get(country, 0) > 0:
                reasons.append(f"{country}地区有命中记录")
            if stars:
                reasons.append(f"{'⭐' * stars}高优先级")
            if not reasons:
                reasons.append("新供应商，值得探索")

            est = self.estimate_scan(r["start"], r["end"])
            recommendations.append({
                **r, "score": round(score, 1), "reason": "，".join(reasons), **est
            })

        recommendations.sort(key=lambda x: x["score"], reverse=True)

        provider_stats = {p: {"scanned": provider_total.get(p, 0), "total_found": provider_hits.get(p, 0),
                              "avg_hit_rate": round(provider_rate.get(p, 0), 2)} for p in set(list(provider_hits.keys()) + list(provider_total.keys()))}

        unscanned_count = len([r for r in self.KNOWN_RANGES if (r["start"], r["end"]) not in scanned_set])
        return {"recommendations": recommendations, "total_unscanned": unscanned_count, "provider_stats": provider_stats}

    async def shutdown(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
            self._session = None


scanner_service = ScannerService()
