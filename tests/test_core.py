from datetime import timedelta

import pytest
from sqlalchemy import select

from app import core, models


def make_intent(db, business, source, index=1, amount=500):
    return core.create_payment_intent(
        db,
        business,
        source_id=source.id,
        external_id=f"order-{index}",
        idempotency_key=f"idem-{index}",
        amount_minor=amount,
        currency="USD",
        metadata={"index": index},
    )


def test_intent_idempotency(db, business_and_source):
    business, source, _ = business_and_source
    intent1, request1 = make_intent(db, business, source)
    intent2, request2 = make_intent(db, business, source)
    assert intent1.id == intent2.id
    assert request1.id == request2.id


def test_idempotency_conflict_on_different_amount(db, business_and_source):
    business, source, _ = business_and_source
    make_intent(db, business, source)
    with pytest.raises(core.Conflict):
        core.create_payment_intent(
            db,
            business,
            source_id=source.id,
            external_id="order-1",
            idempotency_key="idem-1",
            amount_minor=501,
            currency="USD",
            metadata={},
        )


def test_thirty_dual_slots_then_remark_overflow(db, business_and_source):
    business, source, _ = business_and_source
    requests = [make_intent(db, business, source, i, 500)[1] for i in range(35)]
    dual = [request for request in requests if request.mode == "DUAL"]
    overflow = [request for request in requests if request.mode == "REMARK_PRIMARY"]
    assert len(dual) == 30
    assert len(overflow) == 5
    assert sorted(request.payable_amount_minor for request in dual) == list(range(500, 530))


def test_exact_remark_supports_partial_then_full_payment(db, business_and_source):
    business, source, _ = business_and_source
    intent, request = make_intent(db, business, source, amount=500)

    first = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="m1",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 1111111111\nUSD 2.00",
        trx_id="1111111111",
        amount_minor=200,
        remark=request.remark,
    )
    updated = core.settle_evidence(db, first.id)
    assert updated.status == "PARTIALLY_PAID"
    assert updated.paid_minor == 200

    second = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="m2",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 2222222222\nUSD 3.00",
        trx_id="2222222222",
        amount_minor=300,
        remark=request.remark,
    )

    updated = core.settle_evidence(db, second.id)
    assert updated.status == "PAID"
    assert updated.paid_minor == 500
    assert updated.excess_minor == 0


def test_unique_amount_match_without_remark(db, business_and_source):
    business, source, _ = business_and_source
    intent, request = make_intent(db, business, source, amount=750)
    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="amount-only",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 3333333333\nUSD 7.50",
        trx_id="3333333333",
        amount_minor=request.payable_amount_minor,
    )
    updated = core.settle_evidence(db, evidence.id)
    assert updated.id == intent.id
    assert updated.status == "PAID"


def test_amount_only_rejected_after_match_window(db, business_and_source):
    business, source, _ = business_and_source
    intent, request = make_intent(db, business, source, amount=900)
    request.match_expires_at = core.utcnow() - timedelta(seconds=1)
    db.commit()

    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="late-amount",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 4444444444\nUSD 9.00",
        trx_id="4444444444",
        amount_minor=request.payable_amount_minor,
    )
    assert core.settle_evidence(db, evidence.id) is None
    db.refresh(evidence)
    assert evidence.state == "UNMATCHED"
    assert evidence.quarantine_reason == "no_safe_match"
    db.refresh(intent)
    assert intent.status == "PENDING"


def test_wrong_or_missing_telegram_sender_is_rejected(db, business_and_source):
    _business, source, _ = business_and_source
    for sender in (None, 999999):
        with pytest.raises(core.Conflict):
            core.ingest_evidence(
                db,
                source_id=source.id,
                transport="telegram",
                transport_message_id=f"bad-{sender}",
                sender_id=sender,
                raw_text="Transaction ID: 5555555555\nUSD 1.00",
                trx_id="5555555555",
                amount_minor=100,
            )


def test_edited_transport_message_quarantines_evidence(db, business_and_source):
    _business, source, _ = business_and_source
    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="same-message",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 6666666666\nUSD 1.00",
        trx_id="6666666666",
        amount_minor=100,
    )

    with pytest.raises(core.Conflict):
        core.ingest_evidence(
            db,
            source_id=source.id,
            transport="telegram",
            transport_message_id="same-message",
            sender_id=source.telegram_sender_id,
            raw_text="Transaction ID: 6666666666\nUSD 9.00",
            trx_id="6666666666",
            amount_minor=900,
        )
    db.refresh(evidence)
    assert evidence.state == "QUARANTINED"
    assert evidence.quarantine_reason == "edited_transport_message"


def test_same_trx_id_is_allowed_on_different_sources(db, business_and_source):
    business, source1, _ = business_and_source
    source2 = core.create_source(
        db,
        business.id,
        name="ABA Backup",
        currency="USD",
        telegram_group_id=-1001234567891,
        telegram_sender_id=434343,
        merchant_alias="TEST STORE 2",
        static_khqr="STATIC2",
        enabled=True,
    )
    first = core.ingest_evidence(
        db, source_id=source1.id, transport="telegram",
        transport_message_id="s1", sender_id=source1.telegram_sender_id,
        raw_text="Transaction ID: 7777777777\nUSD 1.00",
        trx_id="7777777777", amount_minor=100,
    )
    second = core.ingest_evidence(
        db, source_id=source2.id, transport="telegram",
        transport_message_id="s2", sender_id=source2.telegram_sender_id,
        raw_text="Transaction ID: 7777777777\nUSD 1.00",
        trx_id="7777777777", amount_minor=100,
    )
    assert first.id != second.id


