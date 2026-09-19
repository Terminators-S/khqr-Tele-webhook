from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tests.khqr_test_utils import valid_khqr_payload
from app.main import app
import app.dashboard_telegram as dashboard_telegram


client = TestClient(app)
SECRET = "test-internal-secret-32-characters"


def login():
    response = client.post("/dashboard/api/login", json={"secret": SECRET})
    assert response.status_code == 200
    return response.json()["csrf_token"]


class FakeTelegramClient:
    def __init__(self, messages=None):
        self.is_connected = True
        self.messages = messages or []

    async def disconnect(self):
        self.is_connected = False

    def get_dialogs(self, limit=500):
        async def rows():
            if False:
                yield None
        return rows()
    def get_chat_history(self, chat_id, limit=300):
        async def rows():
            for message in self.messages[:limit]:
                yield message
        return rows()


def payment_message(message_id, sender_id, amount, trx):
    return SimpleNamespace(
        id=message_id,
        from_user=SimpleNamespace(id=sender_id),
        sender_chat=None,
        text="$" + str(amount) + " paid via ABA PAY. Trx. ID: " + str(trx),
        caption=None,
        date=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )


def test_telegram_dashboard_endpoints_require_dashboard_auth():
    assert client.get("/dashboard/api/telegram/status").status_code == 401
    assert client.get("/dashboard/api/telegram/chats").status_code == 401


def test_telegram_cancel_requires_csrf():
    csrf = login()
    response = client.post("/dashboard/api/telegram/cancel", json={})
    assert response.status_code == 403

    allowed = client.post(
        "/dashboard/api/telegram/cancel",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert allowed.status_code == 200
    assert allowed.json()["cancelled"] is True


def test_dashboard_discovers_unanimous_sender_without_enabling(
    db, monkeypatch, tmp_path
):
    csrf = login()
    business = client.post(
        "/dashboard/api/businesses",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Telegram UI", "slug": "telegram-ui"},
    ).json()
    source = client.post(
        "/dashboard/api/sources",
        headers={"X-CSRF-Token": csrf},
        json={
            "business_id": business["id"],
            "name": "ABA Main",
            "currency": "USD",
            "telegram_group_id": -100919191,
            "merchant_alias": "TELEGRAM UI",
            "static_khqr": valid_khqr_payload(account_id="telegram-ui@aba", merchant_name="TELEGRAM UI"),
        },
    ).json()
    assert source["enabled"] is False

    messages = [
        payment_message(1, 191919, "1.01", "178900000000101"),
        payment_message(2, 191919, "2.02", "178900000000102"),
        payment_message(3, 191919, "3.03", "178900000000103"),
    ]
    fake = FakeTelegramClient(messages)
    session_path = tmp_path / "khqr_collector.session"
    session_path.write_bytes(b"session")
    session_path.chmod(0o600)

    async def fake_connect():
        fake.is_connected = True
        return fake, session_path, True, 555

    monkeypatch.setattr(
        dashboard_telegram,
        "_connect_authorized",
        fake_connect,
    )

    response = client.post(
        "/dashboard/api/telegram/sources/"
        + source["id"]
        + "/discover-sender",
        headers={"X-CSRF-Token": csrf},
        json={"limit": 20, "apply": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["parsed_messages"] == 3
    assert body["unanimous"] is True
    assert body["candidate_senders"] == [
        {"sender_id": 191919, "count": 3}
    ]
    assert body["applied"] is False
    assert body["source_enabled"] is False


def test_reset_telegram_account_removes_session_and_clears_sender(db, monkeypatch, tmp_path):
    from app import core

    csrf = login()
    business, _api_key, _secret = core.create_business(db, "Reset Store", "reset-store")
    source = core.create_source(
        db,
        business.id,
        name="ABA Reset",
        currency="USD",
        telegram_group_id=-100700700,
        telegram_sender_id=700700,
        merchant_alias="RESET STORE",
        static_khqr=valid_khqr_payload(account_id="reset@aba", merchant_name="RESET STORE"),
        enabled=False,
    )
    session_path = tmp_path / "khqr_collector.session"
    session_path.write_bytes(b"session")
    session_path.chmod(0o600)
    wal_path = tmp_path / "khqr_collector.session-wal"
    wal_path.write_bytes(b"wal")

    monkeypatch.setattr(
        dashboard_telegram,
        "session_location",
        lambda _value: (session_path, "khqr_collector", tmp_path),
    )

    response = client.post(
        "/dashboard/api/telegram/reset-account",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": "CHANGE TELEGRAM ACCOUNT"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["authorized"] is False
    assert body["session_removed"] is True
    assert body["sender_bindings_cleared"] == 1
    assert body["group_mappings_preserved"] == 1
    assert not session_path.exists()
    assert not wal_path.exists()

    db.expire_all()
    refreshed = db.get(type(source), source.id)
    assert refreshed.telegram_group_id == -100700700
    assert refreshed.telegram_sender_id is None
    assert refreshed.enabled is False


def test_reset_telegram_account_blocked_while_any_source_enabled(db, monkeypatch, tmp_path):
    from app import core

    csrf = login()
    business, _api_key, _secret = core.create_business(db, "Live Store", "live-store")
    core.create_source(
        db,
        business.id,
        name="ABA Live",
        currency="USD",
        telegram_group_id=-100800800,
        telegram_sender_id=800800,
        merchant_alias="LIVE STORE",
        static_khqr=valid_khqr_payload(account_id="live@aba", merchant_name="LIVE STORE"),
        enabled=True,
    )
    session_path = tmp_path / "khqr_collector.session"
    session_path.write_bytes(b"session")
    session_path.chmod(0o600)
    monkeypatch.setattr(
        dashboard_telegram,
        "session_location",
        lambda _value: (session_path, "khqr_collector", tmp_path),
    )

    response = client.post(
        "/dashboard/api/telegram/reset-account",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": "CHANGE TELEGRAM ACCOUNT"},
    )
    assert response.status_code == 409
    assert "disable every live payment source" in response.json()["detail"]
    assert session_path.exists()
