import json
import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .parser import evidence_fingerprint, parse_aba_text
from .security import generate_api_key, generate_webhook_secret, hash_secret


class PaymentCoreError(Exception):
    pass


class NotFound(PaymentCoreError):
    pass


class Conflict(PaymentCoreError):
    pass


REMARK_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def source_configured(source: models.PaymentSource) -> bool:
    return bool(
        source.telegram_group_id is not None
        and source.telegram_sender_id is not None
        and (source.merchant_alias or "").strip()
        and (source.static_khqr or "").strip()
    )


def source_ready(source: models.PaymentSource) -> bool:
    return bool(source.enabled and source_configured(source))


def create_business(db: Session, name: str, slug: str, webhook_url: str | None = None):
    api_key = generate_api_key()
    webhook_secret = generate_webhook_secret() if webhook_url else None
    business = models.Business(
        name=name.strip(),
        slug=slug.strip().lower(),
        api_key_hash=hash_secret(api_key),
        webhook_url=webhook_url,
        webhook_secret=webhook_secret,
    )
    db.add(business)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("business slug already exists") from exc
    db.refresh(business)
    return business, api_key, webhook_secret


def rotate_business_api_key(db: Session, business_id: str) -> tuple[models.Business, str]:
    business = db.get(models.Business, business_id)
    if not business:
        raise NotFound("business not found")
    api_key = generate_api_key()
    business.api_key_hash = hash_secret(api_key)
    db.commit()
    db.refresh(business)
    return business, api_key


def rotate_business_webhook_secret(db: Session, business_id: str) -> tuple[models.Business, str]:
    business = db.get(models.Business, business_id)
    if not business:
        raise NotFound("business not found")
    if not business.webhook_url:
        raise Conflict("configure a webhook URL before rotating its signing secret")
    webhook_secret = generate_webhook_secret()
    business.webhook_secret = webhook_secret
    db.commit()
    db.refresh(business)
    return business, webhook_secret


def create_source(db: Session, business_id: str, **values):
    business = db.get(models.Business, business_id)
    if not business:
        raise NotFound("business not found")
    source = models.PaymentSource(business_id=business_id, **values)
    if source.enabled and not source_ready(source):
        raise Conflict("enabled source requires Telegram group/sender, merchant alias, and static KHQR")
    db.add(source)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("payment source conflicts with existing configuration") from exc
    db.refresh(source)
    return source


def configure_source_payment(
    db: Session,
    source_id: str,
    *,
    merchant_alias: str,
    static_khqr: str,
    currency: str,
) -> models.PaymentSource:
    source = db.get(models.PaymentSource, source_id)
    if not source:
        raise NotFound("payment source not found")
    if source.enabled:
        raise Conflict("disable payment source before changing payment identity")
    source.merchant_alias = merchant_alias.strip()
    source.static_khqr = static_khqr.strip()
    source.currency = currency.strip().upper()
    db.commit()
    db.refresh(source)
    return source


def configure_source_group(
    db: Session,
    source_id: str,
    telegram_group_id: int,
) -> models.PaymentSource:
    source = db.get(models.PaymentSource, source_id)
    if not source:
        raise NotFound("payment source not found")
    if source.enabled:
        raise Conflict("disable payment source before changing Telegram group")
    if source.telegram_group_id != telegram_group_id:
        source.telegram_sender_id = None
    source.telegram_group_id = telegram_group_id
    db.commit()
    db.refresh(source)
    return source


def configure_source_sender(
    db: Session,
    source_id: str,
    telegram_sender_id: int,
) -> models.PaymentSource:
    source = db.get(models.PaymentSource, source_id)
    if not source:
        raise NotFound("payment source not found")
    if source.enabled:
        raise Conflict("disable payment source before changing Telegram sender")
    source.telegram_sender_id = telegram_sender_id
    db.commit()
    db.refresh(source)
    return source


def set_source_enabled(
    db: Session,
    source_id: str,
    enabled: bool,
) -> models.PaymentSource:
    source = db.get(models.PaymentSource, source_id)
    if not source:
        raise NotFound("payment source not found")
    if enabled and not source_configured(source):
        raise Conflict("payment source configuration is incomplete")
    source.enabled = enabled
    db.commit()
    db.refresh(source)
    return source


def business_for_api_key(db: Session, api_key: str) -> models.Business:
    business = db.scalar(
        select(models.Business).where(
            models.Business.api_key_hash == hash_secret(api_key),
            models.Business.is_active.is_(True),
        )
    )
    if not business:
        raise NotFound("invalid API key")
    return business


