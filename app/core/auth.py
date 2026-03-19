"""统一的 session 认证管理模块"""

import secrets
import time

from fastapi import Header, HTTPException

SESSION_TTL = 86400
MAX_SESSIONS = 5000

# 内存中的 session 存储
_sessions: dict = {}


def cleanup_sessions(now: float | None = None):
    """清理过期和超量的 session"""
    now = time.time() if now is None else now
    expired = [k for k, v in _sessions.items() if now > v.get("expires", 0)]
    for k in expired:
        _sessions.pop(k, None)

    if len(_sessions) > MAX_SESSIONS:
        items = sorted(_sessions.items(), key=lambda kv: kv[1].get("expires", 0))
        to_remove = len(_sessions) - MAX_SESSIONS
        for k, _ in items[:to_remove]:
            _sessions.pop(k, None)


def create_session(username: str) -> str:
    """创建新 session，返回 token"""
    token = secrets.token_urlsafe(32)
    _sessions[token] = {"user": username, "expires": time.time() + SESSION_TTL}
    return token


def validate_token(token: str) -> dict | None:
    """验证 token，返回 session 或 None"""
    session = _sessions.get(token)
    if not session:
        return None
    if time.time() > session["expires"]:
        _sessions.pop(token, None)
        return None
    return session


async def require_admin(authorization: str = Header(None)):
    """FastAPI 依赖：要求管理员登录"""
    cleanup_sessions()
    if not authorization:
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.replace("Bearer ", "").strip()
    session = validate_token(token)
    if not session:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return session
