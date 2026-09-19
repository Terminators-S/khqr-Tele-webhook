import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select, text

from .. import core, models
from ..config import get_settings
from ..db import SessionLocal, engine
from ..telegram_credentials import load_telegram_credentials
from ..telegram_session import session_location


log = logging.getLogger("khqr.telegram")


def _enabled_sources():
    db = SessionLocal()
    try:
        return list(
            db.scalars(
                select(models.PaymentSource).where(models.PaymentSource.enabled.is_(True))
            )
        )
    finally:
        db.close()


def _claim_source_locks(sources):
    connection = engine.connect()
    if engine.dialect.name != "postgresql":
        return connection, {source.id for source in sources}
    owned = set()
    for source in sources:
        key = f"khqr-source:{source.id}"
        locked = connection.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": key}
        ).scalar()
        if locked:
            owned.add(source.id)
    return connection, owned


def _message_sender_id(message):
    sender = getattr(message, "from_user", None) or getattr(message, "sender_chat", None)
    return getattr(sender, "id", None)


def _persist_message(source, message, transport: str, shadow_only: bool | None = None):
    raw_text = message.text or message.caption or ""
    if not raw_text:
        return
    db = SessionLocal()
    try:
        shadow = get_settings().telegram_shadow_only if shadow_only is None else shadow_only
        core.ingest_evidence(
            db,
            source_id=source.id,
            transport=transport,
            transport_message_id=str(message.id),
            sender_id=_message_sender_id(message),
            raw_text=raw_text,
            received_at=message.date,
            initial_state="SHADOW" if shadow else "RECEIVED",
        )
        cursor = db.scalar(
            select(models.SourceCursor).where(models.SourceCursor.source_id == source.id)
        )
        if not cursor:
            cursor = models.SourceCursor(source_id=source.id)
            db.add(cursor)
        cursor.last_message_id = str(message.id)
        cursor.last_reconciled_at = core.utcnow()
        db.commit()
    except core.Conflict as exc:
        db.rollback()
        log.warning("evidence rejected source=%s message=%s reason=%s", source.id, message.id, exc)
    finally:
        db.close()


async def _history_replay(client, sources):
    cutoff = core.utcnow() - timedelta(seconds=get_settings().history_recovery_seconds)
    for source in sources:
        async for message in client.get_chat_history(source.telegram_group_id, limit=1000):
            if message.date and core.as_utc(message.date) < cutoff:
                break
            _persist_message(source, message, "telegram")


async def run() -> None:
    settings = get_settings()
    credentials = load_telegram_credentials()
    if not credentials:
        raise RuntimeError("Telegram API credentials are required")
    if not settings.telegram_shadow_only and not settings.allow_live_telegram:
        raise RuntimeError("live Telegram mode is blocked; set ALLOW_LIVE_TELEGRAM=true only at approved cutover")

    from pyrogram import Client, filters, idle
    from pyrogram.handlers import MessageHandler

    sources = [source for source in _enabled_sources() if core.source_ready(source)]
    lock_connection, owned_ids = _claim_source_locks(sources)
    sources = [source for source in sources if source.id in owned_ids]
    if not sources:
        lock_connection.close()
        raise RuntimeError("no ready payment source lock acquired")

    source_by_group = {int(source.telegram_group_id): source for source in sources}
    _session_path, client_name, workdir = session_location(settings.telegram_session_name)
    client = Client(
        client_name,
        api_id=credentials.api_id,
        api_hash=credentials.api_hash,
        workdir=str(workdir),
    )


    async def on_message(_client, message):
        chat_id = int(getattr(message.chat, "id", 0))
        source = source_by_group.get(chat_id)
        if source:
            _persist_message(source, message, "telegram")

    client.add_handler(
        MessageHandler(on_message, filters.chat(list(source_by_group.keys())))
    )
    try:
        await client.start()
        await _history_replay(client, sources)
        log.info("collector live for %d payment source(s) shadow_only=%s", len(sources), settings.telegram_shadow_only)
        await idle()
    finally:
        try:
            await client.stop()
        finally:
            lock_connection.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
