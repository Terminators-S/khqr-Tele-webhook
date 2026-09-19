from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import core, models
from app.main import app
import app.dashboard_acceptance as acceptance


client = TestClient(app)
SECRET = "test-internal-secret-32-characters"


def login():
    response = client.post("/dashboard/api/login", json={"secret": SECRET})
    assert response.status_code == 200
    return response.json()["csrf_token"]


class FakeTelegramClient:
    def __init__(self, messages):
        self.messages = messages
        self.is_connected = True
    async def disconnect(self):
        self.is_connected = False

    def get_dialogs(self, limit=500):
        async def rows():
            if False:
                yield None
        return rows()

    def get_chat_history(self, chat_id, limit=120):
        async def rows():
            for message in self.messages[:limit]:
                yield message
        return rows()


def aba_message(message_id, sender_id, amount, remark, trx):
    return SimpleNamespace(
        id=message_id,
        from_user=SimpleNamespace(id=sender_id),
        sender_chat=None,
        text=(
            "$" + amount + " paid by TEST USER (*001) "
            "via ABA PAY at TEST STORE. Remark: " + remark
            + ". Trx. ID: " + trx + ", APV: 123456."
        ),
        caption=None,
        date=datetime.now(timezone.utc),
    )
def create_disabled_source(db):
    business, _api_key, _secret = core.create_business(
        db,
        "Acceptance Store",
        "acceptance-store",
        "https://example.invalid/webhook",
    )
    source = core.create_source(
        db,
        business.id,
        name="ABA Test",
        currency="USD",
        telegram_group_id=-100777001,
        telegram_sender_id=777001,
        merchant_alias="TEST STORE",
        static_khqr="000201010211TESTSTATIC6304ABCD",
        enabled=False,
    )
    return business, source


def test_acceptance_start_requires_explicit_confirmation(db):
    _business, source = create_disabled_source(db)
    csrf = login()
    response = client.post(
        "/dashboard/api/acceptance-tests/start",
        headers={"X-CSRF-Token": csrf},
        json={"source_id": source.id, "amount_minor": 100, "confirm": ""},
    )
    assert response.status_code == 400
def test_acceptance_real_evidence_stays_shadow_and_suppresses_side_effects(
    db, monkeypatch, tmp_path
):
    session_path = tmp_path / "authorized.session"
    session_path.write_bytes(b"session")
    monkeypatch.setattr(acceptance, "_session_parts", lambda: (None, None, session_path, "x", tmp_path))
    monkeypatch.setattr(acceptance, "_local_session_account_id", lambda path: 123)
    _business, source = create_disabled_source(db)
    csrf = login()

    started = client.post(
        "/dashboard/api/acceptance-tests/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "source_id": source.id,
            "amount_minor": 100,
            "confirm": "SEND REAL TEST MONEY",
        },
    )
    assert started.status_code == 200
    test = started.json()
    assert test["source_enabled"] is False
    assert test["webhook_suppressed"] is True
    assert test["payable_amount_minor"] >= 100
    assert test["remark"].startswith("KQ")

    amount = test["payable_amount_minor"] / 100
    message = aba_message(
        501,
        777001,
        f"{amount:.2f}",
        test["remark"],
        "178900000099001",
    )
    fake = FakeTelegramClient([message])
    telegram_session_path = tmp_path / "khqr_collector.session"
    telegram_session_path.write_bytes(b"session")

    async def fake_connect():
        fake.is_connected = True
        return fake, telegram_session_path, True, 123

    monkeypatch.setattr(acceptance, "_connect_authorized", fake_connect)

    scanned = client.post(
        "/dashboard/api/acceptance-tests/"
        + test["intent_id"]
        + "/scan",
        headers={"X-CSRF-Token": csrf},
        json={"limit": 20},
    )
    assert scanned.status_code == 200
    result = scanned.json()
    assert result["result"] == "VERIFIED"
    assert result["would_status"] == "PAID"
    assert result["would_excess_minor"] == 0
    assert result["match_reason"] == "remark"
    assert result["source_enabled"] is False

    db.expire_all()
    source_row = db.get(models.PaymentSource, source.id)
    assert source_row.enabled is False
    evidence = db.scalar(
        select(models.PaymentEvidence).where(
            models.PaymentEvidence.source_id == source.id,
            models.PaymentEvidence.trx_id == "178900000099001",
        )
    )
    assert evidence is not None
    assert evidence.state == "SHADOW"

    allocations = db.scalar(select(func.count(models.PaymentAllocation.id))) or 0
    outbox = db.scalar(select(func.count(models.WebhookOutbox.id))) or 0
    events = db.scalar(select(func.count(models.PaymentEvent.id))) or 0
    assert allocations == 0
    assert outbox == 0
    assert events == 0


def test_acceptance_cancel_releases_reservation(db, monkeypatch, tmp_path):
    session_path = tmp_path / "authorized.session"
    session_path.write_bytes(b"session")
    monkeypatch.setattr(acceptance, "_session_parts", lambda: (None, None, session_path, "x", tmp_path))
    monkeypatch.setattr(acceptance, "_local_session_account_id", lambda path: 123)
    _business, source = create_disabled_source(db)
    csrf = login()
    started = client.post(
        "/dashboard/api/acceptance-tests/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "source_id": source.id,
            "amount_minor": 125,
            "confirm": "SEND REAL TEST MONEY",
        },
    ).json()
    before = db.scalar(
        select(func.count(models.AmountReservation.id)).where(
            models.AmountReservation.source_id == source.id
        )
    )
    assert before == 1

    cancelled = client.post(
        "/dashboard/api/acceptance-tests/"
        + started["intent_id"]
        + "/cancel",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["result"] == "CANCELLED"

    db.expire_all()
    after = db.scalar(
        select(func.count(models.AmountReservation.id)).where(
            models.AmountReservation.source_id == source.id
        )
    )
    assert after == 0
