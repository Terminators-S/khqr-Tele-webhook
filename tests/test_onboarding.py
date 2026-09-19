import json
import stat
import time

import httpx
import pytest

from app.security import sign_webhook
from scripts import onboard_consuming_project as onboard
from scripts.integration_smoke import start_webhook_receiver


def test_onboarding_uses_only_admin_business_and_source_routes(tmp_path):
    captured: list[tuple[str, str, dict, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(
            (
                request.method,
                request.url.path,
                body,
                request.headers.get("X-Internal-Secret"),
            )
        )
        if request.url.path == "/internal/businesses":
            return httpx.Response(
                200,
                json={
                    "id": "biz_1",
                    "name": "Client",
                    "slug": "client",
                    "api_key": "khqr_live_secret-value",
                    "webhook_secret": "whsec_secret-value",
                },
            )
        if request.url.path == "/internal/businesses/biz_1/sources":
            return httpx.Response(
                200,
                json={
                    "id": "src_1",
                    "business_id": "biz_1",
                    "name": "ABA",
                    "currency": "USD",
                    "telegram_group_id": -1001,
                    "telegram_sender_id": 42,
                    "merchant_alias": "CLIENT",
                    "static_khqr": "STATIC",
                    "enabled": True,
                    "ready": True,
                },
            )
        return httpx.Response(404, json={"detail": "unexpected route"})

    output = tmp_path / "client.env"
    result = onboard.onboard_project(
        api_base_url="https://khqr.test",
        internal_secret="internal-test-secret",
        project_name="Client",
        project_slug="client",
        source_name="ABA",
        currency="USD",
        merchant_alias="CLIENT",
        static_khqr="STATIC",
        telegram_group_id=-1001,
        telegram_sender_id=42,
        webhook_url="https://client.invalid/hook",
        output_path=output,
        transport=httpx.MockTransport(handler),
    )

    assert [item[1] for item in captured] == [
        "/internal/businesses",
        "/internal/businesses/biz_1/sources",
    ]
    assert all(item[3] == "internal-test-secret" for item in captured)
    assert captured[0][2]["webhook_url"] == "https://client.invalid/hook"
    assert captured[1][2]["enabled"] is True
    assert result["business_id"] == "biz_1"
    assert result["source_id"] == "src_1"
    assert result["source_ready"] is True
    assert "credentials" not in result["redacted"]

    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    contents = output.read_text()
    assert "KHQR_API_KEY=khqr_live_secret-value" in contents
    assert "KHQR_WEBHOOK_SECRET=whsec_secret-value" in contents
    assert "KHQR_SOURCE_ID=src_1" in contents
def test_onboarding_fails_closed_when_source_is_not_ready():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/businesses":
            return httpx.Response(
                200,
                json={
                    "id": "biz_2",
                    "api_key": "khqr_live_key",
                    "webhook_secret": None,
                },
            )
        return httpx.Response(200, json={"id": "src_bad", "ready": False})

    with pytest.raises(RuntimeError, match="not ready"):
        onboard.onboard_project(
            api_base_url="https://khqr.test",
            internal_secret="internal",
            project_name="Bad",
            project_slug="bad",
            source_name="ABA",
            currency="USD",
            merchant_alias="BAD",
            static_khqr="STATIC",
            telegram_group_id=-1002,
            telegram_sender_id=43,
            transport=httpx.MockTransport(handler),
        )


def test_credentials_reject_newline_values(tmp_path):
    with pytest.raises(ValueError, match="newline"):
        onboard._write_credentials(
            tmp_path / "bad.env",
            {"KHQR_API_KEY": "safe\nINJECTED=1"},
        )


def test_temporary_webhook_receiver_verifies_paid_event_signature():
    server, capture, thread = start_webhook_receiver()
    secret = "whsec_test-secret"
    capture.set_secret(secret)
    try:
        body = json.dumps(
            {
                "id": "evt_paid_1",
                "type": "payment.intent.paid",
                "created_at": "2026-09-19T00:00:00+00:00",
                "data": {
                    "intent_id": "pi_1",
                    "trx_id": "SMOKE12345678",
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        timestamp = str(int(time.time()))
        signature = "v1=" + sign_webhook(secret, timestamp, body)
        response = httpx.post(
            f"http://127.0.0.1:{server.server_port}/khqr-smoke",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-KHQR-Event-ID": "evt_paid_1",
                "X-KHQR-Timestamp": timestamp,
                "X-KHQR-Signature": signature,
            },
            timeout=5,
        )
        assert response.status_code == 204
        record = capture.wait_for_paid("pi_1", timeout=1)
        assert record["valid"] is True
        assert record["payload"]["data"]["trx_id"] == "SMOKE12345678"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_temporary_webhook_receiver_rejects_bad_signature():
    server, capture, thread = start_webhook_receiver()
    capture.set_secret("whsec_correct")
    try:
        body = b'{"id":"evt_bad","type":"payment.intent.paid","data":{"intent_id":"pi_bad"}}'
        timestamp = str(int(time.time()))
        response = httpx.post(
            f"http://127.0.0.1:{server.server_port}/khqr-smoke",
            content=body,
            headers={
                "X-KHQR-Event-ID": "evt_bad",
                "X-KHQR-Timestamp": timestamp,
                "X-KHQR-Signature": "v1=bad",
            },
            timeout=5,
        )
        assert response.status_code == 401
        assert capture.events[-1]["valid"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_staged_disabled_source_allows_sender_to_be_discovered_later():
    captured_source = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/businesses":
            return httpx.Response(
                200,
                json={
                    "id": "biz_stage",
                    "api_key": "khqr_live_stage",
                    "webhook_secret": None,
                },
            )
        captured_source.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "src_stage",
                "enabled": False,
                "ready": False,
            },
        )

    result = onboard.onboard_project(
        api_base_url="https://khqr.test",
        internal_secret="internal",
        project_name="Stage",
        project_slug="stage",
        source_name="ABA",
        currency="USD",
        merchant_alias="STAGE",
        static_khqr="STATIC",
        telegram_group_id=-1003,
        telegram_sender_id=None,
        enabled=False,
        transport=httpx.MockTransport(handler),
    )
    assert captured_source["telegram_sender_id"] is None
    assert captured_source["enabled"] is False
    assert result["source_enabled"] is False
    assert result["source_ready"] is False
