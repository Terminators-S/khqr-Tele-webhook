from __future__ import annotations

import stat
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import core, models
from .config import get_settings
from .dashboard_auth import (
    COOKIE_NAME,
    create_dashboard_session,
    require_dashboard_csrf,
    require_dashboard_session,
    verify_dashboard_secret,
)
from .db import get_db
from .telegram_credentials import telegram_credentials_status
from .telegram_session import session_location


router = APIRouter(prefix="/dashboard", tags=["dashboard"])
UI_ROOT = Path(__file__).resolve().parent / "dashboard_ui"


class DashboardLogin(BaseModel):
    secret: str = Field(min_length=8, max_length=512)


class DashboardBusinessCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,118}[a-z0-9]$")
    webhook_url: str | None = None
class DashboardSourceCreate(BaseModel):
    business_id: str
    name: str = Field(min_length=2, max_length=160)
    currency: str = Field(default="USD", min_length=3, max_length=8)
    telegram_group_id: int | None = None
    telegram_sender_id: int | None = None
    merchant_alias: str | None = Field(default=None, max_length=160)
    static_khqr: str | None = None


class DashboardSenderUpdate(BaseModel):
    telegram_sender_id: int


class DashboardEnabledUpdate(BaseModel):
    enabled: bool
    confirm: str = ""


def _source_has_verified_acceptance(db: Session, source_id: str) -> bool:
    row = db.scalar(
        select(models.PaymentIntent.id).where(
            models.PaymentIntent.source_id == source_id,
            models.PaymentIntent.metadata_json.like('%"acceptance_test":true%'),
            models.PaymentIntent.metadata_json.like('%"result":"VERIFIED"%'),
        ).limit(1)
    )
    return row is not None


def _dashboard_enabled() -> None:
    if not get_settings().dashboard_enabled:
        raise HTTPException(status_code=404, detail="dashboard disabled")


def _mask_trx(value: str | None) -> str:
    if not value:
        return ""
    return ("•" * max(0, len(value) - 8)) + value[-8:]


def _source_payload(source: models.PaymentSource) -> dict:
    return {
        "id": source.id,
        "business_id": source.business_id,
        "name": source.name,
        "currency": source.currency,
        "telegram_group_id": source.telegram_group_id,
        "telegram_sender_id": source.telegram_sender_id,
        "merchant_alias": source.merchant_alias,
        "static_khqr_configured": bool(source.static_khqr),
        "enabled": bool(source.enabled),
        "ready": core.source_ready(source),
        "configured": core.source_configured(source),
        "created_at": source.created_at,
    }


def _runtime_status(db: Session) -> dict:
    evidence = dict(
        db.execute(
            select(models.PaymentEvidence.state, func.count(models.PaymentEvidence.id))
            .group_by(models.PaymentEvidence.state)
        ).all()
    )
    intents = dict(
        db.execute(
            select(models.PaymentIntent.status, func.count(models.PaymentIntent.id))
            .group_by(models.PaymentIntent.status)
        ).all()
    )
    outbox = dict(
        db.execute(
            select(models.WebhookOutbox.status, func.count(models.WebhookOutbox.id))
            .group_by(models.WebhookOutbox.status)
        ).all()
    )
    return {"evidence": evidence, "intents": intents, "outbox": outbox}


