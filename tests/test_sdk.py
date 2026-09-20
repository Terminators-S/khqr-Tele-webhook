import json

import httpx
import pytest

from khqr_sdk import KhqrApiError, KhqrClient, decode_webhook, verify_webhook_signature
from app.security import sign_webhook


def test_sdk_create_intent_sends_auth_and_idempotency_headers():
    captured = {}

    def handler(request: httpx.Request):
        captured["api_key"] = request.headers.get("X-Api-Key")
        captured["idempotency"] = request.headers.get("Idempotency-Key")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"id": "pi_1", "status": "PENDING"},
        )

    transport = httpx.MockTransport(handler)
    with KhqrClient("https://khqr.example", "client-key", transport=transport) as client:
        response = client.create_payment_intent(
            source_id="src_1",
            external_id="order_1",
            amount_minor=141,
            idempotency_key="idem_1",
            metadata={"cart": 1},
            remark_prefix="BB",
        )

    assert response["id"] == "pi_1"
    assert captured["api_key"] == "client-key"
    assert captured["idempotency"] == "idem_1"
    assert captured["body"]["amount_minor"] == 141
    assert captured["body"]["remark_prefix"] == "BB"


def test_sdk_raises_structured_api_error():
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(409, json={"detail": "payment conflict"})
    )
    with KhqrClient("https://khqr.example", "client-key", transport=transport) as client:
        with pytest.raises(KhqrApiError) as caught:
            client.get_payment_intent("pi_missing")
    assert caught.value.status_code == 409
    assert caught.value.detail == "payment conflict"


def test_sdk_webhook_signature_and_timestamp_window():
    body = b'{"id":"evt_1","type":"payment.intent.paid"}'
    timestamp = "2000"
    secret = "whsec_test"
    signature = "v1=" + sign_webhook(secret, timestamp, body)

    assert verify_webhook_signature(
        secret, timestamp, body, signature, tolerance_seconds=300, now=2100
    )
    assert not verify_webhook_signature(
        secret, timestamp, body, signature, tolerance_seconds=30, now=2100
    )
    assert not verify_webhook_signature(
        secret, timestamp, body, "v1=bad", tolerance_seconds=300, now=2100
    )
    assert decode_webhook(body)["id"] == "evt_1"
