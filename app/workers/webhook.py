import json
import logging
import time
from datetime import timedelta

import httpx
from sqlalchemy import or_, select

from .. import models
from ..config import get_settings
from ..core import utcnow
from ..db import SessionLocal
from ..security import sign_webhook


log = logging.getLogger("khqr.webhook")


def _claim_one(db):
    now = utcnow()
    row = db.scalar(
        select(models.WebhookOutbox)
        .where(
            models.WebhookOutbox.next_attempt_at <= now,
            or_(
                models.WebhookOutbox.status == "PENDING",
                (models.WebhookOutbox.status == "IN_FLIGHT") & (models.WebhookOutbox.lease_until < now),
            ),
        )
        .order_by(models.WebhookOutbox.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )

    if not row:
        return None
    row.status = "IN_FLIGHT"
    row.lease_until = now + timedelta(seconds=30)
    db.commit()
    return row.id


def _payload(db, row):
    event = db.get(models.PaymentEvent, row.event_id)
    business = db.get(models.Business, row.business_id)
    if not event or not business:
        return None, None, None
    body = {
        "id": event.id,
        "type": event.event_type,
        "created_at": event.created_at.isoformat(),
        "data": json.loads(event.payload_json),
    }
    return event, business, json.dumps(body, separators=(",", ":"), sort_keys=True).encode()


def deliver_one() -> bool:
    settings = get_settings()
    db = SessionLocal()
    try:
        row_id = _claim_one(db)
        if not row_id:
            return False
        row = db.get(models.WebhookOutbox, row_id)
        _event, business, body = _payload(db, row)

        if not business or not business.webhook_url or not business.webhook_secret or body is None:
            row.status = "DEAD"
            row.last_error = "business webhook configuration missing"
            db.commit()
            return True

        timestamp = str(int(time.time()))
        signature = sign_webhook(business.webhook_secret, timestamp, body)
        headers = {
            "Content-Type": "application/json",
            "X-KHQR-Event-ID": row.event_id,
            "X-KHQR-Timestamp": timestamp,
            "X-KHQR-Signature": "v1=" + signature,
        }
        try:
            response = httpx.post(
                business.webhook_url,
                content=body,
                headers=headers,
                timeout=settings.webhook_timeout_seconds,
            )
            response.raise_for_status()
        except Exception as exc:
            row.attempts += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            row.lease_until = None

            if row.attempts >= settings.webhook_max_attempts:
                row.status = "DEAD"
            else:
                row.status = "PENDING"
                backoff = min(300, 2 ** min(row.attempts, 8))
                row.next_attempt_at = utcnow() + timedelta(seconds=backoff)
            db.commit()
            return True

        row.status = "SENT"
        row.attempts += 1
        row.lease_until = None
        row.last_error = None
        db.commit()
        return True
    except Exception:
        db.rollback()
        log.exception("webhook iteration failed")
        return False
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    while True:
        if not deliver_one():
            time.sleep(1)


if __name__ == "__main__":
    main()
