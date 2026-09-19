from __future__ import annotations

import re
import secrets
import shutil
import stat
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
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
from .khqr_asset import (
    KhqrAssetError,
    asset_dir,
    image_exists,
    image_metadata,
    image_path,
    save_uploaded_khqr,
)
from .khqr_payload import KhqrPayloadError, validate_static_khqr
from .security import generate_webhook_secret
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


class DashboardStoreCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    currency: Literal["USD", "KHR"] = "USD"
    webhook_url: str | None = None


class DashboardStoreUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    currency: Literal["USD", "KHR"] = "USD"
    webhook_url: str | None = None


class DashboardGroupUpdate(BaseModel):
    telegram_group_id: int


class DashboardSenderUpdate(BaseModel):
    telegram_sender_id: int


class DashboardEnabledUpdate(BaseModel):
    enabled: bool
    confirm: str = ""


class DashboardRotateCredential(BaseModel):
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
    khqr_valid = False
    khqr_error = None
    khqr_details = None
    uploaded_image = image_exists(source.id)
    upload_meta = image_metadata(source.id) if uploaded_image else {}
    if (source.static_khqr or "").strip():
        try:
            khqr_details = validate_static_khqr(source.static_khqr or "")
            khqr_valid = True
        except KhqrPayloadError as exc:
            khqr_error = str(exc)

    configured = bool(core.source_configured(source) and khqr_valid and uploaded_image)
    return {
        "id": source.id,
        "business_id": source.business_id,
        "name": source.name,
        "currency": source.currency,
        "telegram_group_id": source.telegram_group_id,
        "telegram_sender_id": source.telegram_sender_id,
        "merchant_alias": source.merchant_alias,
        "static_khqr_configured": bool(source.static_khqr),
        "khqr_image_configured": uploaded_image,
        "khqr_image_url": (
            f"/dashboard/api/sources/{source.id}/khqr-image"
            if uploaded_image else None
        ),
        "khqr_image_filename": upload_meta.get("filename"),
        "khqr_valid": khqr_valid and uploaded_image,
        "khqr_error": (
            khqr_error
            if khqr_error
            else None if uploaded_image else "upload your store's static KHQR image"
        ),
        "khqr_account_id": khqr_details.bakong_id if khqr_details else None,
        "khqr_account_type": khqr_details.account_type if khqr_details else None,
        "khqr_merchant_name": khqr_details.merchant_name if khqr_details else None,
        "enabled": bool(source.enabled),
        "ready": bool(source.enabled and configured),
        "configured": configured,
        "created_at": source.created_at,
    }


def _store_slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    base = base[:90] or "store"
    return base


def _apply_khqr_asset(
    db: Session,
    source: models.PaymentSource,
    asset,
    *,
    business: models.Business | None = None,
) -> models.PaymentSource:
    business = business or db.get(models.Business, source.business_id)
    source.static_khqr = asset.payload
    source.currency = asset.validation.currency or source.currency
    source.merchant_alias = (
        (asset.validation.merchant_name or "").strip()
        or (business.name if business else source.merchant_alias)
        or source.name
    )
    if source.name.endswith(" Payments") or source.name.startswith("New payment QR"):
        merchant = (asset.validation.merchant_name or "").strip()
        source.name = merchant or source.name
    db.commit()
    db.refresh(source)
    return source


def _primary_source(db: Session, business_id: str) -> models.PaymentSource | None:
    return db.scalar(
        select(models.PaymentSource)
        .where(models.PaymentSource.business_id == business_id)
        .order_by(models.PaymentSource.created_at)
        .limit(1)
    )


