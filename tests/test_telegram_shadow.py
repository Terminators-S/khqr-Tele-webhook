from types import SimpleNamespace

from sqlalchemy import select

from app import models
from app.workers.telegram import _persist_message


def test_collector_persists_shadow_without_settlement(db, business_and_source):
    _business, source, _api_key = business_and_source
    message = SimpleNamespace(
        id=90001,
        text="$1.41 paid by TEST via ABA PAY. Trx. ID: 123450000000001, APV: 111111.",
        caption=None,
        date=None,
        from_user=SimpleNamespace(id=source.telegram_sender_id),
        sender_chat=None,
    )

    _persist_message(source, message, "telegram", shadow_only=True)

    evidence = db.scalar(
        select(models.PaymentEvidence).where(
            models.PaymentEvidence.source_id == source.id,
            models.PaymentEvidence.transport_message_id == str(message.id),
        )
    )
    assert evidence is not None
    assert evidence.state == "SHADOW"
    assert evidence.matched_request_id is None