def _normalize_remark_prefix(prefix: str | None) -> str:
    value = (prefix or "KQ").strip().upper()
    allowed = set(string.ascii_uppercase + string.digits)
    if not (2 <= len(value) <= 6) or any(char not in allowed for char in value):
        raise Conflict("remark prefix must be 2-6 ASCII letters or digits")
    return value


def _new_remark(db: Session, source_id: str, prefix: str | None = None) -> str:
    normalized = _normalize_remark_prefix(prefix)
    for _ in range(24):
        suffix = "".join(secrets.choice(REMARK_ALPHABET) for _ in range(5))
        remark = normalized + suffix
        exists = db.scalar(
            select(models.PaymentRequest.id).where(
                models.PaymentRequest.source_id == source_id,
                models.PaymentRequest.remark == remark,
            )
        )
        if not exists:
            return remark
    raise Conflict("could not allocate unique payment remark")


def _purge_expired_reservations(db: Session, now: datetime) -> None:
    db.execute(delete(models.AmountReservation).where(models.AmountReservation.reserved_until < now))


def _event(db: Session, business: models.Business, intent: models.PaymentIntent, event_type: str, payload: dict):
    event = models.PaymentEvent(
        business_id=business.id,
        intent_id=intent.id,
        event_type=event_type,
        payload_json=json.dumps(payload, separators=(",", ":"), sort_keys=True),
    )
    db.add(event)
    db.flush()
    if business.webhook_url:
        db.add(models.WebhookOutbox(event_id=event.id, business_id=business.id))
    return event


def _existing_intent_matches(intent: models.PaymentIntent, source_id: str, amount_minor: int, currency: str) -> bool:
    return (
        intent.source_id == source_id
        and intent.base_amount_minor == amount_minor
        and intent.currency == currency
    )


def _existing_intent(db: Session, business_id: str, idempotency_key: str, external_id: str):
    by_key = db.scalar(
        select(models.PaymentIntent).where(
            models.PaymentIntent.business_id == business_id,
            models.PaymentIntent.idempotency_key == idempotency_key,
        )
    )
    if by_key:
        return by_key
    return db.scalar(
        select(models.PaymentIntent).where(
            models.PaymentIntent.business_id == business_id,
            models.PaymentIntent.external_id == external_id,
        )
    )


def get_request_for_intent(db: Session, intent_id: str) -> models.PaymentRequest:
    request = db.scalar(select(models.PaymentRequest).where(models.PaymentRequest.intent_id == intent_id))
    if not request:
        raise NotFound("payment request not found")
    return request


def create_payment_intent(
    db: Session,
    business: models.Business,
    *,
    source_id: str,
    external_id: str,
    idempotency_key: str,
    amount_minor: int,
    currency: str,
    metadata: dict,
    remark_prefix: str | None = None,
):
    currency = currency.strip().upper()
    existing = _existing_intent(db, business.id, idempotency_key, external_id)
    if existing:
        if not _existing_intent_matches(existing, source_id, amount_minor, currency):
            raise Conflict("idempotency/external reference already exists with different payment data")
        return existing, get_request_for_intent(db, existing.id)

    source = db.get(models.PaymentSource, source_id)
    if not source or source.business_id != business.id:
        raise NotFound("payment source not found")
    if not source_ready(source):
        raise Conflict("payment source is not ready")
    if source.currency.upper() != currency:
        raise Conflict("currency does not match payment source")

    settings = get_settings()
    now = utcnow()
    checkout_expires = now + timedelta(seconds=settings.checkout_ttl_seconds)
    match_expires = checkout_expires + timedelta(seconds=settings.late_match_grace_seconds)
    history_expires = now + timedelta(seconds=settings.history_recovery_seconds)

    intent = models.PaymentIntent(
        business_id=business.id,
        source_id=source.id,
        external_id=external_id,
        idempotency_key=idempotency_key,
        base_amount_minor=amount_minor,
        currency=currency,
        metadata_json=json.dumps(metadata, separators=(",", ":"), sort_keys=True),
        checkout_expires_at=checkout_expires,
        history_expires_at=history_expires,
    )
    request = models.PaymentRequest(
        intent_id=intent.id,
        source_id=source.id,
        remark=_new_remark(db, source.id, remark_prefix),
        mode="REMARK_PRIMARY",
        offset_minor=None,
        payable_amount_minor=amount_minor,
        checkout_expires_at=checkout_expires,
        match_expires_at=match_expires,
    )

    try:
        db.add(intent)
        db.flush()
        request.intent_id = intent.id
        db.add(request)
        db.flush()
        _purge_expired_reservations(db, now)

        for offset_minor in range(settings.offset_max_minor + 1):
            payable = amount_minor + offset_minor
            try:
                with db.begin_nested():
                    reservation = models.AmountReservation(
                        request_id=request.id,
                        source_id=source.id,
                        currency=currency,
                        base_amount_minor=amount_minor,
                        payable_amount_minor=payable,
                        offset_minor=offset_minor,
                        reserved_until=match_expires,
                    )
                    db.add(reservation)
                    db.flush()
            except IntegrityError:
                continue
            request.mode = "DUAL"
            request.offset_minor = offset_minor
            request.payable_amount_minor = payable
            break

        _event(
            db,
            business,
            intent,
            "payment.intent.created",
            {
                "intent_id": intent.id,
                "external_id": intent.external_id,
                "amount_minor": intent.base_amount_minor,
                "payable_amount_minor": request.payable_amount_minor,
                "currency": intent.currency,
                "remark": request.remark,
                "mode": request.mode,
                "checkout_expires_at": checkout_expires.isoformat(),
                "match_expires_at": match_expires.isoformat(),
            },
        )
        db.commit()
        db.refresh(intent)
        db.refresh(request)
        return intent, request
    except IntegrityError as exc:
        db.rollback()
        winner = _existing_intent(db, business.id, idempotency_key, external_id)
        if winner and _existing_intent_matches(winner, source_id, amount_minor, currency):
            return winner, get_request_for_intent(db, winner.id)
        raise Conflict("payment-intent creation conflicted with another request") from exc