def test_remark_amount_conflict_fails_closed(db, business_and_source):
    business, source, _ = business_and_source
    _intent1, request1 = make_intent(db, business, source, 1, 500)
    _intent2, request2 = make_intent(db, business, source, 2, 500)
    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="conflict",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 8888888888\nUSD 5.01",
        trx_id="8888888888",
        amount_minor=request2.payable_amount_minor,
        remark=request1.remark,
    )
    assert core.settle_evidence(db, evidence.id) is None
    db.refresh(evidence)
    assert evidence.state == "QUARANTINED"
    assert evidence.quarantine_reason == "remark_amount_conflict"


def test_trx_recovery_allocates_unmatched_evidence(db, business_and_source):
    business, source, _ = business_and_source
    intent, request = make_intent(db, business, source, amount=1200)
    request.match_expires_at = core.utcnow() - timedelta(seconds=1)
    db.commit()

    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="recovery",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 9999999999\nUSD 12.00",
        trx_id="9999999999",
        amount_minor=request.payable_amount_minor,
    )
    assert core.settle_evidence(db, evidence.id) is None
    recovered = core.recover_by_trx(
        db, business, intent.id, evidence.trx_id, actor="test-suite"
    )
    assert recovered.status == "PAID"
    claims = list(db.scalars(select(models.PaymentClaim)))
    assert len(claims) == 1
    assert claims[0].claim_type == "TRX_ID_RECOVERY"


def test_overpayment_is_explicit(db, business_and_source):
    business, source, _ = business_and_source
    intent, request = make_intent(db, business, source, amount=500)

    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="overpay",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 1212121212\nUSD 6.00",
        trx_id="1212121212",
        amount_minor=600,
        remark=request.remark,
    )
    updated = core.settle_evidence(db, evidence.id)
    assert updated.status == "PAID"
    assert updated.paid_minor == 600
    assert updated.excess_minor == 100


def test_webhook_outbox_is_created_for_business_events(db):
    business, _key, secret = core.create_business(
        db, "Webhook Store", "webhook-store", "https://example.invalid/hook"
    )
    source = core.create_source(
        db, business.id, name="ABA", currency="USD",
        telegram_group_id=-10022, telegram_sender_id=22,
        merchant_alias="WEBHOOK", static_khqr="STATIC", enabled=True,
    )
    make_intent(db, business, source, amount=250)
    assert secret
    assert db.scalar(select(models.WebhookOutbox)) is not None


def test_shadow_evidence_cannot_settle_until_promoted(db, business_and_source):
    business, source, _ = business_and_source
    intent, request = make_intent(db, business, source, amount=141)
    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="shadow-message",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 1313131313\nUSD 1.41",
        trx_id="1313131313",
        amount_minor=141,
        remark=request.remark,
        initial_state="SHADOW",
    )
    assert evidence.state == "SHADOW"
    assert core.settle_evidence(db, evidence.id) is None
    db.refresh(intent)
    assert intent.status == "PENDING"

    with pytest.raises(core.Conflict):
        core.recover_by_trx(db, business, intent.id, evidence.trx_id, actor="shadow-test")

    promoted = core.promote_shadow_evidence(db, source.id, limit=10)
    assert [row.id for row in promoted] == [evidence.id]
    updated = core.settle_evidence(db, evidence.id)
    db.refresh(updated)
    assert updated.status == "PAID"
    assert updated.paid_minor == 141


def test_nonzero_offset_is_required_not_excess(db, business_and_source):
    business, source, _ = business_and_source
    _first_intent, first_request = make_intent(db, business, source, index=101, amount=500)
    intent, request = make_intent(db, business, source, index=102, amount=500)

    assert first_request.offset_minor == 0
    assert request.offset_minor == 1
    assert request.payable_amount_minor == 501

    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="offset-required",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 1414141414\nUSD 5.01",
        trx_id="1414141414",
        amount_minor=501,
        remark=request.remark,
    )
    updated = core.settle_evidence(db, evidence.id)
    assert updated.id == intent.id
    assert updated.status == "PAID"
    assert updated.paid_minor == 501
    assert updated.excess_minor == 0


def test_split_payment_must_reach_full_offset_amount(db, business_and_source):
    business, source, _ = business_and_source
    make_intent(db, business, source, index=111, amount=500)
    intent, request = make_intent(db, business, source, index=112, amount=500)
    assert request.payable_amount_minor == 501

    first = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="offset-split-1",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 1515151515\nUSD 4.00",
        trx_id="1515151515",
        amount_minor=400,
        remark=request.remark,
    )
    updated = core.settle_evidence(db, first.id)
    assert updated.status == "PARTIALLY_PAID"
    assert updated.paid_minor == 400
    assert updated.excess_minor == 0

    second = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram",
        transport_message_id="offset-split-2",
        sender_id=source.telegram_sender_id,
        raw_text="Transaction ID: 1616161616\nUSD 1.01",
        trx_id="1616161616",
        amount_minor=101,
        remark=request.remark,
    )
    updated = core.settle_evidence(db, second.id)
    assert updated.status == "PAID"
    assert updated.paid_minor == 501
    assert updated.excess_minor == 0
