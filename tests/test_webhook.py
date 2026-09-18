import hmac

from sqlalchemy import select

from app import core, models
from app.security import sign_webhook
from app.workers import webhook


class FakeResponse:
    def raise_for_status(self):
        return None


def test_webhook_delivery_uses_stable_event_and_valid_signature(db, monkeypatch):
    business, _key, secret = core.create_business(
        db, "Hook Store", "hook-store", "https://hooks.invalid/payment"
    )
    source = core.create_source(
        db, business.id, name="ABA", currency="USD",
        telegram_group_id=-10077, telegram_sender_id=77,
        merchant_alias="HOOK", static_khqr="STATIC", enabled=True,
    )
    core.create_payment_intent(
        db, business, source_id=source.id,
        external_id="hook-order", idempotency_key="hook-idem",
        amount_minor=321, currency="USD", metadata={},
    )
    captured = {}


    def fake_post(url, *, content, headers, timeout):
        captured.update(
            url=url, content=content, headers=headers, timeout=timeout
        )
        return FakeResponse()

    monkeypatch.setattr(webhook.httpx, "post", fake_post)
    assert webhook.deliver_one() is True

    outbox = db.scalar(select(models.WebhookOutbox))
    db.refresh(outbox)
    assert outbox.status == "SENT"
    assert outbox.attempts == 1
    assert captured["url"] == "https://hooks.invalid/payment"

    event_id = captured["headers"]["X-KHQR-Event-ID"]
    timestamp = captured["headers"]["X-KHQR-Timestamp"]
    signature = captured["headers"]["X-KHQR-Signature"].removeprefix("v1=")
    expected = sign_webhook(secret, timestamp, captured["content"])
    assert hmac.compare_digest(signature, expected)
    assert event_id == outbox.event_id