def ingest_evidence(
    db: Session,
    *,
    source_id: str,
    transport: str,
    transport_message_id: str,
    sender_id: int | None,
    raw_text: str,
    received_at: datetime | None = None,
    trx_id: str | None = None,
    amount_minor: int | None = None,
    currency: str = "USD",
    remark: str | None = None,
    initial_state: str = "RECEIVED",
):
    if initial_state not in {"RECEIVED", "SHADOW"}:
        raise Conflict("invalid evidence initial state")
    source = db.get(models.PaymentSource, source_id)
    if not source:
        raise NotFound("payment source not found")
    if transport.startswith("telegram") and source.telegram_sender_id is not None and sender_id != source.telegram_sender_id:
        raise Conflict("evidence sender does not match payment source")

    parsed = parse_aba_text(raw_text)
    trx_id = (trx_id or parsed.trx_id or "").strip()
    amount_minor = amount_minor if amount_minor is not None else parsed.amount_minor
    remark = (remark or parsed.remark or "").strip().upper() or None
    currency = (currency or parsed.currency).strip().upper()
    if not trx_id or amount_minor is None or amount_minor <= 0:
        raise Conflict("evidence must contain a transaction ID and positive amount")

    received = as_utc(received_at or utcnow())
    fingerprint = evidence_fingerprint(source_id, transport, transport_message_id, raw_text)
    existing_message = db.scalar(
        select(models.PaymentEvidence).where(
            models.PaymentEvidence.source_id == source_id,
            models.PaymentEvidence.transport == transport,
            models.PaymentEvidence.transport_message_id == transport_message_id,
        )
    )
    if existing_message:
        if existing_message.fingerprint_hash != fingerprint:
            existing_message.quarantine_reason = "edited_transport_message"
            if existing_message.state != "ALLOCATED":
                existing_message.state = "QUARANTINED"
            db.commit()
            raise Conflict("transport message changed after first observation")
        return existing_message

    existing_trx = db.scalar(
        select(models.PaymentEvidence).where(
            models.PaymentEvidence.source_id == source_id,
            models.PaymentEvidence.trx_id == trx_id,
        )
    )
    if existing_trx:
        immutable_same = (
            existing_trx.source_id == source_id
            and existing_trx.amount_minor == amount_minor
            and existing_trx.currency == currency
        )
        if not immutable_same:
            raise Conflict("transaction ID replay conflicts with immutable evidence")
        return existing_trx

    evidence = models.PaymentEvidence(
        business_id=source.business_id,
        source_id=source_id,
        transport=transport,
        transport_message_id=transport_message_id,
        trx_id=trx_id,
        amount_minor=amount_minor,
        currency=currency,
        remark=remark,
        raw_text=raw_text,
        fingerprint_hash=fingerprint,
        received_at=received,
        state=initial_state,
    )
    db.add(evidence)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = db.scalar(
            select(models.PaymentEvidence).where(
                models.PaymentEvidence.source_id == source_id,
                models.PaymentEvidence.trx_id == trx_id,
            )
        )
        if existing and existing.source_id == source_id and existing.amount_minor == amount_minor:
            return existing
        raise Conflict("evidence raced with a conflicting insert") from exc
    db.refresh(evidence)
    return evidence


