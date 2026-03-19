"""代理管理服务 - 支持订阅解析、节点测试、智能选择"""

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import ipaddress
import aiohttp

from app.core.logger import logger
from app.core.storage import storage_manager


@dataclass
class ProxyNode:
    id: str
    name: str
    protocol: str  # ss, vmess, trojan, http, socks5
    server: str
    port: int
    config: dict = field(default_factory=dict)
    latency_ms: float = 0
    alive: bool = False
    last_test: float = 0
    source: str = ""  # subscription url or "manual"

    def get_proxy_url(self) -> Optional[str]:
        if self.protocol == "http":
            auth = ""
            if self.config.get("username"):
                auth = f"{self.config['username']}:{self.config.get('password', '')}@"
            return f"http://{auth}{self.server}:{self.port}"
        if self.protocol == "socks5":
            auth = ""
            if self.config.get("username"):
                auth = f"{self.config['username']}:{self.config.get('password', '')}@"
            return f"socks5://{auth}{self.server}:{self.port}"
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["proxy_url"] = self.get_proxy_url()
        return d


class XrayManager:
    """Manage a single xray-core subprocess for SS/VMess/Trojan proxying."""

    SOCKS_PORT = 10808

    def __init__(self):
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._current_node_id: Optional[str] = None
        self._xray_path = shutil.which("xray")
        self._config_file: Optional[str] = None

    @property
    def available(self) -> bool:
        return self._xray_path is not None

    def _build_config(self, node: 'ProxyNode') -> dict:
        outbound = self._build_outbound(node)
        return {
            "inbounds": [{"tag": "socks-in", "port": self.SOCKS_PORT, "listen": "127.0.0.1",
                          "protocol": "socks", "settings": {"udp": True}}],
            "outbounds": [outbound],
        }

    def _build_outbound(self, node: 'ProxyNode') -> dict:
        cfg = node.config
        if node.protocol == "ss":
            return {
                "protocol": "shadowsocks",
                "settings": {"servers": [{
                    "address": node.server, "port": node.port,
                    "method": cfg.get("method", "aes-256-gcm"),
                    "password": cfg.get("password", ""),
                }]},
            }
        if node.protocol == "vmess":
            user = {"id": cfg.get("id", ""), "alterId": int(cfg.get("aid", 0)), "security": cfg.get("security", "auto")}
            stream = {"network": cfg.get("net", "tcp")}
            net = stream["network"]
            if net == "ws":
                stream["wsSettings"] = {"path": cfg.get("path", "/"), "headers": {"Host": cfg.get("host", "")}}
            elif net == "grpc":
                stream["grpcSettings"] = {"serviceName": cfg.get("path", "")}
            if cfg.get("tls") == "tls":
                stream["security"] = "tls"
                stream["tlsSettings"] = {"serverName": cfg.get("sni") or cfg.get("host", ""), "allowInsecure": True}
            return {
                "protocol": "vmess",
                "settings": {"vnext": [{"address": node.server, "port": node.port, "users": [user]}]},
                "streamSettings": stream,
            }
        if node.protocol == "trojan":
            return {
                "protocol": "trojan",
                "settings": {"servers": [{
                    "address": node.server, "port": node.port,
                    "password": cfg.get("password", ""),
                }]},
                "streamSettings": {
                    "security": "tls",
                    "tlsSettings": {"serverName": cfg.get("sni", node.server), "allowInsecure": cfg.get("allowInsecure", True)},
                },
            }
        return {"protocol": "freedom"}

    async def ensure_running(self, node: 'ProxyNode') -> Optional[str]:
        if not self._xray_path:
            return None
        if self._proc and self._proc.returncode is None and self._current_node_id == node.id:
            return f"socks5://127.0.0.1:{self.SOCKS_PORT}"
        await self.stop()
        config = self._build_config(node)
        try:
            fd, path = tempfile.mkstemp(suffix=".json", prefix="xray_")
            with os.fdopen(fd, "w") as f:
                json.dump(config, f)
            self._config_file = path
            self._proc = await asyncio.create_subprocess_exec(
                self._xray_path, "run", "-config", path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(0.5)
            if self._proc.returncode is not None:
                logger.warning(f"[XrayManager] xray exited with code {self._proc.returncode}")
                self._proc = None
                return None
            self._current_node_id = node.id
            logger.info(f"[XrayManager] Started for {node.name} ({node.protocol})")
            return f"socks5://127.0.0.1:{self.SOCKS_PORT}"
        except Exception as e:
            logger.error(f"[XrayManager] Failed to start: {e}")
            self._proc = None
            return None

    async def stop(self):
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except Exception:
                self._proc.kill()
            logger.info("[XrayManager] Stopped")
        self._proc = None
        self._current_node_id = None
        if self._config_file:
            try:
                os.unlink(self._config_file)
            except OSError:
                pass
            self._config_file = None


class ProxyManager:
    def __init__(self):
        self._nodes: Dict[str, ProxyNode] = {}
        self._subscriptions: List[dict] = []  # [{url, name, added_at}]
        self._enabled = False
        self._auto_select = True
        self._selected_id: Optional[str] = None
        self._lock = asyncio.Lock()
        self._xray = XrayManager()

    async def init(self):
        data = await storage_manager.load_json("proxy.json", default={})
        if data:
            self._enabled = data.get("enabled", False)
            self._auto_select = data.get("auto_select", True)
            self._selected_id = data.get("selected_id")
            self._subscriptions = data.get("subscriptions", [])
            for nid, nd in data.get("nodes", {}).items():
                try:
                    self._nodes[nid] = ProxyNode(**nd)
                except Exception:
                    pass
        logger.info(f"Proxy manager initialized: {len(self._nodes)} nodes")

    async def _save(self):
        data = {
            "enabled": self._enabled,
            "auto_select": self._auto_select,
            "selected_id": self._selected_id,
            "subscriptions": self._subscriptions,
            "nodes": {k: asdict(v) for k, v in self._nodes.items()},
        }
        await storage_manager.save_json("proxy.json", data)

    # --- 订阅解析 ---

    async def add_subscription(self, url: str, name: str = "") -> dict:
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as sess:
                async with sess.get(url) as resp:
                    raw = await resp.text()
        except Exception as e:
            return {"success": False, "message": f"下载失败: {e}"}

        nodes = self._parse_subscription(raw, url)
        if not nodes:
            return {"success": False, "message": "未解析到任何节点"}

        async with self._lock:
            self._subscriptions.append({
                "url": url, "name": name or url[:40],
                "added_at": time.time(), "node_count": len(nodes),
            })
            for n in nodes:
                self._nodes[n.id] = n
            await self._save()

        return {"success": True, "added": len(nodes)}

    def _parse_subscription(self, raw: str, source: str) -> List[ProxyNode]:
        raw = raw.strip()
        # Try Clash YAML
        if raw.startswith("proxies:") or raw.startswith("port:") or "proxies:" in raw[:500]:
            return self._parse_clash(raw, source)
        # Try base64 V2Ray
        try:
            decoded = base64.b64decode(raw + "==").decode("utf-8", errors="ignore")
            if "://" in decoded:
                raw = decoded
        except Exception:
            pass
        # Parse line by line
        nodes = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            n = None
            if line.startswith("ss://"):
                n = self._parse_ss(line, source)
            elif line.startswith("vmess://"):
                n = self._parse_vmess(line, source)
            elif line.startswith("trojan://"):
                n = self._parse_trojan(line, source)
            if n:
                nodes.append(n)
        return nodes

    def _parse_ss(self, url: str, source: str) -> Optional[ProxyNode]:
        try:
            url = url.strip()
            name = ""
            if "#" in url:
                url, name = url.rsplit("#", 1)
                name = urllib.parse.unquote(name)
            body = url[5:]  # remove ss://
            # Format: base64(method:password)@server:port or base64(method:password@server:port)
            if "@" in body:
                info_b64, server_part = body.rsplit("@", 1)
                try:
                    info = base64.b64decode(info_b64 + "==").decode()
                except Exception:
                    info = info_b64
                method, password = info.split(":", 1)
                server, port = server_part.split(":")
            else:
                decoded = base64.b64decode(body + "==").decode()
                method_pass, server_port = decoded.rsplit("@", 1)
                method, password = method_pass.split(":", 1)
                server, port = server_port.split(":")
            nid = f"ss-{server}-{port}"
            return ProxyNode(
                id=nid, name=name or f"SS {server}", protocol="ss",
                server=server, port=int(port), source=source,
                config={"method": method, "password": password},
            )
        except Exception:
            return None

    def _parse_vmess(self, url: str, source: str) -> Optional[ProxyNode]:
        try:
            body = url[8:]  # remove vmess://
            decoded = base64.b64decode(body + "==").decode()
            cfg = json.loads(decoded)
            server = cfg.get("add", "")
            port = int(cfg.get("port", 0))
            name = cfg.get("ps", f"VMess {server}")
            nid = f"vmess-{server}-{port}"
            return ProxyNode(
                id=nid, name=name, protocol="vmess",
                server=server, port=port, source=source,
                config={k: cfg.get(k) for k in ("id", "aid", "net", "type", "host", "path", "tls", "sni") if cfg.get(k)},
            )
        except Exception:
            return None

    def _parse_trojan(self, url: str, source: str) -> Optional[ProxyNode]:
        try:
            url = url.strip()
            name = ""
            if "#" in url:
                url, name = url.rsplit("#", 1)
                name = urllib.parse.unquote(name)
            body = url[9:]  # remove trojan://
            password, rest = body.split("@", 1)
            server_port = rest.split("?")[0]
            server, port = server_port.rsplit(":", 1)
            nid = f"trojan-{server}-{port}"
            params = {}
            if "?" in rest:
                qs = rest.split("?", 1)[1]
                params = dict(urllib.parse.parse_qsl(qs))
            return ProxyNode(
                id=nid, name=name or f"Trojan {server}", protocol="trojan",
                server=server, port=int(port), source=source,
                config={"password": password, **params},
            )
        except Exception:
            return None

    def _parse_clash(self, raw: str, source: str) -> List[ProxyNode]:
        try:
            import yaml
        except ImportError:
            logger.warning("pyyaml not installed, cannot parse Clash config")
            return []
        try:
            data = yaml.safe_load(raw)
            proxies = data.get("proxies", [])
        except Exception:
            return []
        nodes = []
        for p in proxies:
            ptype = p.get("type", "")
            server = p.get("server", "")
            port = int(p.get("port", 0))
            name = p.get("name", f"{ptype} {server}")
            nid = f"{ptype}-{server}-{port}"
            config = {k: v for k, v in p.items() if k not in ("type", "server", "port", "name")}
            proto_map = {"ss": "ss", "vmess": "vmess", "trojan": "trojan", "http": "http", "socks5": "socks5"}
            proto = proto_map.get(ptype)
            if proto and server and port:
                nodes.append(ProxyNode(
                    id=nid, name=name, protocol=proto,
                    server=server, port=port, source=source, config=config,
                ))
        return nodes

    # --- 节点管理 ---

    async def add_node(self, name: str, protocol: str, server: str, port: int, config: dict = None) -> dict:
        nid = f"{protocol}-{server}-{port}"
        node = ProxyNode(
            id=nid, name=name, protocol=protocol,
            server=server, port=port, config=config or {}, source="manual",
        )
        async with self._lock:
            self._nodes[nid] = node
            await self._save()
        return {"success": True, "id": nid}

    async def remove_node(self, nid: str) -> bool:
        async with self._lock:
            if nid in self._nodes:
                del self._nodes[nid]
                if self._selected_id == nid:
                    self._selected_id = None
                await self._save()
                return True
        return False

    async def remove_subscription(self, url: str) -> dict:
        async with self._lock:
            self._subscriptions = [s for s in self._subscriptions if s["url"] != url]
            removed = [nid for nid, n in self._nodes.items() if n.source == url]
            for nid in removed:
                del self._nodes[nid]
            await self._save()
        return {"success": True, "removed_nodes": len(removed)}

    # --- 测试 ---

    async def test_node(self, nid: str) -> dict:
        node = self._nodes.get(nid)
        if not node:
            return {"success": False, "message": "节点不存在"}
        latency = await self._test_latency(node)
        async with self._lock:
            node.latency_ms = latency
            node.alive = latency > 0
            node.last_test = time.time()
            await self._save()
        return {"success": True, "latency_ms": latency, "alive": latency > 0}

    async def test_all(self) -> dict:
        tasks = []
        for nid, node in list(self._nodes.items()):
            tasks.append(self._test_and_update(node))
        if tasks:
            await asyncio.gather(*tasks)
            await self._save()
        alive = sum(1 for n in self._nodes.values() if n.alive)
        return {"success": True, "total": len(self._nodes), "alive": alive}

    async def _test_and_update(self, node: ProxyNode):
        latency = await self._test_latency(node)
        node.latency_ms = latency
        node.alive = latency > 0
        node.last_test = time.time()

    async def _test_latency(self, node: ProxyNode) -> float:
        """Test node latency by connecting to the proxy server."""
        proxy_url = node.get_proxy_url()
        if not proxy_url and self._xray.available and node.protocol in ("ss", "vmess", "trojan"):
            proxy_url = await self._xray.ensure_running(node)
        try:
            t0 = time.time()
            if proxy_url:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as sess:
                    async with sess.get("http://www.gstatic.com/generate_204", proxy=proxy_url) as resp:
                        if resp.status in (200, 204):
                            return round((time.time() - t0) * 1000)
            else:
                # Fallback: test TCP connectivity
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(node.server, node.port), timeout=10
                )
                latency = round((time.time() - t0) * 1000)
                writer.close()
                await writer.wait_closed()
                return latency
        except Exception:
            pass
        return 0

    # --- 选择 ---

    def get_best_node(self) -> Optional[ProxyNode]:
        if not self._enabled:
            return None
        if self._selected_id and not self._auto_select:
            node = self._nodes.get(self._selected_id)
            if node and node.alive:
                return node
        xray_ok = self._xray.available
        alive = [n for n in self._nodes.values()
                 if n.alive and n.latency_ms > 0
                 and (n.get_proxy_url() is not None or xray_ok)]
        if not alive:
            return None
        alive.sort(key=lambda n: n.latency_ms)
        return alive[0]

    async def get_proxy_url(self) -> Optional[str]:
        node = self.get_best_node()
        if not node:
            return None
        url = node.get_proxy_url()
        if url:
            return url
        return await self._xray.ensure_running(node)

    async def smart_select_node(self) -> dict:
        """Pick proxy node closest to where most backend traffic goes."""
        from app.services.backend_manager import backend_manager
        from app.services.scanner import scanner_service

        # Map backend IPs to countries via KNOWN_RANGES, weighted by request_count
        country_reqs = {}
        for b in backend_manager.get_all():
            if b["request_count"] <= 0:
                continue
            try:
                ip_int = int(ipaddress.ip_address(b["ip"]))
            except Exception:
                continue
            for r in scanner_service.KNOWN_RANGES:
                try:
                    start_int = int(ipaddress.ip_address(r["start"]))
                    end_int = int(ipaddress.ip_address(r["end"]))
                except Exception:
                    continue
                if start_int <= ip_int <= end_int:
                    country_reqs[r["country"]] = country_reqs.get(r["country"], 0) + b["request_count"]
                    break

        if not country_reqs:
            return {"success": False, "message": "无法确定后端流量分布，请先使用后端"}

        # Sort countries by total requests
        sorted_countries = sorted(country_reqs.items(), key=lambda x: x[1], reverse=True)
        top_country = sorted_countries[0][0]

        # Country → proxy node name keyword mapping
        COUNTRY_KEYWORDS = {
            "德国": ["德国", "germany", "de", "法兰克福", "frankfurt"],
            "芬兰": ["芬兰", "finland", "fi", "赫尔辛基"],
            "法国": ["法国", "france", "fr", "巴黎"],
            "美国": ["美国", "us", "usa", "洛杉矶", "圣何塞", "硅谷", "西雅图", "纽约", "达拉斯"],
            "加拿大": ["加拿大", "canada", "ca", "蒙特利尔"],
            "日本": ["日本", "japan", "jp", "东京", "大阪"],
            "新加坡": ["新加坡", "singapore", "sg"],
            "英国": ["英国", "uk", "伦敦", "london"],
            "荷兰": ["荷兰", "netherlands", "nl", "阿姆斯特丹"],
        }
        # For European countries, also try other EU nodes as fallback
        EU_COUNTRIES = {"德国", "芬兰", "法国", "英国", "荷兰"}

        keywords = COUNTRY_KEYWORDS.get(top_country, [top_country.lower()])
        xray_ok = self._xray.available
        alive = [n for n in self._nodes.values()
                 if n.alive and n.latency_ms > 0
                 and (n.get_proxy_url() is not None or xray_ok)]
        if not alive:
            return {"success": False, "message": "没有可用的代理节点，请先测速"}

        # Find matching nodes
        def match_node(node, kws):
            name_lower = node.name.lower()
            return any(kw in name_lower for kw in kws)

        matched = [n for n in alive if match_node(n, keywords)]

        # Fallback: if top country is EU, try other EU nodes
        if not matched and top_country in EU_COUNTRIES:
            eu_kws = []
            for c in EU_COUNTRIES:
                eu_kws.extend(COUNTRY_KEYWORDS.get(c, []))
            matched = [n for n in alive if match_node(n, eu_kws)]

        if not matched:
            # Last fallback: pick lowest latency alive node
            matched = alive

        matched.sort(key=lambda n: n.latency_ms)
        best = matched[0]

        # Apply selection
        async with self._lock:
            self._selected_id = best.id
            self._auto_select = False
            await self._save()

        return {
            "success": True,
            "selected": best.to_dict(),
            "reason": f"后端流量最多在{top_country}({country_reqs[top_country]}次请求)，已选择线路「{best.name}」(延迟{best.latency_ms}ms)",
            "traffic_distribution": {c: cnt for c, cnt in sorted_countries[:5]},
        }

    async def shutdown(self):
        await self._xray.stop()

    # --- 状态 ---

    async def set_enabled(self, enabled: bool):
        self._enabled = enabled
        await self._save()

    async def set_auto_select(self, auto: bool):
        self._auto_select = auto
        await self._save()

    async def set_selected(self, nid: Optional[str]):
        self._selected_id = nid
        await self._save()

    def get_nodes(self) -> List[dict]:
        return [n.to_dict() for n in sorted(self._nodes.values(), key=lambda x: x.latency_ms or 99999)]

    def get_subscriptions(self) -> List[dict]:
        return list(self._subscriptions)

    def get_status(self) -> dict:
        best = self.get_best_node()
        return {
            "enabled": self._enabled,
            "auto_select": self._auto_select,
            "selected_id": self._selected_id,
            "total_nodes": len(self._nodes),
            "alive_nodes": sum(1 for n in self._nodes.values() if n.alive),
            "best_node": best.name if best else None,
            "best_latency": best.latency_ms if best else None,
        }


proxy_manager = ProxyManager()