def _setup_state(db: Session) -> dict:
    settings = get_settings()
    businesses = db.scalar(select(func.count(models.Business.id))) or 0
    sources = list(db.scalars(select(models.PaymentSource).order_by(models.PaymentSource.created_at)))
    configured_sources = sum(1 for source in sources if core.source_configured(source))
    ready_sources = sum(1 for source in sources if core.source_ready(source))
    session_path, _name, _workdir = session_location(settings.telegram_session_name)
    session_exists = session_path.exists()
    session_mode = (
        stat.S_IMODE(session_path.stat().st_mode) if session_exists else None
    )
    try:
        credential_state = telegram_credentials_status()
    except RuntimeError as exc:
        credential_state = {
            "configured": False,
            "source": None,
            "error": str(exc),
        }
    internal_secret_safe = (
        settings.internal_secret != "dev-internal-secret-change-me"
        and len(settings.internal_secret) >= 24
    )

    if not internal_secret_safe:
        stage = "secure_installation"
    elif not businesses:
        stage = "create_business"
    elif not sources:
        stage = "create_source"
    elif not credential_state["configured"]:
        stage = "telegram_credentials"
    elif not session_exists:
        stage = "telegram_session"
    elif not configured_sources:
        stage = "configure_source"
    else:
        stage = "operational"

    return {
        "stage": stage,
        "business_count": businesses,
        "source_count": len(sources),
        "configured_source_count": configured_sources,
        "ready_source_count": ready_sources,
        "internal_secret_safe": internal_secret_safe,
        "telegram_api_credentials": bool(
            credential_state["configured"]
        ),
        "telegram_credentials_source": credential_state.get("source"),
        "telegram_credentials_error": credential_state.get("error"),
        "telegram_session_exists": session_exists,
        "telegram_session_owner_only": session_mode == 0o600 if session_exists else False,
        "dashboard_cookie_secure": settings.dashboard_cookie_secure,
    }


@router.get("")
@router.get("/")
def dashboard_index():
    _dashboard_enabled()
    return FileResponse(UI_ROOT / "index.html")


@router.post("/api/login")
def dashboard_login(payload: DashboardLogin, response: Response):
    _dashboard_enabled()
    if not verify_dashboard_secret(payload.secret):
        raise HTTPException(status_code=401, detail="invalid dashboard secret")
    settings = get_settings()
    session = create_dashboard_session()
    response.set_cookie(
        COOKIE_NAME,
        session.token,
        max_age=settings.dashboard_session_ttl_seconds,
        httponly=True,
        secure=settings.dashboard_cookie_secure,
        samesite="strict",
        path="/dashboard",
    )
    return {
        "authenticated": True,
        "expires_at": session.expires_at,
        "csrf_token": session.csrf_token,
    }


@router.post("/api/logout")
def dashboard_logout(
    response: Response,
    _session=Depends(require_dashboard_csrf),
):
    response.delete_cookie(COOKIE_NAME, path="/dashboard")
    return {"authenticated": False}


@router.get("/api/session")
def dashboard_session(_session=Depends(require_dashboard_session)):
    return {
        "authenticated": True,
        "expires_at": _session.expires_at,
        "csrf_token": _session.csrf_token,
    }


