import json
from types import SimpleNamespace

from sqlalchemy import func, select

from app import core, models
from scripts import cutover_preflight as preflight


def safe_settings():
    return SimpleNamespace(
        telegram_shadow_only=True,
        allow_live_telegram=False,
        allow_shadow_promotion=False,
        telegram_session_name="runtime/khqr_collector",
    )


def create_staged_source(db):
    business, _api_key, _secret = core.create_business(
        db, "Cutover Test", "cutover-test"
    )
    return core.create_source(
        db,
        business.id,
        name="ABA staged",
        currency="USD",
        telegram_group_id=-100777,
        telegram_sender_id=777,
        merchant_alias="CUTOVER TEST",
        static_khqr="STATIC",
        enabled=False,
    )
def write_parity(path, rows):
    payload = {
        "shadow_rows": rows,
        "trx_found": rows,
        "amount_match": rows,
        "time_within_5s": rows,
        "source_group_match": rows,
        "source_generic_telegram": 0,
        "source_other": 0,
        "missing": 0,
        "max_time_delta_seconds": 0.0,
        "parity_pass": True,
    }
    path.write_text(json.dumps(payload))
    return path


def test_parity_passes_only_complete_artifact():
    good = {
        "shadow_rows": 3,
        "trx_found": 3,
        "amount_match": 3,
        "time_within_5s": 3,
        "source_group_match": 3,
        "missing": 0,
        "source_other": 0,
        "parity_pass": True,
    }
    assert preflight.parity_passes(good) is True
    good["amount_match"] = 2
    assert preflight.parity_passes(good) is False
def test_preflight_blocks_small_shadow_sample_without_state_change(
    db, tmp_path, monkeypatch
):
    source = create_staged_source(db)
    for index in range(3):
        core.ingest_evidence(
            db,
            source_id=source.id,
            transport="shadow-test",
            transport_message_id=f"msg-{index}",
            sender_id=777,
            raw_text=f"$1.0{index} paid. Trx. ID: 17890000000000{index}",
            initial_state="SHADOW",
        )

    session_path = tmp_path / "khqr_collector.session"
    session_path.write_bytes(b"session")
    session_path.chmod(0o600)
    monkeypatch.setattr(preflight, "get_settings", safe_settings)
    monkeypatch.setattr(
        preflight,
        "session_location",
        lambda _name: (session_path, "khqr_collector", tmp_path),
    )

    parity_path = write_parity(tmp_path / "parity.json", 3)
    errors = preflight.run_preflight(source.id, 10, parity_path)
    assert "insufficient SHADOW sample: 3 observed, 10 required" in errors
    assert "parity artifact sample is below the required SHADOW threshold" in errors

    db.expire_all()
    refreshed = db.get(models.PaymentSource, source.id)
    assert refreshed.enabled is False

    total = db.scalar(
        select(func.count(models.PaymentEvidence.id)).where(
            models.PaymentEvidence.source_id == source.id
        )
    )
    shadow = db.scalar(
        select(func.count(models.PaymentEvidence.id)).where(
            models.PaymentEvidence.source_id == source.id,
            models.PaymentEvidence.state == "SHADOW",
        )
    )
    allocations = db.scalar(select(func.count(models.PaymentAllocation.id))) or 0
    assert total == 3
    assert shadow == 3
    assert allocations == 0
