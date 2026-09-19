from __future__ import annotations

import os
import sqlite3
import stat
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import core, models
from .config import get_settings
from .dashboard_auth import (
    DashboardSession,
    require_dashboard_csrf,
    require_dashboard_session,
)
from .db import get_db
from .parser import parse_aba_text
from .telegram_credentials import (
    load_telegram_credentials,
    save_telegram_credentials,
    telegram_credentials_status,
)
from .telegram_session import session_location


router = APIRouter(prefix="/dashboard/api/telegram", tags=["dashboard-telegram"])
AUTH_TTL_SECONDS = 600
_auth_flows: dict[str, dict[str, Any]] = {}


class TelegramCredentialRequest(BaseModel):
    api_id: int = Field(gt=0)
    api_hash: str = Field(min_length=16, max_length=128)


class PhoneRequest(BaseModel):
    phone: str = Field(pattern=r"^\+?[0-9]{8,20}$")


class CodeRequest(BaseModel):
    code: str = Field(pattern=r"^[0-9]{3,12}$")


class PasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class SenderDiscoveryRequest(BaseModel):
    limit: int = Field(default=300, ge=1, le=2000)
    apply: bool = False


def _credentials_ready():
    try:
        credentials = load_telegram_credentials()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not credentials:
        raise HTTPException(
            status_code=409,
            detail="Telegram API credentials are not configured",
        )
    return credentials


def _session_parts():
    settings = get_settings()
    credentials = _credentials_ready()
    path, name, workdir = session_location(settings.telegram_session_name)
    workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(workdir, 0o700)
    except OSError:
        pass
    return settings, credentials, path, name, workdir


def _client():
    from pyrogram import Client

    _settings, credentials, path, name, workdir = _session_parts()
    return (
        Client(
            name,
            api_id=credentials.api_id,
            api_hash=credentials.api_hash,
            workdir=str(workdir),
        ),
        path,
    )