def _store_payload(db: Session, business: models.Business) -> dict:
    sources = list(
        db.scalars(
            select(models.PaymentSource)
            .where(models.PaymentSource.business_id == business.id)
            .order_by(models.PaymentSource.created_at)
        )
    )
    source_payloads = [_source_payload(source) for source in sources]
    return {
        "id": business.id,
        "name": business.name,
        "slug": business.slug,
        "webhook_url": business.webhook_url,
        "webhook_configured": bool(business.webhook_url),
        "is_active": business.is_active,
        "created_at": business.created_at,
        "source": source_payloads[0] if source_payloads else None,
        "sources": source_payloads,
        "source_count": len(source_payloads),
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
    source_states = [_source_payload(source) for source in sources]
    configured_sources = sum(1 for item in source_states if item["configured"])
    ready_sources = sum(1 for item in source_states if item["ready"])
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
            "ready_sources": sum(1 for source in sources if _source_payload(source)["ready"]),
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


@router.get("/api/stores")
def dashboard_stores(
    _session=Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    rows = list(db.scalars(select(models.Business).order_by(models.Business.created_at)))
    return [_store_payload(db, row) for row in rows]


@router.post("/api/stores")
def dashboard_create_store(
    payload: DashboardStoreCreate,
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    base = _store_slug(payload.name)
    business = None
    api_key = None
    webhook_secret = None
    for attempt in range(12):
        slug = base if attempt == 0 else f"{base}-{secrets.token_hex(2)}"
        try:
            business, api_key, webhook_secret = core.create_business(
                db,
                payload.name,
                slug,
                payload.webhook_url,
            )
            break
        except core.Conflict:
            continue
    if business is None:
        raise HTTPException(status_code=409, detail="could not create a unique store")

    try:
        core.create_source(
            db,
            business.id,
            name=payload.name.strip() + " Payments",
            currency=payload.currency,
            telegram_group_id=None,
            telegram_sender_id=None,
            merchant_alias=payload.name.strip(),
            static_khqr=None,
            enabled=False,
        )
    except Exception:
        db.delete(business)
        db.commit()
        raise

    result = _store_payload(db, business)
    result.update({
        "api_key": api_key,
        "webhook_secret": webhook_secret,
        "one_time_secret": True,
    })
    return result


@router.post("/api/stores/{store_id}")
def dashboard_update_store(
    store_id: str,
    payload: DashboardStoreUpdate,
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    business = db.get(models.Business, store_id)
    if not business:
        raise HTTPException(status_code=404, detail="store not found")
    sources = list(
        db.scalars(
            select(models.PaymentSource)
            .where(models.PaymentSource.business_id == business.id)
            .order_by(models.PaymentSource.created_at)
        )
    )
    if any(source.enabled for source in sources):
        raise HTTPException(status_code=409, detail="disable every payment source before editing store setup")
    if (
        len(sources) == 1
        and sources[0].static_khqr
        and sources[0].currency != payload.currency
    ):
        raise HTTPException(
            status_code=409,
            detail="replace the uploaded KHQR image to change currency",
        )

    business.name = payload.name.strip()
    new_webhook_secret = None
    if business.webhook_url != payload.webhook_url:
        business.webhook_url = payload.webhook_url
        business.webhook_secret = (
            generate_webhook_secret() if payload.webhook_url else None
        )
        new_webhook_secret = business.webhook_secret
    for source in sources:
        if not source.static_khqr and not image_exists(source.id):
            source.currency = payload.currency
            source.merchant_alias = payload.name.strip()
            if source.name.endswith(" Payments") or source.name.startswith("New payment QR"):
                source.name = payload.name.strip() + " Payments"
    db.commit()
    db.refresh(business)
    result = _store_payload(db, business)
    if new_webhook_secret:
        result["webhook_secret"] = new_webhook_secret
    return result


@router.get("/api/stores/{store_id}/integration")
def dashboard_store_integration(
    store_id: str,
    request: Request,
    source_id: str | None = None,
    _session=Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    business = db.get(models.Business, store_id)
    if not business:
        raise HTTPException(status_code=404, detail="store not found")
    settings = get_settings()
    source = db.get(models.PaymentSource, source_id) if source_id else _primary_source(db, business.id)
    if source_id and not source:
        raise HTTPException(status_code=404, detail="payment source not found for this store")
    if source and source.business_id != business.id:
        raise HTTPException(status_code=404, detail="payment source not found for this store")
    source_payload = _source_payload(source) if source else None
    verified = bool(source and _source_has_verified_acceptance(db, source.id))
    live_collector_allowed = bool(
        not settings.telegram_shadow_only and settings.allow_live_telegram
    )
    api_base_url = str(request.base_url).rstrip("/")
    return {
        "store_id": business.id,
        "project_name": business.name,
        "slug": business.slug,
        "source_id": source.id if source else None,
        "currency": source.currency if source else None,
        "api_base_url": api_base_url,
        "payment_intent_url": api_base_url + "/v1/payment-intents",
        "api_key_configured": bool(business.api_key_hash),
        "webhook_url": business.webhook_url,
        "webhook_configured": bool(business.webhook_url),
        "webhook_secret_configured": bool(business.webhook_secret),
        "telegram_group_id": source.telegram_group_id if source else None,
        "telegram_sender_id": source.telegram_sender_id if source else None,
        "telegram_group_configured": bool(source and source.telegram_group_id is not None),
        "telegram_sender_configured": bool(source and source.telegram_sender_id is not None),
        "khqr_configured": bool(source_payload and source_payload["khqr_valid"]),
        "real_payment_test_verified": verified,
        "source_enabled": bool(source and source.enabled),
        "live_collector_allowed": live_collector_allowed,
        "telegram_shadow_only": settings.telegram_shadow_only,
        "allow_live_telegram": settings.allow_live_telegram,
        "activation_ready": bool(
            source_payload
            and source_payload["configured"]
            and verified
            and business.webhook_url
            and business.webhook_secret
            and live_collector_allowed
        ),
    }


@router.post("/api/stores/{store_id}/rotate-api-key")
def dashboard_rotate_api_key(
    store_id: str,
    payload: DashboardRotateCredential,
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    if payload.confirm != "ROTATE API KEY":
        raise HTTPException(status_code=400, detail='type "ROTATE API KEY" to continue')
    try:
        business, api_key = core.rotate_business_api_key(db, store_id)
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "store_id": business.id,
        "api_key": api_key,
        "one_time_secret": True,
    }


@router.post("/api/stores/{store_id}/rotate-webhook-secret")
def dashboard_rotate_webhook_secret(
    store_id: str,
    payload: DashboardRotateCredential,
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    if payload.confirm != "ROTATE WEBHOOK SECRET":
        raise HTTPException(
            status_code=400,
            detail='type "ROTATE WEBHOOK SECRET" to continue',
        )
    try:
        business, webhook_secret = core.rotate_business_webhook_secret(db, store_id)
    except core.NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except core.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "store_id": business.id,
        "webhook_secret": webhook_secret,
        "one_time_secret": True,
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


@router.get("/api/sources/{source_id}/khqr-image")
def dashboard_source_khqr_image(
    source_id: str,
    _session=Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    source = db.get(models.PaymentSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="payment source not found")
    if not image_exists(source.id):
        raise HTTPException(status_code=404, detail="KHQR image has not been uploaded")
    meta = image_metadata(source.id)
    return FileResponse(
        image_path(source.id),
        media_type=meta.get("media_type") or "application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/sources/{source_id}/qr.png")
def dashboard_source_qr_legacy_alias(
    source_id: str,
    _session=Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    source = db.get(models.PaymentSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="payment source not found")
    if not image_exists(source.id):
        raise HTTPException(status_code=404, detail="KHQR image has not been uploaded")
    meta = image_metadata(source.id)
    return FileResponse(
        image_path(source.id),
        media_type=meta.get("media_type") or "application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/api/sources/{source_id}/khqr-image")
async def dashboard_upload_khqr_image(
    source_id: str,
    file: UploadFile = File(...),
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    source = db.get(models.PaymentSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="payment source not found")
    if source.enabled:
        raise HTTPException(status_code=409, detail="disable the store before replacing KHQR")

    limit = get_settings().khqr_upload_max_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail="KHQR image is too large")

    try:
        asset = save_uploaded_khqr(
            source.id,
            data,
            filename=file.filename,
            media_type=file.content_type,
        )
    except KhqrAssetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    business = db.get(models.Business, source.business_id)
    _apply_khqr_asset(db, source, asset, business=business)

    result = _source_payload(source)
    result.update({
        "upload_valid": True,
        "uploaded_filename": asset.filename,
        "uploaded_sha256": asset.sha256,
        "image_width": asset.width,
        "image_height": asset.height,
    })
    return result


@router.post("/api/stores/{store_id}/khqr-images")
async def dashboard_bulk_upload_khqr_images(
    store_id: str,
    files: list[UploadFile] = File(...),
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    business = db.get(models.Business, store_id)
    if not business:
        raise HTTPException(status_code=404, detail="store not found")
    if not files:
        raise HTTPException(status_code=400, detail="choose at least one KHQR image")
    if len(files) > 20:
        raise HTTPException(status_code=400, detail="upload at most 20 KHQR images at once")

    existing_sources = list(
        db.scalars(
            select(models.PaymentSource)
            .where(models.PaymentSource.business_id == store_id)
            .order_by(models.PaymentSource.created_at)
        )
    )
    reusable = [
        source for source in existing_sources
        if not source.enabled
        and not (source.static_khqr or "").strip()
        and not image_exists(source.id)
    ]
    existing_payloads = {
        (source.static_khqr or "").strip(): source.id
        for source in existing_sources
        if (source.static_khqr or "").strip()
    }
    limit = get_settings().khqr_upload_max_bytes
    created: list[dict] = []
    errors: list[dict] = []

    for index, file in enumerate(files, start=1):
        filename = (file.filename or f"khqr-{index}.png").strip() or f"khqr-{index}.png"
        data = await file.read(limit + 1)
        if len(data) > limit:
            errors.append({"filename": filename, "error": "KHQR image is too large"})
            continue

        source = reusable.pop(0) if reusable else None
        source_was_created = source is None
        if source is None:
            source = core.create_source(
                db,
                business.id,
                name=f"New payment QR {len(existing_sources) + len(created) + 1}",
                currency="USD",
                telegram_group_id=None,
                telegram_sender_id=None,
                merchant_alias=business.name,
                static_khqr=None,
                enabled=False,
            )

        try:
            asset = save_uploaded_khqr(
                source.id,
                data,
                filename=filename,
                media_type=file.content_type,
            )
            duplicate_source_id = existing_payloads.get(asset.payload)
            if duplicate_source_id and duplicate_source_id != source.id:
                shutil.rmtree(asset_dir(source.id), ignore_errors=True)
                if source_was_created:
                    db.delete(source)
                    db.commit()
                else:
                    reusable.insert(0, source)
                errors.append({
                    "filename": filename,
                    "error": "this KHQR is already configured for this store",
                })
                continue

            _apply_khqr_asset(db, source, asset, business=business)
            existing_payloads[asset.payload] = source.id
            payload = _source_payload(source)
            payload.update({
                "upload_valid": True,
                "uploaded_filename": asset.filename,
                "uploaded_sha256": asset.sha256,
                "image_width": asset.width,
                "image_height": asset.height,
            })
            created.append(payload)
        except KhqrAssetError as exc:
            shutil.rmtree(asset_dir(source.id), ignore_errors=True)
            if source_was_created:
                db.delete(source)
                db.commit()
            else:
                reusable.insert(0, source)
            errors.append({"filename": filename, "error": str(exc)})
        except core.Conflict as exc:
            shutil.rmtree(asset_dir(source.id), ignore_errors=True)
            if source_was_created:
                db.delete(source)
                db.commit()
            else:
                reusable.insert(0, source)
            errors.append({"filename": filename, "error": str(exc)})
        except Exception:
            shutil.rmtree(asset_dir(source.id), ignore_errors=True)
            if source_was_created:
                db.delete(source)
                db.commit()
            else:
                reusable.insert(0, source)
            raise

    db.expire_all()
    return {
        "created": created,
        "errors": errors,
        "store": _store_payload(db, business),
    }


@router.post("/api/sources/{source_id}/telegram-group")
def dashboard_update_telegram_group(
    source_id: str,
    payload: DashboardGroupUpdate,
    _session=Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    try:
        source = core.configure_source_group(
            db,
            source_id,
            payload.telegram_group_id,
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
    if payload.enabled:
        source = db.get(models.PaymentSource, source_id)
        if not source:
            raise HTTPException(status_code=404, detail="payment source not found")
        if not _source_payload(source)["configured"]:
            raise HTTPException(
                status_code=409,
                detail="finish store setup, including an uploaded verified KHQR image, before activation",
            )
        if not _source_has_verified_acceptance(db, source_id):
            raise HTTPException(
                status_code=409,
                detail="pass a Real Payment Test before dashboard activation",
            )
        business = db.get(models.Business, source.business_id)
        if not business or not business.webhook_url or not business.webhook_secret:
            raise HTTPException(
                status_code=409,
                detail="configure the project webhook and signing secret before dashboard activation",
            )
        settings = get_settings()
        if settings.telegram_shadow_only or not settings.allow_live_telegram:
            raise HTTPException(
                status_code=409,
                detail=(
                    "live Telegram collector is locked; set TELEGRAM_SHADOW_ONLY=false "
                    "and ALLOW_LIVE_TELEGRAM=true, restart core services, then activate"
                ),
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
