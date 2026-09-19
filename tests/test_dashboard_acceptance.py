from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import core, models
import app.dashboard_acceptance as acceptance
import app.khqr_asset as khqr_asset
from app.main import app
from tests.khqr_test_utils import khqr_png_bytes, valid_khqr_payload


client = TestClient(app)
SECRET = "test-internal-secret-32-characters"


def login():
    response = client.post(
        "/dashboard/api/login",
        json={"secret": SECRET},
    )
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
def create_disabled_source(db, monkeypatch, tmp_path):
    settings = khqr_asset.get_settings()
    monkeypatch.setattr(
        settings,
        "khqr_asset_root",
        str(tmp_path / "store-assets"),
    )
    business, _api_key, _secret = core.create_business(
        db,
        "Acceptance Store",
        "acceptance-store",
        "https://example.invalid/webhook",
    )
    payload = valid_khqr_payload(
        account_id="acceptance@aba",
        merchant_name="TEST STORE",
    )
    source = core.create_source(
        db,
        business.id,
        name="ABA Test",
        currency="USD",
        telegram_group_id=-100777001,
        telegram_sender_id=777001,
        merchant_alias="TEST STORE",
        static_khqr=payload,
        enabled=False,
    )
    original = khqr_png_bytes(
        account_id="acceptance@aba",
        merchant_name="TEST STORE",
    )
    asset = khqr_asset.save_uploaded_khqr(
        source.id,
        original,
        filename="merchant.png",
        media_type="image/png",
    )
    assert asset.payload == payload
    return business, source, original


def authorize_session(monkeypatch, tmp_path):
    session_path = tmp_path / "authorized.session"
    session_path.write_bytes(b"session")
    monkeypatch.setattr(
        acceptance,
        "_session_parts",
        lambda: (None, None, session_path, "x", tmp_path),
    )
    monkeypatch.setattr(
        acceptance,
        "_local_session_account_id",
        lambda path: 123,
    )
def test_acceptance_start_requires_explicit_confirmation(
    db, monkeypatch, tmp_path
):
    authorize_session(monkeypatch, tmp_path)
    _business, source, _image = create_disabled_source(
        db,
        monkeypatch,
        tmp_path,
    )
    csrf = login()
    response = client.post(
        "/dashboard/api/acceptance-tests/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "source_id": source.id,
            "amount_minor": 100,
            "confirm": "",
        },
    )
    assert response.status_code == 400


def test_acceptance_requires_uploaded_image(db, monkeypatch, tmp_path):
    authorize_session(monkeypatch, tmp_path)
    settings = khqr_asset.get_settings()
    monkeypatch.setattr(
        settings,
        "khqr_asset_root",
        str(tmp_path / "missing-assets"),
    )
    business, _api, _secret = core.create_business(
        db,
        "No Image Store",
        "no-image-store",
        None,
    )
    source = core.create_source(
        db,
        business.id,
        name="No Image",
        currency="USD",
        telegram_group_id=-100700001,
        telegram_sender_id=700001,
        merchant_alias="NO IMAGE",
        static_khqr=valid_khqr_payload(
            account_id="no-image@aba",
            merchant_name="NO IMAGE",
        ),
        enabled=False,
    )
    csrf = login()
    response = client.post(
        "/dashboard/api/acceptance-tests/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "source_id": source.id,
            "amount_minor": 100,
            "confirm": "SEND REAL TEST MONEY",
        },
    )
    assert response.status_code == 409
    assert "upload your store's static KHQR image" in response.json()["detail"]
def test_acceptance_real_evidence_stays_shadow_and_suppresses_side_effects(
    db, monkeypatch, tmp_path
):
    authorize_session(monkeypatch, tmp_path)
    _business, source, original_image = create_disabled_source(
        db,
        monkeypatch,
        tmp_path,
    )
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

    qr = client.get(
        f"/dashboard/api/acceptance-tests/{test['intent_id']}/qr.png"
    )
    assert qr.status_code == 200
    assert qr.content == original_image

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

    allocations = (
        db.scalar(select(func.count(models.PaymentAllocation.id))) or 0
    )
    outbox = db.scalar(select(func.count(models.WebhookOutbox.id))) or 0
    events = db.scalar(select(func.count(models.PaymentEvent.id))) or 0
    assert allocations == 0
    assert outbox == 0
    assert events == 0


def test_acceptance_cancel_releases_reservation(
    db, monkeypatch, tmp_path
):
    authorize_session(monkeypatch, tmp_path)
    _business, source, _image = create_disabled_source(
        db,
        monkeypatch,
        tmp_path,
    )
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