def _local_session_account_id(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(
            f"file:{path}?mode=ro",
            uri=True,
            timeout=0.5,
            check_same_thread=False,
        )
        try:
            row = connection.execute(
                "SELECT user_id FROM sessions LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
    except (sqlite3.Error, OSError):
        return None
    if not row or row[0] is None:
        return None
    return int(row[0])


def _mask_phone(phone: str) -> str:
    digits = phone.strip()
    if len(digits) <= 5:
        return "••••"
    return digits[:3] + ("•" * max(2, len(digits) - 6)) + digits[-3:]


def _clean_flows() -> None:
    cutoff = time.time() - AUTH_TTL_SECONDS
    expired = [
        key for key, value in _auth_flows.items()
        if float(value.get("created_at") or 0) < cutoff
    ]
    for key in expired:
        _auth_flows.pop(key, None)


async def _connect_authorized():
    client, path = _client()
    authorized = False
    try:
        authorized = bool(await client.connect())
        account_id = None
        if authorized:
            me = await client.get_me()
            account_id = int(me.id)
        return client, path, authorized, account_id
    except Exception:
        if getattr(client, "is_connected", False):
            await client.disconnect()
        raise


async def _disconnect(client, path: Path) -> None:
    if getattr(client, "is_connected", False):
        await client.disconnect()
    if path.exists():
        os.chmod(path, 0o600)


def _message_sender_id(message) -> int | None:
    sender = getattr(message, "from_user", None) or getattr(message, "sender_chat", None)
    value = getattr(sender, "id", None)
    return int(value) if value is not None else None


@router.post("/credentials")
def telegram_save_credentials(
    payload: TelegramCredentialRequest,
    session: DashboardSession = Depends(require_dashboard_csrf),
):
    del session
    credentials = save_telegram_credentials(
        payload.api_id,
        payload.api_hash,
    )
    return {
        "configured": True,
        "source": credentials.source,
        "owner_only": True,
    }


@router.get("/status")
async def telegram_status(
    session: DashboardSession = Depends(require_dashboard_session),
):
    del session
    try:
        credential_state = telegram_credentials_status()
    except RuntimeError as exc:
        return {
            "api_credentials": False,
            "credential_error": str(exc),
            "session_exists": False,
            "authorized": False,
            "owner_only": False,
        }
    if not credential_state["configured"]:
        return {
            "api_credentials": False,
            "credential_source": None,
            "session_exists": False,
            "authorized": False,
            "owner_only": False,
        }

    _settings, _credentials, path, _name, _workdir = _session_parts()
    account_id = _local_session_account_id(path)
    owner_only = bool(
        path.exists() and stat.S_IMODE(path.stat().st_mode) == 0o600
    )
    return {
        "api_credentials": True,
        "credential_source": credential_state["source"],
        "session_exists": path.exists(),
        "authorized": account_id is not None,
        "owner_only": owner_only,
        "account_id": account_id,
    }


@router.post("/send-code")
async def telegram_send_code(
    payload: PhoneRequest,
    session: DashboardSession = Depends(require_dashboard_csrf),
):
    _clean_flows()
    _settings, _credentials, existing_path, _name, _workdir = _session_parts()
    account_id = _local_session_account_id(existing_path)
    if account_id is not None:
        return {
            "authorized": True,
            "account_id": account_id,
            "step": "complete",
        }

    client, path = _client()
    try:
        await client.connect()
        sent = await client.send_code(payload.phone)
        _auth_flows[session.nonce] = {
            "phone": payload.phone,
            "phone_code_hash": sent.phone_code_hash,
            "created_at": time.time(),
            "step": "code",
        }
        return {
            "authorized": False,
            "step": "code",
            "masked_phone": _mask_phone(payload.phone),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Telegram code request failed ({type(exc).__name__})",
        ) from exc
    finally:
        await _disconnect(client, path)


@router.post("/confirm-code")
async def telegram_confirm_code(
    payload: CodeRequest,
    session: DashboardSession = Depends(require_dashboard_csrf),
):
    _clean_flows()
    flow = _auth_flows.get(session.nonce)
    if not flow or flow.get("step") != "code":
        raise HTTPException(status_code=409, detail="Telegram code flow expired; resend code")

    client, path = _client()
    try:
        await client.connect()
        from pyrogram.errors import (
            PhoneCodeExpired,
            PhoneCodeInvalid,
            SessionPasswordNeeded,
        )
        try:
            user = await client.sign_in(
                flow["phone"],
                flow["phone_code_hash"],
                payload.code,
            )
        except SessionPasswordNeeded:
            flow["step"] = "password"
            flow["created_at"] = time.time()
            return {
                "authorized": False,
                "step": "password",
                "requires_password": True,
            }
        except (PhoneCodeInvalid, PhoneCodeExpired) as exc:
            raise HTTPException(
                status_code=400,
                detail="Telegram confirmation code is invalid or expired",
            ) from exc

        if not user:
            raise HTTPException(
                status_code=409,
                detail="Telegram account registration is not supported by dashboard setup",
            )
        _auth_flows.pop(session.nonce, None)
        return {
            "authorized": True,
            "step": "complete",
            "account_id": int(user.id),
        }
    finally:
        await _disconnect(client, path)


@router.post("/confirm-password")
async def telegram_confirm_password(
    payload: PasswordRequest,
    session: DashboardSession = Depends(require_dashboard_csrf),
):
    _clean_flows()
    flow = _auth_flows.get(session.nonce)
    if not flow or flow.get("step") != "password":
        raise HTTPException(status_code=409, detail="Telegram password step is not active")

    client, path = _client()
    try:
        await client.connect()
        from pyrogram.errors import PasswordHashInvalid
        try:
            user = await client.check_password(payload.password)
        except PasswordHashInvalid as exc:
            raise HTTPException(
                status_code=400,
                detail="Telegram 2-step verification password is invalid",
            ) from exc
        _auth_flows.pop(session.nonce, None)
        return {
            "authorized": True,
            "step": "complete",
            "account_id": int(user.id),
        }
    finally:
        await _disconnect(client, path)


@router.post("/cancel")
def telegram_cancel(
    session: DashboardSession = Depends(require_dashboard_csrf),
):
    _auth_flows.pop(session.nonce, None)
    return {"cancelled": True}


@router.get("/chats")
async def telegram_chats(
    limit: int = 200,
    session: DashboardSession = Depends(require_dashboard_session),
):
    del session
    limit = min(max(limit, 1), 500)
    client, path, authorized, _account_id = await _connect_authorized()
    try:
        if not authorized:
            raise HTTPException(status_code=409, detail="Telegram session is not authorized")
        rows = []
        async for dialog in client.get_dialogs(limit=limit):
            chat = dialog.chat
            chat_type = getattr(chat.type, "value", str(chat.type))
            if chat_type not in {"group", "supergroup", "channel"}:
                continue
            rows.append(
                {
                    "id": int(chat.id),
                    "title": chat.title or chat.username or str(chat.id),
                    "type": chat_type,
                }
            )
        return rows
    finally:
        await _disconnect(client, path)


@router.post("/sources/{source_id}/discover-sender")
async def discover_sender(
    source_id: str,
    payload: SenderDiscoveryRequest,
    session: DashboardSession = Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    del session
    source = db.get(models.PaymentSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="payment source not found")
    if source.enabled:
        raise HTTPException(
            status_code=409,
            detail="disable payment source before sender discovery",
        )
    if source.telegram_group_id is None:
        raise HTTPException(status_code=409, detail="Telegram group is not configured")

    client, path, authorized, _account_id = await _connect_authorized()
    try:
        if not authorized:
            raise HTTPException(status_code=409, detail="Telegram session is not authorized")
        async for _dialog in client.get_dialogs(limit=500):
            pass

        parsed = 0
        senders: Counter[int] = Counter()
        async for message in client.get_chat_history(
            int(source.telegram_group_id),
            limit=payload.limit,
        ):
            raw = message.text or message.caption or ""
            evidence = parse_aba_text(raw)
            if not evidence.trx_id or evidence.amount_minor is None:
                continue
            parsed += 1
            sender_id = _message_sender_id(message)
            if sender_id is not None:
                senders[sender_id] += 1
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="Telegram group is not reachable by the dedicated session",
        ) from exc
    finally:
        await _disconnect(client, path)

    candidates = [
        {"sender_id": sender_id, "count": count}
        for sender_id, count in senders.most_common()
    ]
    unanimous = bool(
        parsed > 0
        and len(candidates) == 1
        and candidates[0]["count"] == parsed
    )
    applied = False
    if payload.apply:
        if not unanimous:
            raise HTTPException(
                status_code=409,
                detail="sender discovery is not unanimous; refusing to bind sender",
            )
        source = core.configure_source_sender(
            db,
            source_id,
            candidates[0]["sender_id"],
        )
        applied = True

    return {
        "source_id": source_id,
        "parsed_messages": parsed,
        "candidate_senders": candidates,
        "unanimous": unanimous,
        "applied": applied,
        "source_enabled": bool(source.enabled),
        "ready": core.source_ready(source),
    }
