#!/usr/bin/env python3
"""One-shot SHADOW observation for a staged disabled payment source.

This is a migration gate, not the production collector. It replays a bounded
Telegram history into SHADOW evidence while keeping the source disabled.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app import core, models
from app.config import get_settings
from app.db import SessionLocal
from app.parser import parse_aba_text
from app.telegram_session import session_location


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = (REPO_ROOT / "runtime").resolve()


def _sender_id(message: Any) -> int | None:
    sender = getattr(message, "from_user", None) or getattr(message, "sender_chat", None)
    value = getattr(sender, "id", None)
    return int(value) if value is not None else None


def _safe_session() -> tuple[Path, str, Path]:
    settings = get_settings()
    session_path, client_name, workdir = session_location(
        settings.telegram_session_name
    )
    if session_path.parent.resolve() != RUNTIME_ROOT:
        raise RuntimeError("dedicated Telegram session must live directly under runtime/")
    if not session_path.exists():
        raise RuntimeError(f"dedicated Telegram session is missing: {session_path}")
    if stat.S_IMODE(session_path.stat().st_mode) & 0o077:
        raise RuntimeError("dedicated Telegram session must be owner-only (0600)")
    return session_path, client_name, workdir


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
    finally:
        os.chmod(path, 0o600)

def observe_message(db, source: models.PaymentSource, message: Any) -> dict[str, Any]:
    raw_text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    parsed = parse_aba_text(raw_text)
    if not parsed.trx_id or parsed.amount_minor is None:
        return {"result": "ignored_unparseable"}

    sender_id = _sender_id(message)
    if sender_id != source.telegram_sender_id:
        return {
            "result": "ignored_sender_mismatch",
            "sender_id": sender_id,
            "trx_id": parsed.trx_id,
            "amount_minor": parsed.amount_minor,
        }

    existing = db.scalar(
        select(models.PaymentEvidence).where(
            models.PaymentEvidence.source_id == source.id,
            models.PaymentEvidence.trx_id == parsed.trx_id,
        )
    )
    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram-shadow",
        transport_message_id=str(getattr(message, "id", "")),
        sender_id=sender_id,
        raw_text=raw_text,
        received_at=getattr(message, "date", None),
        initial_state="SHADOW",
    )
    return {
        "result": "existing" if existing else "shadow_observed",
        "evidence_id": evidence.id,
        "state": evidence.state,
        "trx_id": evidence.trx_id,
        "amount_minor": evidence.amount_minor,
        "message_id": str(getattr(message, "id", "")),
    }


async def observe(source_id: str, limit: int) -> dict[str, Any]:
    settings = get_settings()
    if not settings.telegram_shadow_only:
        raise RuntimeError("TELEGRAM_SHADOW_ONLY must remain true")
    if settings.allow_live_telegram:
        raise RuntimeError("ALLOW_LIVE_TELEGRAM must remain false")
    if settings.allow_shadow_promotion:
        raise RuntimeError("ALLOW_SHADOW_PROMOTION must remain false")
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("Telegram API credentials are not configured")

    session_path, client_name, workdir = _safe_session()

    with SessionLocal() as db:
        source = db.get(models.PaymentSource, source_id)
        if not source:
            raise RuntimeError("payment source not found")
        if source.enabled:
            raise RuntimeError("shadow observation requires the staged source to stay disabled")
        if not core.source_configured(source):
            raise RuntimeError("staged source must have group, sender, merchant alias, and static KHQR")
        group_id = int(source.telegram_group_id)
        expected_sender = int(source.telegram_sender_id)
        evidence_before = db.scalar(
            select(func.count(models.PaymentEvidence.id)).where(
                models.PaymentEvidence.source_id == source.id
            )
        ) or 0

    from pyrogram import Client

    client = Client(
        client_name,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        workdir=str(workdir),
    )
    authorized = False
    messages: list[Any] = []
    try:
        authorized = bool(await client.connect())
        if not authorized:
            raise RuntimeError("dedicated Telegram session is not authorized")
        async for message in client.get_chat_history(group_id, limit=limit):
            messages.append(message)
    finally:
        if getattr(client, "is_connected", False):
            await client.disconnect()

    results: list[dict[str, Any]] = []
    with SessionLocal() as db:
        source = db.get(models.PaymentSource, source_id)
        if not source or source.enabled:
            raise RuntimeError("source state changed during shadow observation")
        for message in messages:
            try:
                results.append(observe_message(db, source, message))
            except core.Conflict as exc:
                results.append(
                    {
                        "result": "conflict",
                        "message_id": str(getattr(message, "id", "")),
                        "reason": str(exc),
                    }
                )

        evidence_after = db.scalar(
            select(func.count(models.PaymentEvidence.id)).where(
                models.PaymentEvidence.source_id == source.id
            )
        ) or 0
        non_shadow = db.scalar(
            select(func.count(models.PaymentEvidence.id)).where(
                models.PaymentEvidence.source_id == source.id,
                models.PaymentEvidence.state != "SHADOW",
            )
        ) or 0

    counts: dict[str, int] = {}
    for row in results:
        key = str(row["result"])
        counts[key] = counts.get(key, 0) + 1

    if non_shadow:
        raise RuntimeError("non-SHADOW evidence exists for staged source")
    if counts.get("shadow_observed", 0) + counts.get("existing", 0) == 0:
        raise RuntimeError("no trusted parseable ABA evidence was observed")

    return {
        "source_id": source_id,
        "source_enabled": False,
        "group_id": group_id,
        "trusted_sender_id": expected_sender,
        "session": str(session_path),
        "history_messages_read": len(messages),
        "evidence_before": int(evidence_before),
        "evidence_after": int(evidence_after),
        "new_shadow_evidence": counts.get("shadow_observed", 0),
        "existing_evidence": counts.get("existing", 0),
        "ignored_unparseable": counts.get("ignored_unparseable", 0),
        "ignored_sender_mismatch": counts.get("ignored_sender_mismatch", 0),
        "conflicts": counts.get("conflict", 0),
        "non_shadow_evidence": int(non_shadow),
        "rows": [
            row
            for row in results
            if row["result"] in {"shadow_observed", "existing", "conflict"}
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay bounded Telegram history into SHADOW evidence"
    )
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--output",
        default="runtime/telegram-shadow-observation.json",
        help="Owner-only summary artifact for parity review",
    )
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 2000:
        parser.error("--limit must be between 1 and 2000")

    try:
        result = asyncio.run(observe(args.source_id, args.limit))
        output = Path(args.output)
        _write_result(output, result)
    except Exception as exc:
        print(f"TELEGRAM_SHADOW_OBSERVE BLOCKED: {type(exc).__name__}: {exc}")
        return 1

    print("TELEGRAM_SHADOW_OBSERVE PASS")
    print(f"- source_id={result['source_id']}")
    print("- source_enabled=false")
    print(f"- history_messages_read={result['history_messages_read']}")
    print(f"- new_shadow_evidence={result['new_shadow_evidence']}")
    print(f"- existing_evidence={result['existing_evidence']}")
    print(f"- sender_mismatch_ignored={result['ignored_sender_mismatch']}")
    print(f"- conflicts={result['conflicts']}")
    print("- non_shadow_evidence=0")
    print(f"- output={output} (mode 0600)")
    print("- shadow_promotion=false")
    print("- live_activation=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
