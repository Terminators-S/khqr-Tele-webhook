from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import core, models, schemas
from .config import get_settings
from .db import get_db
from .security import hash_secret


router = APIRouter()


def require_internal(x_internal_secret: str = Header(alias="X-Internal-Secret")):
    expected = get_settings().internal_secret
    if hash_secret(x_internal_secret) != hash_secret(expected):
        raise HTTPException(status_code=401, detail="invalid internal secret")


def require_business(
    x_api_key: str = Header(alias="X-Api-Key"),
    db: Session = Depends(get_db),
):
    try:
        return core.business_for_api_key(db, x_api_key)
    except core.NotFound as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def source_out(source: models.PaymentSource) -> schemas.PaymentSourceOut:
    return schemas.PaymentSourceOut(
        **{column: getattr(source, column) for column in (
            "id", "business_id", "name", "currency", "telegram_group_id",
            "telegram_sender_id", "merchant_alias", "static_khqr", "enabled"
        )},
        ready=core.source_ready(source),
    )


def intent_out(db: Session, intent: models.PaymentIntent) -> schemas.PaymentIntentOut:
    request = core.get_request_for_intent(db, intent.id)
    source = db.get(models.PaymentSource, intent.source_id)
    return schemas.PaymentIntentOut(
        id=intent.id,
        external_id=intent.external_id,
        source_id=intent.source_id,
        amount_minor=intent.base_amount_minor,
        currency=intent.currency,
        status=intent.status,
        paid_minor=intent.paid_minor,
        excess_minor=intent.excess_minor,
        created_at=intent.created_at,
        checkout_expires_at=intent.checkout_expires_at,
        payment_request=schemas.PaymentRequestOut(
            id=request.id,
            remark=request.remark,
            mode=request.mode,
            offset_minor=request.offset_minor,
            payable_amount_minor=request.payable_amount_minor,
            static_khqr=source.static_khqr or "",
            checkout_expires_at=request.checkout_expires_at,
            match_expires_at=request.match_expires_at,
        ),
    )


@router.get("/healthz")
def healthz():
    return {"ok": True, "service": "khqr-self-develop"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)):
    try:
        sources = list(db.scalars(select(models.PaymentSource).where(models.PaymentSource.enabled.is_(True))))
        broken = [source.id for source in sources if not core.source_ready(source)]
        db.execute(select(1))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    ready_state = bool(sources) and not broken
    payload = {"ready": ready_state, "enabled_sources": len(sources), "invalid_sources": broken}
    if not ready_state:
        raise HTTPException(status_code=503, detail=payload)
    return payload


@router.post("/internal/businesses", response_model=schemas.BusinessCreated, dependencies=[Depends(require_internal)])
def add_business(payload: schemas.BusinessCreate, db: Session = Depends(get_db)):
    try:
        business, api_key, webhook_secret = core.create_business(db, payload.name, payload.slug, payload.webhook_url)
    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return schemas.BusinessCreated(
        id=business.id,
        name=business.name,
        slug=business.slug,
        api_key=api_key,
        webhook_secret=webhook_secret,
    )


@router.post(
    "/internal/businesses/{business_id}/sources",
    response_model=schemas.PaymentSourceOut,
    dependencies=[Depends(require_internal)],
)
def add_source(business_id: str, payload: schemas.PaymentSourceCreate, db: Session = Depends(get_db)):
    try:
        source = core.create_source(
            db,
            business_id,
            name=payload.name,
            currency=payload.currency.upper(),
            telegram_group_id=payload.telegram_group_id,
            telegram_sender_id=payload.telegram_sender_id,
            merchant_alias=payload.merchant_alias,
            static_khqr=payload.static_khqr,
            enabled=payload.enabled,
        )
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return source_out(source)


@router.post(
    "/internal/sources/{source_id}/sender",
    response_model=schemas.PaymentSourceOut,
    dependencies=[Depends(require_internal)],
)
def configure_source_sender(
    source_id: str,
    payload: schemas.PaymentSourceSenderUpdate,
    db: Session = Depends(get_db),
):
    try:
        source = core.configure_source_sender(
            db,
            source_id,
            payload.telegram_sender_id,
        )
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return source_out(source)


