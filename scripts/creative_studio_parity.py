#!/usr/bin/env python3
"""Read-only parity exporter for the Creative Studio migration.

Reads the standalone SHADOW observation artifact, queries only matching
Creative Studio ABA transaction IDs, and writes aggregate parity evidence.
It prints no payer names, raw notification text, or transaction IDs.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from sqlalchemy import bindparam, create_engine, text


DEFAULT_ENV = Path("/root/CreativeStudioWeb/backend/.env")
DEFAULT_OBSERVATION = Path("runtime/telegram-shadow-observation.json")
DEFAULT_OUTPUT = Path("runtime/creative-studio-parity.json")


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def resolve_database_url(env_path: Path) -> str:
    values = dotenv_values(env_path)
    database_url = str(values.get("DATABASE_URL") or "sqlite:///./sql_app.db")
    if database_url.startswith("sqlite:///./"):
        relative = database_url.removeprefix("sqlite:///./")
        return "sqlite:///" + str((env_path.parent / relative).resolve())
    return database_url


def load_observation(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError("SHADOW observation must be a JSON object")
    rows = value.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("SHADOW observation contains no payment rows")
    return value


def fetch_incumbent_rows(
    database_url: str,
    trx_ids: list[str],
) -> dict[str, dict[str, Any]]:
    engine = create_engine(database_url, pool_pre_ping=True)
    statement = text(
        "SELECT trx_id, amount, received_at, source "
        "FROM aba_transactions WHERE trx_id IN :trx_ids"
    ).bindparams(bindparam("trx_ids", expanding=True))
    try:
        with engine.connect() as connection:
            result = connection.execute(statement, {"trx_ids": trx_ids})
            return {
                str(row.trx_id): {
                    "amount": row.amount,
                    "received_at": row.received_at,
                    "source": row.source,
                }
                for row in result
            }
    finally:
        engine.dispose()


def compare(
    observation: dict[str, Any],
    incumbent: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = observation["rows"]
    group_id = str(observation.get("group_id") or "")
    summary: dict[str, Any] = {
        "shadow_rows": len(rows),
        "trx_found": 0,
        "amount_match": 0,
        "time_within_5s": 0,
        "source_group_match": 0,
        "source_generic_telegram": 0,
        "source_other": 0,
        "missing": 0,
        "max_time_delta_seconds": 0.0,
    }

    for row in rows:
        existing = incumbent.get(str(row.get("trx_id") or ""))
        if not existing:
            summary["missing"] += 1
            continue

        summary["trx_found"] += 1
        expected_amount = int(row["amount_minor"]) / 100
        if abs(float(existing["amount"]) - expected_amount) < 0.000001:
            summary["amount_match"] += 1

        shadow_time = parse_dt(row.get("received_at"))
        incumbent_time = parse_dt(existing.get("received_at"))
        if shadow_time and incumbent_time:
            delta = abs((shadow_time - incumbent_time).total_seconds())
            summary["max_time_delta_seconds"] = max(
                float(summary["max_time_delta_seconds"]),
                delta,
            )
            if delta <= 5:
                summary["time_within_5s"] += 1

        source = str(existing.get("source") or "")
        if group_id and group_id in source:
            summary["source_group_match"] += 1
        elif source.lower().startswith("telegram"):
            summary["source_generic_telegram"] += 1
        else:
            summary["source_other"] += 1

    total = int(summary["shadow_rows"])
    summary["parity_pass"] = bool(
        total > 0
        and summary["trx_found"] == total
        and summary["amount_match"] == total
        and summary["time_within_5s"] == total
        and summary["source_group_match"] == total
        and summary["missing"] == 0
        and summary["source_other"] == 0
    )
    return summary


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
    finally:
        os.chmod(path, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare KHQR SHADOW evidence with Creative Studio"
    )
    parser.add_argument("--creative-env", type=Path, default=DEFAULT_ENV)
    parser.add_argument(
        "--observation",
        type=Path,
        default=DEFAULT_OBSERVATION,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    try:
        observation = load_observation(args.observation)
        trx_ids = [str(row["trx_id"]) for row in observation["rows"]]
        incumbent = fetch_incumbent_rows(
            resolve_database_url(args.creative_env),
            trx_ids,
        )
        summary = compare(observation, incumbent)
        write_summary(args.output, summary)
    except Exception as exc:
        print(f"CREATIVE_STUDIO_PARITY BLOCKED: {type(exc).__name__}: {exc}")
        return 1

    print("CREATIVE_STUDIO_PARITY " + ("PASS" if summary["parity_pass"] else "BLOCKED"))
    for key in (
        "shadow_rows",
        "trx_found",
        "amount_match",
        "time_within_5s",
        "source_group_match",
        "source_generic_telegram",
        "source_other",
        "missing",
    ):
        print(f"- {key}={summary[key]}")
    print(
        "- max_time_delta_seconds="
        f"{float(summary['max_time_delta_seconds']):.3f}"
    )
    print(f"- output={args.output} (mode 0600)")
    print("- incumbent_writes=none")
    print("- raw_payment_output=none")
    return 0 if summary["parity_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