@router.get("/api/overview")
def dashboard_overview(
    _session=Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    businesses = db.scalar(select(func.count(models.Business.id))) or 0
    sources = list(db.scalars(select(models.PaymentSource)))
    allocations = db.scalar(select(func.count(models.PaymentAllocation.id))) or 0
    recent_evidence = list(
        db.scalars(
            select(models.PaymentEvidence)
            .order_by(models.PaymentEvidence.received_at.desc())
            .limit(8)
        )
    )
    return {
        "setup": _setup_state(db),
        "runtime": _runtime_status(db),
        "totals": {
            "businesses": businesses,
            "sources": len(sources),
            "enabled_sources": sum(1 for source in sources if source.enabled),
            "ready_sources": sum(1 for source in sources if core.source_ready(source)),
            "allocations": allocations,
        },
        "safety": {
            "telegram_shadow_only": settings.telegram_shadow_only,
            "allow_live_telegram": settings.allow_live_telegram,
            "allow_shadow_promotion": settings.allow_shadow_promotion,
            "checkout_ttl_seconds": settings.checkout_ttl_seconds,
            "late_match_grace_seconds": settings.late_match_grace_seconds,
            "reservation_seconds": settings.reservation_seconds,
            "offset_max_minor": settings.offset_max_minor,
        },
        "recent_evidence": [
            {
                "id": row.id,
                "trx": _mask_trx(row.trx_id),
                "amount_minor": row.amount_minor,
                "currency": row.currency,
                "state": row.state,
                "transport": row.transport,
                "source_id": row.source_id,
                "received_at": row.received_at,
            }
            for row in recent_evidence
        ],
    }


@router.get("/api/businesses")
def dashboard_businesses(
    _session=Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    source_counts = dict(
        db.execute(
            select(models.PaymentSource.business_id, func.count(models.PaymentSource.id))
            .group_by(models.PaymentSource.business_id)
        ).all()
    )
    rows = list(db.scalars(select(models.Business).order_by(models.Business.created_at)))
    return [
        {
            "id": row.id,
            "name": row.name,
            "slug": row.slug,
            "webhook_url": row.webhook_url,
            "webhook_configured": bool(row.webhook_url),
            "is_active": row.is_active,
            "source_count": source_counts.get(row.id, 0),
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/api/businesses")
def dashboard_create_business(
    payload: DashboardBusinessCreate,
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    try:
        business, api_key, webhook_secret = core.create_business(
            db,
            payload.name,
            payload.slug,
            payload.webhook_url,
        )
    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "id": business.id,
        "name": business.name,
        "slug": business.slug,
        "api_key": api_key,
        "webhook_secret": webhook_secret,
        "one_time_secret": True,
    }


@router.get("/api/sources")
def dashboard_sources(
    _session=Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    rows = list(
        db.scalars(select(models.PaymentSource).order_by(models.PaymentSource.created_at))
    )
    return [_source_payload(row) for row in rows]


@router.post("/api/sources")
def dashboard_create_source(
    payload: DashboardSourceCreate,
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    try:
        source = core.create_source(
            db,
            payload.business_id,
            name=payload.name,
            currency=payload.currency.upper(),
            telegram_group_id=payload.telegram_group_id,
            telegram_sender_id=payload.telegram_sender_id,
            merchant_alias=payload.merchant_alias,
            static_khqr=payload.static_khqr,
            enabled=False,
        )
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _source_payload(source)


@router.post("/api/sources/{source_id}/sender")
def dashboard_configure_sender(
    source_id: str,
    payload: DashboardSenderUpdate,
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    try:
        source = core.configure_source_sender(db, source_id, payload.telegram_sender_id)
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _source_payload(source)


@router.post("/api/sources/{source_id}/enabled")
def dashboard_set_enabled(
    source_id: str,
    payload: DashboardEnabledUpdate,
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    if payload.enabled and payload.confirm != "ENABLE SOURCE":
        raise HTTPException(
            status_code=400,
            detail='type "ENABLE SOURCE" to activate a payment source',
        )
    if payload.enabled and not _source_has_verified_acceptance(db, source_id):
        raise HTTPException(
            status_code=409,
            detail="pass a Real Payment Test before dashboard activation",
        )
    try:
        source = core.set_source_enabled(db, source_id, payload.enabled)
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _source_payload(source)


@router.get("/api/evidence")
def dashboard_evidence(
    limit: int = 50,
    _session=Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    limit = min(max(limit, 1), 200)
    rows = list(
        db.scalars(
            select(models.PaymentEvidence)
            .order_by(models.PaymentEvidence.received_at.desc())
            .limit(limit)
        )
    )
    return [
        {
            "id": row.id,
            "trx": _mask_trx(row.trx_id),
            "amount_minor": row.amount_minor,
            "currency": row.currency,
            "remark": row.remark,
            "state": row.state,
            "quarantine_reason": row.quarantine_reason,
            "source_id": row.source_id,
            "transport": row.transport,
            "received_at": row.received_at,
        }
        for row in rows
    ]


@router.get("/api/intents")
def dashboard_intents(
    limit: int = 50,
    _session=Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    limit = min(max(limit, 1), 200)
    rows = list(
        db.scalars(
            select(models.PaymentIntent)
            .order_by(models.PaymentIntent.created_at.desc())
            .limit(limit)
        )
    )
    return [
        {
            "id": row.id,
            "external_id": row.external_id,
            "source_id": row.source_id,
            "amount_minor": row.base_amount_minor,
            "currency": row.currency,
            "status": row.status,
            "paid_minor": row.paid_minor,
            "excess_minor": row.excess_minor,
            "created_at": row.created_at,
            "settled_at": row.settled_at,
        }
        for row in rows
    ]