def _remark_candidate(db: Session, evidence: models.PaymentEvidence):
    if not evidence.remark:
        return None
    received = as_utc(evidence.received_at)
    stmt = (
        select(models.PaymentRequest, models.PaymentIntent)
        .join(models.PaymentIntent, models.PaymentIntent.id == models.PaymentRequest.intent_id)
        .where(
            models.PaymentRequest.source_id == evidence.source_id,
            models.PaymentRequest.remark == evidence.remark,
            models.PaymentRequest.created_at <= received + timedelta(seconds=60),
            models.PaymentIntent.history_expires_at >= received,
        )
    )
    return db.execute(stmt).first()


def _amount_candidates(db: Session, evidence: models.PaymentEvidence):
    received = as_utc(evidence.received_at)
    stmt = (
        select(models.PaymentRequest, models.PaymentIntent)
        .join(models.PaymentIntent, models.PaymentIntent.id == models.PaymentRequest.intent_id)
        .where(
            models.PaymentRequest.source_id == evidence.source_id,
            models.PaymentRequest.mode == "DUAL",
            models.PaymentRequest.payable_amount_minor == evidence.amount_minor,
            models.PaymentRequest.created_at <= received + timedelta(seconds=60),
            models.PaymentRequest.match_expires_at >= received,
            models.PaymentIntent.currency == evidence.currency,
        )
    )
    return list(db.execute(stmt).all())


def _quarantine(db: Session, evidence: models.PaymentEvidence, reason: str):
    evidence.state = "QUARANTINED"
    evidence.quarantine_reason = reason[:240]
    db.commit()
    return None


def _allocate(
    db: Session,
    evidence: models.PaymentEvidence,
    request: models.PaymentRequest,
    intent_id: str,
    reason: str,
):
    intent = db.scalar(
        select(models.PaymentIntent).where(models.PaymentIntent.id == intent_id).with_for_update()
    )
    if not intent:
        raise NotFound("payment intent disappeared during settlement")

    prior = db.scalar(
        select(models.PaymentAllocation).where(models.PaymentAllocation.evidence_id == evidence.id)
    )
    if prior:
        evidence.state = "ALLOCATED"
        evidence.matched_request_id = request.id
        db.commit()
        return intent

    previous_status = intent.status
    required_minor = request.payable_amount_minor
    intent.paid_minor += evidence.amount_minor
    intent.excess_minor = max(0, intent.paid_minor - required_minor)
    if intent.paid_minor >= required_minor:
        intent.status = "PAID"
        if intent.settled_at is None:
            intent.settled_at = utcnow()
    else:
        intent.status = "PARTIALLY_PAID"

    db.add(
        models.PaymentAllocation(
            evidence_id=evidence.id,
            intent_id=intent.id,
            amount_minor=evidence.amount_minor,
        )
    )
    evidence.state = "ALLOCATED"
    evidence.quarantine_reason = None
    evidence.matched_request_id = request.id

    business = db.get(models.Business, intent.business_id)
    event_type = "payment.intent.paid" if previous_status != "PAID" and intent.status == "PAID" else "payment.intent.updated"
    _event(db, business, intent, event_type, {
        "intent_id": intent.id,
        "external_id": intent.external_id,
        "status": intent.status,
        "paid_minor": intent.paid_minor,
        "excess_minor": intent.excess_minor,

        "base_amount_minor": intent.base_amount_minor,
        "required_amount_minor": required_minor,
        "currency": intent.currency,
        "trx_id": evidence.trx_id,
        "evidence_id": evidence.id,
        "match_reason": reason,
    })
    db.commit()
    db.refresh(intent)
    return intent


