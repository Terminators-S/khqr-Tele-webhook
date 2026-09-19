from __future__ import annotations

import json
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import core, models
from .config import get_settings
from .dashboard_auth import (
    DashboardSession,
    require_dashboard_csrf,
    require_dashboard_session,
)
from .dashboard_telegram import (
    _connect_authorized,
    _disconnect,
    _local_session_account_id,
    _message_sender_id,
    _session_parts,
)
from .db import get_db
from .khqr_asset import image_exists, image_metadata, image_path
from .khqr_payload import KhqrPayloadError, validate_static_khqr
from .parser import parse_aba_text


router = APIRouter(prefix="/dashboard/api/acceptance-tests", tags=["dashboard-acceptance"])


class AcceptanceStart(BaseModel):
    source_id: str
    amount_minor: int = Field(ge=1, le=500)
    confirm: str


class AcceptanceScan(BaseModel):
    limit: int = Field(default=120, ge=10, le=500)


def _metadata(intent: models.PaymentIntent) -> dict:
    try:
        value = json.loads(intent.metadata_json or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def _require_test_intent(db: Session, intent_id: str):
    intent = db.get(models.PaymentIntent, intent_id)
    if not intent or not _metadata(intent).get("acceptance_test"):
        raise HTTPException(status_code=404, detail="acceptance test not found")
    request = core.get_request_for_intent(db, intent.id)
    source = db.get(models.PaymentSource, intent.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="payment source not found")
    return intent, request, source


def _active_test_exists(db: Session, source_id: str) -> bool:
    now = core.utcnow()
    rows = list(
        db.scalars(
            select(models.PaymentIntent).where(
                models.PaymentIntent.source_id == source_id,
                models.PaymentIntent.checkout_expires_at >= now,
            )
        )
    )
    return any(_metadata(row).get("acceptance_test") for row in rows)


def _create_request(db: Session, source: models.PaymentSource, amount_minor: int):
    business = db.get(models.Business, source.business_id)
    if not business:
        raise HTTPException(status_code=404, detail="business not found")

    settings = get_settings()
    now = core.utcnow()
    checkout_expires = now + timedelta(seconds=settings.checkout_ttl_seconds)
    match_expires = checkout_expires + timedelta(seconds=settings.late_match_grace_seconds)
    history_expires = now + timedelta(seconds=settings.history_recovery_seconds)
    marker = secrets.token_hex(8)
    intent = models.PaymentIntent(
        business_id=business.id,
        source_id=source.id,
        external_id="acceptance-" + marker,
        idempotency_key="acceptance-" + marker,
        base_amount_minor=amount_minor,
        currency=source.currency.upper(),
        status="TEST_WAITING",
        metadata_json=json.dumps(
            {"acceptance_test": True, "dashboard_only": True, "result": "WAITING"},
            separators=(",", ":"),
            sort_keys=True,
        ),
        checkout_expires_at=checkout_expires,
        history_expires_at=history_expires,
    )
    request = models.PaymentRequest(
        intent_id=intent.id,
        source_id=source.id,
        remark=core._new_remark(db, source.id),
        mode="REMARK_PRIMARY",
        offset_minor=None,
        payable_amount_minor=amount_minor,
        checkout_expires_at=checkout_expires,
        match_expires_at=match_expires,
    )

    db.add(intent)
    db.flush()
    request.intent_id = intent.id
    db.add(request)
    db.flush()
    core._purge_expired_reservations(db, now)
    for offset_minor in range(settings.offset_max_minor + 1):
        payable = amount_minor + offset_minor
        try:
            with db.begin_nested():
                reservation = models.AmountReservation(
                    request_id=request.id,
                    source_id=source.id,
                    currency=source.currency.upper(),
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

    db.commit()
    db.refresh(intent)
    db.refresh(request)
    return intent, request


def _result_payload(intent, request, source) -> dict:
    meta = _metadata(intent)
    return {
        "intent_id": intent.id,
        "source_id": source.id,
        "source_name": source.name,
        "merchant_alias": source.merchant_alias,
        "currency": intent.currency,
        "base_amount_minor": intent.base_amount_minor,
        "payable_amount_minor": request.payable_amount_minor,
        "offset_minor": request.offset_minor,
        "mode": request.mode,
        "remark": request.remark,
        "static_khqr": source.static_khqr,
        "checkout_expires_at": request.checkout_expires_at,
        "match_expires_at": request.match_expires_at,
        "result": meta.get("result", "WAITING"),
        "result_reason": meta.get("result_reason"),
        "evidence_id": meta.get("evidence_id"),
        "trx_tail": meta.get("trx_tail"),
        "observed_amount_minor": meta.get("observed_amount_minor"),
        "observed_remark": meta.get("observed_remark"),
        "would_status": meta.get("would_status"),
        "would_paid_minor": meta.get("would_paid_minor"),
        "would_excess_minor": meta.get("would_excess_minor"),
        "match_reason": meta.get("match_reason"),
        "webhook_suppressed": True,
        "source_enabled": bool(source.enabled),
    }


def _save_result(db: Session, intent: models.PaymentIntent, **updates) -> None:
    value = _metadata(intent)
    value.update(updates)
    result = str(value.get("result") or "WAITING")
    intent.status = "TEST_" + result
    intent.metadata_json = json.dumps(value, separators=(",", ":"), sort_keys=True)
    db.commit()
    db.refresh(intent)


def _preview_match(db: Session, request: models.PaymentRequest, evidence: models.PaymentEvidence) -> dict:
    remark_pair = core._remark_candidate(db, evidence)
    amount_pairs = core._amount_candidates(db, evidence)
    conflicting_amount = [pair for pair in amount_pairs if pair[0].id != request.id]
    if remark_pair:
        if remark_pair[1].currency != evidence.currency:
            return {"result": "MISMATCH", "result_reason": "remark_currency_mismatch"}
        if remark_pair[0].id != request.id:
            return {"result": "MISMATCH", "result_reason": "remark_belongs_to_other_request"}
        if conflicting_amount:
            return {"result": "MISMATCH", "result_reason": "remark_amount_conflict"}
        reason = "remark"
    elif evidence.remark:
        return {"result": "MISMATCH", "result_reason": "unknown_or_wrong_remark"}
    elif len(amount_pairs) > 1:
        return {"result": "MISMATCH", "result_reason": "ambiguous_amount_match"}
    elif len(amount_pairs) == 1 and amount_pairs[0][0].id == request.id:
        reason = "unique_amount"
    else:
        return {"result": "MISMATCH", "result_reason": "no_safe_match"}

    paid_minor = evidence.amount_minor
    required_minor = request.payable_amount_minor
    would_status = "PAID" if paid_minor >= required_minor else "PARTIALLY_PAID"
    return {
        "result": "VERIFIED",
        "result_reason": None,
        "match_reason": reason,
        "would_status": would_status,
        "would_paid_minor": paid_minor,
        "would_excess_minor": max(0, paid_minor - required_minor),
    }


@router.get("/prerequisites")
def acceptance_prerequisites(
    _session: DashboardSession = Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    rows = list(db.scalars(select(models.PaymentSource).order_by(models.PaymentSource.created_at)))
    payload = []
    for source in rows:
        khqr_valid = False
        uploaded_image = image_exists(source.id)
        if uploaded_image and (source.static_khqr or "").strip():
            try:
                validate_static_khqr(source.static_khqr or "")
                khqr_valid = True
            except KhqrPayloadError:
                pass
        payload.append(
        {
            "source_id": source.id,
            "name": source.name,
            "business_id": source.business_id,
            "configured": bool(core.source_configured(source) and khqr_valid),
            "disabled": not source.enabled,
            "telegram_group": source.telegram_group_id is not None,
            "trusted_sender": source.telegram_sender_id is not None,
            "merchant_alias": bool((source.merchant_alias or "").strip()),
            "static_khqr": bool((source.static_khqr or "").strip()),
            "khqr_image_configured": uploaded_image,
            "khqr_valid": khqr_valid,
            "currency": source.currency,
        }
        )
    return payload


@router.post("/start")
def acceptance_start(
    payload: AcceptanceStart,
    _session: DashboardSession = Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    if payload.confirm != "SEND REAL TEST MONEY":
        raise HTTPException(status_code=400, detail='type "SEND REAL TEST MONEY" to start')
    source = db.get(models.PaymentSource, payload.source_id)
    if not source:
        raise HTTPException(status_code=404, detail="payment source not found")
    if source.currency.upper() != "USD":
        raise HTTPException(status_code=409, detail="real-payment test currently supports USD sources")
    if source.enabled:
        raise HTTPException(status_code=409, detail="normal source must remain disabled during the test")
    if not core.source_configured(source):
        raise HTTPException(
            status_code=409,
            detail="complete payment account and Telegram setup first",
        )
    if not image_exists(source.id):
        raise HTTPException(
            status_code=409,
            detail="upload your store's static KHQR image first",
        )
    try:
        validate_static_khqr(source.static_khqr or "")
    except KhqrPayloadError as exc:
        raise HTTPException(
            status_code=409,
            detail="payment QR is invalid; rebuild it in Setup",
        ) from exc
    try:
        _settings, _credentials, session_path, _name, _workdir = _session_parts()
    except HTTPException:
        raise
    if _local_session_account_id(session_path) is None:
        raise HTTPException(status_code=409, detail="Telegram session is not authorized")
    if _active_test_exists(db, source.id):
        raise HTTPException(status_code=409, detail="an acceptance test is already active for this source")

    intent, request = _create_request(db, source, payload.amount_minor)
    return _result_payload(intent, request, source)


@router.get("/{intent_id}")
def acceptance_status(
    intent_id: str,
    _session: DashboardSession = Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    intent, request, source = _require_test_intent(db, intent_id)
    current = _metadata(intent).get("result")
    if core.utcnow() > core.as_utc(request.match_expires_at) and current not in {"VERIFIED", "CANCELLED", "EXPIRED"}:
        _save_result(db, intent, result="EXPIRED", result_reason="match_window_expired")
    return _result_payload(intent, request, source)


@router.post("/{intent_id}/scan")
async def acceptance_scan(
    intent_id: str,
    payload: AcceptanceScan,
    _session: DashboardSession = Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    intent, request, source = _require_test_intent(db, intent_id)
    current = _metadata(intent).get("result")
    if current in {"VERIFIED", "EXPIRED", "CANCELLED"}:
        return _result_payload(intent, request, source)
    if source.enabled:
        raise HTTPException(status_code=409, detail="normal source became enabled; scan stopped")
    if core.utcnow() > core.as_utc(request.match_expires_at):
        _save_result(db, intent, result="EXPIRED", result_reason="match_window_expired")
        return _result_payload(intent, request, source)

    client, session_path, authorized, _account_id = await _connect_authorized()
    try:
        if not authorized:
            raise HTTPException(status_code=409, detail="Telegram session is not authorized")
        async for _dialog in client.get_dialogs(limit=500):
            pass

        exact_candidate = None
        amount_candidate = None
        async for message in client.get_chat_history(int(source.telegram_group_id), limit=payload.limit):
            sender_id = _message_sender_id(message)
            if sender_id != source.telegram_sender_id:
                continue
            raw = message.text or message.caption or ""
            parsed = parse_aba_text(raw)
            if not parsed.trx_id or parsed.amount_minor is None:
                continue
            received_at = getattr(message, "date", None)
            if received_at and core.as_utc(received_at) < (
                core.as_utc(intent.created_at) - timedelta(seconds=60)
            ):
                continue

            parsed_remark = (parsed.remark or "").strip().upper() or None
            if parsed_remark == request.remark:
                exact_candidate = (message, raw, parsed, sender_id)
                break
            if parsed.amount_minor == request.payable_amount_minor and amount_candidate is None:
                amount_candidate = (message, raw, parsed, sender_id)

        candidate = exact_candidate or amount_candidate
    finally:
        await _disconnect(client, session_path)

    if candidate is None:
        return _result_payload(intent, request, source)

    message, raw, parsed, sender_id = candidate
    evidence = core.ingest_evidence(
        db,
        source_id=source.id,
        transport="telegram-acceptance-test",
        transport_message_id=str(message.id),
        sender_id=sender_id,
        raw_text=raw,
        received_at=getattr(message, "date", None),
        trx_id=parsed.trx_id,
        amount_minor=parsed.amount_minor,
        currency=parsed.currency,
        remark=parsed.remark,
        initial_state="SHADOW",
    )
    preview = _preview_match(db, request, evidence)
    trx = evidence.trx_id or ""
    _save_result(
        db,
        intent,
        **preview,
        evidence_id=evidence.id,
        trx_tail=trx[-8:],
        observed_amount_minor=evidence.amount_minor,
        observed_remark=evidence.remark,
    )
    return _result_payload(intent, request, source)


@router.post("/{intent_id}/cancel")
def acceptance_cancel(
    intent_id: str,
    _session: DashboardSession = Depends(require_dashboard_csrf),
    db: Session = Depends(get_db),
):
    intent, request, source = _require_test_intent(db, intent_id)
    if _metadata(intent).get("result") == "WAITING":
        reservation = db.scalar(
            select(models.AmountReservation).where(models.AmountReservation.request_id == request.id)
        )
        if reservation:
            db.delete(reservation)
        _save_result(db, intent, result="CANCELLED", result_reason="operator_cancelled")
    return _result_payload(intent, request, source)


@router.get("/{intent_id}/qr.png")
def acceptance_qr(
    intent_id: str,
    _session: DashboardSession = Depends(require_dashboard_session),
    db: Session = Depends(get_db),
):
    _intent, _request, source = _require_test_intent(db, intent_id)
    if not image_exists(source.id):
        raise HTTPException(status_code=409, detail="KHQR image has not been uploaded")
    meta = image_metadata(source.id)
    return FileResponse(
        image_path(source.id),
        media_type=meta.get("media_type") or "application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