def test_acceptance_recovery_finds_exact_trx_and_stays_shadow(db, monkeypatch, tmp_path):
    authorize_session(monkeypatch, tmp_path)
    _business, source, _image = create_disabled_source(db, monkeypatch, tmp_path)
    csrf = login()
    started = client.post(
        "/dashboard/api/acceptance-tests/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "source_id": source.id,
            "amount_minor": 140,
            "confirm": "SEND REAL TEST MONEY",
        },
    ).json()

    amount = started["payable_amount_minor"] / 100
    trx = "178900000099777"
    message = aba_message(
        777,
        777001,
        f"{amount:.2f}",
        started["remark"],
        trx,
    )
    fake = FakeTelegramClient([message])
    telegram_session_path = tmp_path / "khqr_collector.session"
    telegram_session_path.write_bytes(b"session")

    async def fake_connect():
        fake.is_connected = True
        return fake, telegram_session_path, True, 123

    monkeypatch.setattr(acceptance, "_connect_authorized", fake_connect)

    recovered = client.post(
        f"/dashboard/api/acceptance-tests/{started['intent_id']}/recover",
        headers={"X-CSRF-Token": csrf},
        json={"trx_id": trx},
    )
    assert recovered.status_code == 200
    body = recovered.json()
    assert body["result"] == "VERIFIED"
    assert body["recovery_method"] == "trx_id"
    assert body["trx_tail"] == trx[-8:]

    evidence = db.scalar(
        select(models.PaymentEvidence).where(
            models.PaymentEvidence.source_id == source.id,
            models.PaymentEvidence.trx_id == trx,
        )
    )
    assert evidence is not None
    assert evidence.state == "SHADOW"
    assert (db.scalar(select(func.count(models.PaymentAllocation.id))) or 0) == 0
    assert (db.scalar(select(func.count(models.WebhookOutbox.id))) or 0) == 0


def test_acceptance_mismatch_can_be_cancelled(db, monkeypatch, tmp_path):
    authorize_session(monkeypatch, tmp_path)
    _business, source, _image = create_disabled_source(db, monkeypatch, tmp_path)
    csrf = login()
    started = client.post(
        "/dashboard/api/acceptance-tests/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "source_id": source.id,
            "amount_minor": 155,
            "confirm": "SEND REAL TEST MONEY",
        },
    ).json()

    amount = started["payable_amount_minor"] / 100
    wrong_remark_message = aba_message(
        778,
        777001,
        f"{amount:.2f}",
        "WRONGREMARK",
        "178900000099778",
    )
    fake = FakeTelegramClient([wrong_remark_message])
    telegram_session_path = tmp_path / "khqr_collector.session"
    telegram_session_path.write_bytes(b"session")

    async def fake_connect():
        fake.is_connected = True
        return fake, telegram_session_path, True, 123

    monkeypatch.setattr(acceptance, "_connect_authorized", fake_connect)
    mismatch = client.post(
        f"/dashboard/api/acceptance-tests/{started['intent_id']}/scan",
        headers={"X-CSRF-Token": csrf},
        json={"limit": 20},
    )
    assert mismatch.status_code == 200
    assert mismatch.json()["result"] == "MISMATCH"

    cancelled = client.post(
        f"/dashboard/api/acceptance-tests/{started['intent_id']}/cancel",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["result"] == "CANCELLED"


def test_expired_recovery_stays_expired_when_trx_still_mismatches(db, monkeypatch, tmp_path):
    authorize_session(monkeypatch, tmp_path)
    _business, source, _image = create_disabled_source(db, monkeypatch, tmp_path)
    csrf = login()
    started = client.post(
        "/dashboard/api/acceptance-tests/start",
        headers={"X-CSRF-Token": csrf},
        json={"source_id": source.id, "amount_minor": 165, "confirm": "SEND REAL TEST MONEY"},
    ).json()
    intent = db.get(models.PaymentIntent, started["intent_id"])
    acceptance._save_result(db, intent, result="EXPIRED", result_reason="match_window_expired")

    amount = started["payable_amount_minor"] / 100
    trx = "178900000099779"
    message = aba_message(779, 777001, f"{amount:.2f}", "WRONGREMARK", trx)
    fake = FakeTelegramClient([message])
    session_path = tmp_path / "khqr_collector.session"
    session_path.write_bytes(b"session")

    async def fake_connect():
        fake.is_connected = True
        return fake, session_path, True, 123

    monkeypatch.setattr(acceptance, "_connect_authorized", fake_connect)
    recovered = client.post(
        f"/dashboard/api/acceptance-tests/{started['intent_id']}/recover",
        headers={"X-CSRF-Token": csrf},
        json={"trx_id": trx},
    )
    assert recovered.status_code == 200
    body = recovered.json()
    assert body["result"] == "EXPIRED"
    assert body["result_reason"] == "unknown_or_wrong_remark"
    assert body["recovery_method"] == "trx_id"
