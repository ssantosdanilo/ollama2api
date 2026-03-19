import json
import os


class Settings:
    DEFAULTS = {
        "app_name": "Ollama2API",
        "app_version": "1.0.0",
        "host": "0.0.0.0",
        "port": 8001,
        "admin_username": os.environ.get("ADMIN_USERNAME", "admin"),
        "admin_password": os.environ.get("ADMIN_PASSWORD", "changeme"),
        "storage_path": "data",
        "request_timeout": 300,
        "connect_timeout": 10,
        "health_check_interval": 300,
        "max_retries": 3,
        "cooldown_threshold": 3,
        "cooldown_duration": 300,
        "max_log_entries": 1000,
        "max_log_file_mb": 10,
        "default_ollama_port": 11434,
        "scanner_concurrency": 50,
        "scanner_timeout": 3,
        "masscan_rate": 5000,
        "cleanup_offline_hours": 24,
    }

    def __init__(self):
        self._data = {}
        self._load()

    def _load(self):
        path = os.path.join(self.DEFAULTS["storage_path"], "config.json")
        existed = os.path.exists(path)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}
        changed = not existed
        for k, v in self.DEFAULTS.items():
            if k not in self._data:
                self._data[k] = v
                changed = True
        if changed:
            self._save()

    def _save(self):
        path = os.path.join(self._data.get("storage_path", "data"), "config.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def __getattr__(self, name):
        if name.startswith("_"):
            return super().__getattribute__(name)
        if name in self._data:
            return self._data[name]
        if name in self.DEFAULTS:
            return self.DEFAULTS[name]
        raise AttributeError(f"No setting: {name}")

    def set(self, key, value):
        self._data[key] = value
        self._save()

    def to_dict(self):
        return dict(self._data)


class RuntimeConfig:
    EDITABLE_KEYS = {
        "admin_username": {"type": "str", "label": "管理员账户", "group": "auth"},
        "admin_password": {"type": "password", "label": "管理员密码", "group": "auth"},
        "request_timeout": {"type": "int", "label": "请求超时(秒)", "group": "network"},
        "connect_timeout": {"type": "int", "label": "连接超时(秒)", "group": "network"},
        "health_check_interval": {"type": "int", "label": "健康检查间隔(秒)", "group": "network"},
        "max_retries": {"type": "int", "label": "最大重试次数", "group": "network"},
        "cooldown_threshold": {"type": "int", "label": "冷却阈值(连续失败次数)", "group": "network"},
        "cooldown_duration": {"type": "int", "label": "冷却时长(秒)", "group": "network"},
        "max_log_entries": {"type": "int", "label": "最大日志条数", "group": "system"},
        "default_ollama_port": {"type": "int", "label": "默认Ollama端口", "group": "network"},
        "scanner_concurrency": {"type": "int", "label": "扫描并发数", "group": "scanner"},
        "scanner_timeout": {"type": "int", "label": "扫描超时(秒)", "group": "scanner"},
        "masscan_rate": {"type": "int", "label": "masscan速率(包/秒)", "group": "scanner"},
        "cleanup_offline_hours": {"type": "int", "label": "离线清理阈值(小时)", "group": "scanner"},
    }

    def __init__(self):
        self._settings = settings

    async def init(self):
        pass

    def get(self, key):
        return getattr(self._settings, key)

    def set(self, key, value):
        meta = self.EDITABLE_KEYS.get(key)
        if not meta:
            return False
        if meta["type"] == "int":
            value = int(value)
        elif meta["type"] == "bool":
            value = value if isinstance(value, bool) else str(value).lower() == "true"
        self._settings.set(key, value)
        return True

    def set_batch(self, updates: dict):
        changed = []
        for k, v in updates.items():
            if self.set(k, v):
                changed.append(k)
        return changed

    def get_all(self):
        return self._settings.to_dict()

    def get_schema(self):
        result = {}
        for key, meta in self.EDITABLE_KEYS.items():
            val = getattr(self._settings, key)
            if meta["type"] == "password":
                val = "***"
            result[key] = {**meta, "value": val}
        return result

    def reset(self):
        for k, v in Settings.DEFAULTS.items():
            if k in self.EDITABLE_KEYS:
                self._settings.set(k, v)


settings = Settings()
runtime_config = RuntimeConfig()
