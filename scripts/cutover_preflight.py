#!/usr/bin/env python3
"""Fail-closed cutover readiness check.

This command never enables a source or changes any fuse. It only proves that a
staged source has enough SHADOW evidence and a passing parity artifact.
"""
from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path

from sqlalchemy import func, select

from app import core, models
from app.config import get_settings
from app.db import SessionLocal
from app.telegram_session import session_location


DEFAULT_MIN_SHADOW = 10


def load_parity(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"parity artifact is missing: {path}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError("parity artifact must be a JSON object")
    return value


def parity_passes(value: dict) -> bool:
    rows = int(value.get("shadow_rows") or 0)
    return bool(
        value.get("parity_pass") is True
        and rows > 0
        and int(value.get("trx_found") or 0) == rows
        and int(value.get("amount_match") or 0) == rows
        and int(value.get("time_within_5s") or 0) == rows
        and int(value.get("source_group_match") or 0) == rows
        and int(value.get("missing") or 0) == 0
        and int(value.get("source_other") or 0) == 0
    )


def run_preflight(source_id: str, min_shadow: int, parity_path: Path) -> list[str]:
    settings = get_settings()
    errors: list[str] = []

    if not settings.telegram_shadow_only:
        errors.append("TELEGRAM_SHADOW_ONLY must remain true before cutover")
    if settings.allow_live_telegram:
        errors.append("ALLOW_LIVE_TELEGRAM must remain false before cutover")
    if settings.allow_shadow_promotion:
        errors.append("ALLOW_SHADOW_PROMOTION must remain false before cutover")

    session_path, _name, _workdir = session_location(settings.telegram_session_name)
    if not session_path.exists():
        errors.append("dedicated Telegram session is missing")
    elif stat.S_IMODE(session_path.stat().st_mode) & 0o077:
        errors.append("dedicated Telegram session must be mode 0600")

    with SessionLocal() as db:
        source = db.get(models.PaymentSource, source_id)
        if not source:
            errors.append("payment source not found")
            return errors
        if source.enabled:
            errors.append("payment source must remain disabled during cutover preflight")
        if not core.source_configured(source):
            errors.append("payment source configuration is incomplete")

        total = db.scalar(
            select(func.count(models.PaymentEvidence.id)).where(
                models.PaymentEvidence.source_id == source_id
            )
        ) or 0
        shadow = db.scalar(
            select(func.count(models.PaymentEvidence.id)).where(
                models.PaymentEvidence.source_id == source_id,
                models.PaymentEvidence.state == "SHADOW",
            )
        ) or 0
        allocations = db.scalar(
            select(func.count(models.PaymentAllocation.id)).join(
                models.PaymentEvidence,
                models.PaymentAllocation.evidence_id == models.PaymentEvidence.id,
            ).where(models.PaymentEvidence.source_id == source_id)
        ) or 0

    if total < min_shadow:
        errors.append(
            f"insufficient SHADOW sample: {total} observed, {min_shadow} required"
        )
    if shadow != total:
        errors.append(f"non-SHADOW evidence present: shadow={shadow} total={total}")
    if allocations:
        errors.append(f"payment allocations already exist during shadow phase: {allocations}")

    try:
        parity = load_parity(parity_path)
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    else:
        if not parity_passes(parity):
            errors.append("parity artifact does not prove full Trx/amount/time/source parity")
        elif int(parity.get("shadow_rows") or 0) < min_shadow:
            errors.append(
                "parity artifact sample is below the required SHADOW threshold"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check cutover readiness without changing state")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--min-shadow", type=int, default=DEFAULT_MIN_SHADOW)
    parser.add_argument(
        "--parity-artifact",
        default="runtime/creative-studio-parity.json",
    )
    args = parser.parse_args()
    if args.min_shadow < 1:
        parser.error("--min-shadow must be at least 1")

    errors = run_preflight(
        args.source_id,
        args.min_shadow,
        Path(args.parity_artifact),
    )
    if errors:
        print("CUTOVER_PREFLIGHT BLOCKED")
        for error in errors:
            print(f"- {error}")
        print("- state_changes=none")
        return 1

    print("CUTOVER_PREFLIGHT PASS")
    print(f"- source_id={args.source_id}")
    print(f"- min_shadow={args.min_shadow}")
    print("- state_changes=none")
    print("- explicit_cutover_approval_still_required=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
