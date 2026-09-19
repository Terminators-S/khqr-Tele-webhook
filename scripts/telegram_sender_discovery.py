#!/usr/bin/env python3
"""Read-only discovery of the trusted Telegram notification sender.

This probe is intentionally separate from the collector. It may inspect a
disabled staged source, but it never calls ingest_evidence and never changes
source configuration or payment state.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select

from app import models
from app.config import get_settings
from app.db import SessionLocal
from app.parser import parse_aba_text


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = (REPO_ROOT / "runtime").resolve()


def _session_path() -> Path:
    settings = get_settings()
    base = Path(settings.telegram_session_name).expanduser()
    path = base if base.suffix == ".session" else Path(str(base) + ".session")
    resolved = path.resolve()
    if resolved.parent != RUNTIME_ROOT:
        raise RuntimeError("dedicated Telegram session must live directly under runtime/")
    return resolved


def _sender_id(message: Any) -> int | None:
    sender = getattr(message, "from_user", None) or getattr(message, "sender_chat", None)
    value = getattr(sender, "id", None)
    return int(value) if value is not None else None


def summarize_messages(messages: Iterable[Any]) -> dict[str, Any]:
    candidates: Counter[int] = Counter()
    rows: list[dict[str, Any]] = []
    parsed_messages = 0

    for message in messages:
        raw_text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        parsed = parse_aba_text(raw_text)
        if not parsed.trx_id or parsed.amount_minor is None:
            continue
        parsed_messages += 1
        sender_id = _sender_id(message)
        if sender_id is not None:
            candidates[sender_id] += 1
        received_at = getattr(message, "date", None)
        if isinstance(received_at, datetime):
            received_at = received_at.isoformat()
        rows.append(
            {
                "message_id": str(getattr(message, "id", "")),
                "sender_id": sender_id,
                "trx_id": parsed.trx_id,
                "amount_minor": parsed.amount_minor,
                "received_at": received_at,
            }
        )

    ordered = [
        {"sender_id": sender_id, "count": count}
        for sender_id, count in candidates.most_common()
    ]
    return {
        "parsed_messages": parsed_messages,
        "candidate_senders": ordered,
        "rows": rows,
    }


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
    finally:
        os.chmod(path, 0o600)


async def discover(source_id: str, limit: int) -> dict[str, Any]:
    settings = get_settings()
    if not settings.telegram_shadow_only:
        raise RuntimeError("TELEGRAM_SHADOW_ONLY must remain true")
    if settings.allow_live_telegram:
        raise RuntimeError("ALLOW_LIVE_TELEGRAM must remain false")
    if settings.allow_shadow_promotion:
        raise RuntimeError("ALLOW_SHADOW_PROMOTION must remain false")
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("Telegram API credentials are not configured")

    session_path = _session_path()
    if not session_path.exists():
        raise RuntimeError(f"dedicated Telegram session is missing: {session_path}")
    if stat.S_IMODE(session_path.stat().st_mode) & 0o077:
        raise RuntimeError("dedicated Telegram session must be owner-only (0600)")

    with SessionLocal() as db:
        source = db.get(models.PaymentSource, source_id)
        if not source:
            raise RuntimeError("payment source not found")
        if source.telegram_group_id is None:
            raise RuntimeError("payment source has no Telegram group")
        group_id = int(source.telegram_group_id)
        source_enabled = bool(source.enabled)

    from pyrogram import Client

    client = Client(
        settings.telegram_session_name,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    messages: list[Any] = []
    try:
        await client.start()
        async for message in client.get_chat_history(group_id, limit=limit):
            messages.append(message)
    finally:
        await client.stop()

    summary = summarize_messages(messages)
    summary.update(
        {
            "source_id": source_id,
            "source_enabled": source_enabled,
            "group_id": group_id,
            "session": str(session_path),
        }
    )
    candidates = summary["candidate_senders"]
    if summary["parsed_messages"] == 0:
        raise RuntimeError("no parseable ABA payment notifications found")
    if len(candidates) != 1:
        raise RuntimeError(
            f"sender discovery is ambiguous: {len(candidates)} candidate senders"
        )
    summary["trusted_sender_id"] = candidates[0]["sender_id"]
    summary["unanimous"] = candidates[0]["count"] == summary["parsed_messages"]
    if not summary["unanimous"]:
        raise RuntimeError("some parseable messages have no matching sender identity")
    return summary

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover the ABA Telegram sender without writing payment evidence"
    )
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--output",
        default="runtime/telegram-sender-discovery.json",
        help="Owner-only JSON evidence for later parity review",
    )
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 2000:
        parser.error("--limit must be between 1 and 2000")

    try:
        result = asyncio.run(discover(args.source_id, args.limit))
        output = Path(args.output)
        _write_result(output, result)
    except Exception as exc:
        print(f"TELEGRAM_SENDER_DISCOVERY BLOCKED: {type(exc).__name__}: {exc}")
        return 1

    candidate = result["candidate_senders"][0]
    print("TELEGRAM_SENDER_DISCOVERY PASS")
    print(f"- source_id={result['source_id']}")
    print(f"- source_enabled={str(result['source_enabled']).lower()}")
    print(f"- parsed_messages={result['parsed_messages']}")
    print(f"- candidate_sender_id={candidate['sender_id']}")
    print(f"- candidate_count={candidate['count']}")
    print("- unanimous=true")
    print(f"- output={output} (mode 0600)")
    print("- database_writes=none")
    print("- telegram_shadow_only=true")
    print("- live_activation=false")
    print("- shadow_promotion=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