@router.post(
    "/internal/sources/{source_id}/enabled",
    response_model=schemas.PaymentSourceOut,
    dependencies=[Depends(require_internal)],
)
def set_source_enabled(
    source_id: str,
    payload: schemas.PaymentSourceEnabledUpdate,
    db: Session = Depends(get_db),
):
    try:
        source = core.set_source_enabled(db, source_id, payload.enabled)
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return source_out(source)


@router.post("/v1/payment-intents", response_model=schemas.PaymentIntentOut)
def add_intent(
    payload: schemas.PaymentIntentCreate,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    business: models.Business = Depends(require_business),
    db: Session = Depends(get_db),
):
    try:
        intent, _request = core.create_payment_intent(
            db,
            business,
            source_id=payload.source_id,
            external_id=payload.external_id,
            idempotency_key=idempotency_key,
            amount_minor=payload.amount_minor,
            currency=payload.currency,
            metadata=payload.metadata,
            remark_prefix=payload.remark_prefix,
        )
        return intent_out(db, intent)
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/v1/payment-intents/{intent_id}", response_model=schemas.PaymentIntentOut)
def get_intent(
    intent_id: str,
    business: models.Business = Depends(require_business),
    db: Session = Depends(get_db),
):
    intent = db.scalar(
        select(models.PaymentIntent).where(
            models.PaymentIntent.id == intent_id,
            models.PaymentIntent.business_id == business.id,
        )
    )
    if not intent:
        raise HTTPException(status_code=404, detail="payment intent not found")
    return intent_out(db, intent)


@router.post("/v1/payment-intents/{intent_id}/recover", response_model=schemas.PaymentIntentOut)
def recover_intent(
    intent_id: str,
    payload: schemas.RecoveryRequest,
    business: models.Business = Depends(require_business),
    db: Session = Depends(get_db),
):
    try:
        intent = core.recover_by_trx(db, business, intent_id, payload.trx_id, actor=f"api:{business.slug}")
        return intent_out(db, intent)
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/internal/evidence",
    response_model=schemas.EvidenceOut,
    dependencies=[Depends(require_internal)],
)
def add_evidence(payload: schemas.EvidenceIn, db: Session = Depends(get_db)):
    try:
        evidence = core.ingest_evidence(
            db,
            source_id=payload.source_id,
            transport=payload.transport,
            transport_message_id=payload.transport_message_id,
            sender_id=payload.sender_id,
            raw_text=payload.raw_text,
            received_at=payload.received_at,
            trx_id=payload.trx_id,
            amount_minor=payload.amount_minor,
            currency=payload.currency,
            remark=payload.remark,
        )
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return schemas.EvidenceOut(
        id=evidence.id,
        trx_id=evidence.trx_id,
        state=evidence.state,

        quarantine_reason=evidence.quarantine_reason,
        matched_request_id=evidence.matched_request_id,
    )


@router.get("/internal/runtime", dependencies=[Depends(require_internal)])
def runtime(db: Session = Depends(get_db)):
    evidence_counts = dict(
        db.execute(
            select(models.PaymentEvidence.state, func.count(models.PaymentEvidence.id))
            .group_by(models.PaymentEvidence.state)
        ).all()
    )
    outbox_counts = dict(
        db.execute(
            select(models.WebhookOutbox.status, func.count(models.WebhookOutbox.id))
            .group_by(models.WebhookOutbox.status)
        ).all()
    )
    pending_intents = db.scalar(
        select(func.count(models.PaymentIntent.id)).where(
            models.PaymentIntent.status.in_(["PENDING", "PARTIALLY_PAID"])
        )
    )
    return {
        "pending_intents": pending_intents or 0,
        "evidence": evidence_counts,
        "outbox": outbox_counts,
    }


@router.post(
    "/internal/sources/{source_id}/promote-shadow",
    dependencies=[Depends(require_internal)],
)
def promote_shadow(
    source_id: str,
    payload: schemas.ShadowPromotionRequest,
    db: Session = Depends(get_db),
):
    if not get_settings().allow_shadow_promotion:
        raise HTTPException(status_code=409, detail="shadow promotion is disabled by configuration")
    try:
        rows = core.promote_shadow_evidence(db, source_id, payload.limit)
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"source_id": source_id, "promoted": len(rows)}
