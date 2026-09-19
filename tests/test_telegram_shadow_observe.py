from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import select

from app import core, models
from scripts import telegram_shadow_observe as observe


def make_staged_source(db):
    business, _api_key, _secret = core.create_business(
        db, "Shadow Stage", "shadow-stage"
    )
    source = core.create_source(
        db,
        business.id,
        name="ABA staged",
        currency="USD",
        telegram_group_id=-100909,
        telegram_sender_id=909,
        merchant_alias="SHADOW STAGE",
        static_khqr="STATIC",
        enabled=False,
    )
    return business, source


def message(message_id, sender_id, text):
    return SimpleNamespace(
        id=message_id,
        from_user=SimpleNamespace(id=sender_id) if sender_id is not None else None,
        sender_chat=None,
        text=text,
        caption=None,
        date=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )


def test_trusted_message_is_persisted_as_shadow_only(db):
    _business, source = make_staged_source(db)
    row = observe.observe_message(
        db,
        source,
        message(
            1,
            909,
            "$1.41 paid via ABA PAY. Trx. ID: 178972245859414",
        ),
    )
    assert row["result"] == "shadow_observed"
    assert row["received_at"] == "2026-09-19T00:00:00+00:00"
    evidence = db.scalar(select(models.PaymentEvidence))
    assert evidence is not None
    assert evidence.state == "SHADOW"
    assert evidence.trx_id == "178972245859414"
    assert db.scalar(select(models.PaymentAllocation)) is None


def test_wrong_sender_is_ignored_without_database_write(db):
    _business, source = make_staged_source(db)
    row = observe.observe_message(
        db,
        source,
        message(
            2,
            808,
            "$2.50 paid via ABA PAY. Trx. ID: 178972245859415",
        ),
    )
    assert row["result"] == "ignored_sender_mismatch"
    assert db.scalar(select(models.PaymentEvidence)) is None


def test_unparseable_message_is_ignored(db):
    _business, source = make_staged_source(db)
    row = observe.observe_message(
        db,
        source,
        message(3, 909, "hello from the group"),
    )
    assert row["result"] == "ignored_unparseable"
    assert db.scalar(select(models.PaymentEvidence)) is None


def test_replay_is_idempotent(db):
    _business, source = make_staged_source(db)
    msg = message(
        4,
        909,
        "$3.00 paid via ABA PAY. Trx. ID: 178972245859416",
    )
    first = observe.observe_message(db, source, msg)
    second = observe.observe_message(db, source, msg)
    assert first["result"] == "shadow_observed"
    assert second["result"] == "existing"
    rows = list(db.scalars(select(models.PaymentEvidence)))
    assert len(rows) == 1
    assert rows[0].state == "SHADOW"