def settle_evidence(db: Session, evidence_id: str):
    evidence = db.scalar(
        select(models.PaymentEvidence)
        .where(models.PaymentEvidence.id == evidence_id)
        .with_for_update()
    )
    if not evidence:
        raise NotFound("payment evidence not found")
    if evidence.state == "ALLOCATED":
        allocation = db.scalar(
            select(models.PaymentAllocation).where(models.PaymentAllocation.evidence_id == evidence.id)
        )
        return db.get(models.PaymentIntent, allocation.intent_id) if allocation else None
    if evidence.state == "QUARANTINED":
        return None
    if evidence.state != "RECEIVED":
        return None

    remark_pair = _remark_candidate(db, evidence)
    amount_pairs = _amount_candidates(db, evidence)

    if remark_pair:
        remark_request, remark_intent = remark_pair
        if remark_intent.currency != evidence.currency:
            return _quarantine(db, evidence, "remark_currency_mismatch")
        conflicting_amount = [
            pair for pair in amount_pairs
            if pair[0].id != remark_request.id
        ]
        if conflicting_amount:
            return _quarantine(db, evidence, "remark_amount_conflict")
        return _allocate(db, evidence, remark_request, remark_intent.id, "remark")

    if evidence.remark:
        evidence.state = "UNMATCHED"
        evidence.quarantine_reason = "unknown_or_expired_remark"
        db.commit()
        return None

    if len(amount_pairs) > 1:
        return _quarantine(db, evidence, "ambiguous_amount_match")
    if len(amount_pairs) == 1:
        request, intent = amount_pairs[0]
        return _allocate(db, evidence, request, intent.id, "unique_amount")

    evidence.state = "UNMATCHED"
    evidence.quarantine_reason = "no_safe_match"
    db.commit()
    return None


def recover_by_trx(db: Session, business: models.Business, intent_id: str, trx_id: str, actor: str):
    intent = db.scalar(
        select(models.PaymentIntent)
        .where(models.PaymentIntent.id == intent_id, models.PaymentIntent.business_id == business.id)
        .with_for_update()
    )
    if not intent:
        raise NotFound("payment intent not found")

    evidence = db.scalar(
        select(models.PaymentEvidence)
        .where(
            models.PaymentEvidence.source_id == intent.source_id,
            models.PaymentEvidence.trx_id == trx_id,
        )
        .with_for_update()
    )
    if not evidence:
        raise NotFound("transaction ID is not present in verified payment history")
    if evidence.state == "SHADOW":
        raise Conflict("shadow evidence must be promoted before recovery")
    if evidence.business_id != business.id or evidence.source_id != intent.source_id:
        raise Conflict("transaction evidence belongs to another business or payment source")

    allocation = db.scalar(
        select(models.PaymentAllocation).where(models.PaymentAllocation.evidence_id == evidence.id)
    )
    if allocation:
        if allocation.intent_id != intent.id:
            raise Conflict("transaction ID is already allocated to another payment intent")
        return intent

    if as_utc(evidence.received_at) > as_utc(intent.history_expires_at):
        raise Conflict("transaction is outside the configured historical recovery window")

    request = get_request_for_intent(db, intent.id)
    db.add(
        models.PaymentClaim(
            business_id=business.id,
            intent_id=intent.id,
            evidence_id=evidence.id,
            claim_type="TRX_ID_RECOVERY",
            actor=actor[:160],
        )
    )
    db.flush()
    return _allocate(db, evidence, request, intent.id, "trx_id_recovery")


def promote_shadow_evidence(db: Session, source_id: str, limit: int = 1000):
    source = db.get(models.PaymentSource, source_id)
    if not source:
        raise NotFound("payment source not found")
    bounded_limit = max(1, min(int(limit), 5000))
    cutoff = utcnow() - timedelta(seconds=get_settings().history_recovery_seconds)
    rows = list(
        db.scalars(
            select(models.PaymentEvidence)
            .where(
                models.PaymentEvidence.source_id == source_id,
                models.PaymentEvidence.state == "SHADOW",
                models.PaymentEvidence.received_at >= cutoff,
            )
            .order_by(models.PaymentEvidence.received_at.asc())
            .with_for_update(skip_locked=True)
            .limit(bounded_limit)
        )
    )
    for evidence in rows:
        evidence.state = "RECEIVED"
        evidence.quarantine_reason = None
    if rows:
        db.add(
            models.PaymentEvent(
                business_id=source.business_id,
                intent_id=None,
                event_type="payment.shadow.promoted",
                payload_json=json.dumps(
                    {
                        "source_id": source_id,
                        "count": len(rows),
                        "evidence_ids": [evidence.id for evidence in rows],
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )
    db.commit()
    return rows
