import logging
import time

from sqlalchemy import select

from .. import core, models
from ..db import SessionLocal


log = logging.getLogger("khqr.settlement")


def process_one() -> bool:
    db = SessionLocal()
    try:
        evidence = db.scalar(
            select(models.PaymentEvidence)
            .where(models.PaymentEvidence.state == "RECEIVED")
            .order_by(models.PaymentEvidence.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not evidence:
            db.rollback()
            return False
        evidence_id = evidence.id
        core.settle_evidence(db, evidence_id)
        return True
    except Exception:
        db.rollback()
        log.exception("settlement iteration failed")
        return False
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    while True:
        worked = process_one()
        if not worked:
            time.sleep(0.5)


if __name__ == "__main__":
    main()
