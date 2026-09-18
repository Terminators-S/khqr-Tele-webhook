from concurrent.futures import ThreadPoolExecutor
from collections import Counter

from sqlalchemy import delete, func, select

from app import core, models
from app.db import SessionLocal, engine
from app.workers import settlement


if engine.dialect.name != "postgresql":
    raise SystemExit("This gate requires PostgreSQL.")


def reset_database():
    db = SessionLocal()
    try:
        for table in reversed(models.Base.metadata.sorted_tables):
            db.execute(delete(table))
        db.commit()
    finally:
        db.close()


def setup_source():
    db = SessionLocal()
    try:
        business, _key, _secret = core.create_business(db, "Gate Store", "gate-store")
        source = core.create_source(
            db, business.id, name="ABA Gate", currency="USD",
            telegram_group_id=-1009001, telegram_sender_id=9001,
            merchant_alias="GATE", static_khqr="STATIC-GATE", enabled=True,
        )
        return business.id, source.id
    finally:
        db.close()


def allocate_one(args):
    business_id, source_id, index = args
    db = SessionLocal()
    try:
        business = db.get(models.Business, business_id)
        intent, request = core.create_payment_intent(
            db,
            business,
            source_id=source_id,
            external_id=f"concurrent-{index}",
            idempotency_key=f"concurrent-idem-{index}",
            amount_minor=500,
            currency="USD",
            metadata={"gate": index},
        )
        return intent.id, request.mode, request.payable_amount_minor
    finally:
        db.close()


def allocator_gate(business_id, source_id):
    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(
            pool.map(
                allocate_one,
                [(business_id, source_id, i) for i in range(100)],
            )
        )
    modes = Counter(result[1] for result in results)
    dual_amounts = sorted(result[2] for result in results if result[1] == "DUAL")
    assert modes["DUAL"] == 30, modes
    assert modes["REMARK_PRIMARY"] == 70, modes
    assert dual_amounts == list(range(500, 530)), dual_amounts
    print("ALLOCATOR_GATE PASS dual=30 overflow=70")


def duplicate_settlement_gate(business_id, source_id):
    db = SessionLocal()
    try:
        business = db.get(models.Business, business_id)
        intent, request = core.create_payment_intent(
            db, business, source_id=source_id,
            external_id="settle-race", idempotency_key="settle-race",
            amount_minor=1234, currency="USD", metadata={},
        )
        evidence = core.ingest_evidence(
            db, source_id=source_id, transport="telegram",
            transport_message_id="settle-race-message", sender_id=9001,
            raw_text="Transaction ID: 12345678901234\nUSD 12.34",
            trx_id="12345678901234", amount_minor=1234, remark=request.remark,
        )
        intent_id, evidence_id = intent.id, evidence.id
    finally:
        db.close()

    def settle_once(_):
        session = SessionLocal()
        try:
            result = core.settle_evidence(session, evidence_id)
            return result.status if result else None
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(settle_once, range(16)))

    db = SessionLocal()
    try:
        intent = db.get(models.PaymentIntent, intent_id)
        allocation_count = db.scalar(
            select(func.count(models.PaymentAllocation.id)).where(
                models.PaymentAllocation.evidence_id == evidence_id
            )
        )
        assert intent.status == "PAID"
        assert intent.paid_minor == 1234
        assert allocation_count == 1
        assert all(outcome == "PAID" for outcome in outcomes)
        print("EVIDENCE_LOCK_GATE PASS winners=1 allocations=1")
    finally:
        db.close()


def seed_worker_evidence(business_id, source_id, count=40):
    db = SessionLocal()
    try:
        business = db.get(models.Business, business_id)
        for i in range(count):
            intent, request = core.create_payment_intent(
                db, business, source_id=source_id,
                external_id=f"worker-{i}", idempotency_key=f"worker-idem-{i}",
                amount_minor=2000 + i * 100, currency="USD", metadata={},
            )

            core.ingest_evidence(
                db, source_id=source_id, transport="telegram",
                transport_message_id=f"worker-message-{i}", sender_id=9001,
                raw_text=f"Transaction ID: {20000000000000 + i}\nUSD {(2000 + i * 100) / 100:.2f}",
                trx_id=str(20000000000000 + i),
                amount_minor=2000 + i * 100,
                remark=request.remark,
            )
    finally:
        db.close()


def worker_gate():
    def consume(worker_id):
        processed = 0
        for _ in range(200):
            if settlement.process_one():
                processed += 1
                continue
            session = SessionLocal()
            try:
                remaining = session.scalar(
                    select(func.count(models.PaymentEvidence.id)).where(
                        models.PaymentEvidence.state == "RECEIVED"
                    )
                )
            finally:
                session.close()
            if not remaining:
                return processed
        return processed

    with ThreadPoolExecutor(max_workers=8) as pool:
        counts = list(pool.map(consume, range(8)))

    db = SessionLocal()
    try:
        allocated = db.scalar(
            select(func.count(models.PaymentEvidence.id)).where(
                models.PaymentEvidence.state == "ALLOCATED"
            )
        )
        assert allocated >= 41
        worker_allocations = db.scalar(
            select(func.count(models.PaymentAllocation.id)).where(
                models.PaymentAllocation.amount_minor >= 2000
            )
        )
        assert worker_allocations == 40
        print(f"SKIP_LOCKED_GATE PASS processed=40 workers={counts}")
    finally:
        db.close()


def main():
    reset_database()
    business_id, source_id = setup_source()
    allocator_gate(business_id, source_id)
    duplicate_settlement_gate(business_id, source_id)
    seed_worker_evidence(business_id, source_id)
    worker_gate()
    print("POSTGRES_CONCURRENCY_GATE PASS")


if __name__ == "__main__":
    main()
