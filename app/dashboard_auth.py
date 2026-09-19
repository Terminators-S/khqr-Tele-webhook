from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import get_settings
from .security import hash_secret


COOKIE_NAME = "khqr_dashboard_session"


@dataclass(frozen=True)
class DashboardSession:
    expires_at: int
    nonce: str
    token: str

    @property
    def csrf_token(self) -> str:
        settings = get_settings()
        return hmac.new(
            settings.internal_secret.encode("utf-8"),
            f"dashboard-csrf:{self.token}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()


def _signature(payload: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.internal_secret.encode("utf-8"),
        f"dashboard-session:{payload}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_dashboard_session() -> DashboardSession:
    settings = get_settings()
    expires_at = int(time.time()) + settings.dashboard_session_ttl_seconds
    nonce = secrets.token_urlsafe(24)
    payload = f"{expires_at}.{nonce}"
    signature = _signature(payload)
    token = f"v1.{payload}.{signature}"
    return DashboardSession(expires_at=expires_at, nonce=nonce, token=token)


def parse_dashboard_session(token: str | None) -> DashboardSession | None:
    if not token:
        return None
    try:
        version, expires_raw, nonce, signature = token.split(".", 3)
        expires_at = int(expires_raw)
    except (TypeError, ValueError):
        return None
    if version != "v1" or expires_at <= int(time.time()) or not nonce:
        return None
    payload = f"{expires_at}.{nonce}"
    if not hmac.compare_digest(signature, _signature(payload)):
        return None
    return DashboardSession(expires_at=expires_at, nonce=nonce, token=token)

def verify_dashboard_secret(value: str) -> bool:
    expected = get_settings().internal_secret
    return hmac.compare_digest(hash_secret(value), hash_secret(expected))


def require_dashboard_session(request: Request) -> DashboardSession:
    session = parse_dashboard_session(request.cookies.get(COOKIE_NAME))
    if not session:
        raise HTTPException(status_code=401, detail="dashboard authentication required")
    return session


def require_dashboard_csrf(request: Request) -> DashboardSession:
    session = require_dashboard_session(request)
    provided = request.headers.get("X-CSRF-Token", "")
    if not hmac.compare_digest(provided, session.csrf_token):
        raise HTTPException(status_code=403, detail="invalid dashboard csrf token")
    return session
