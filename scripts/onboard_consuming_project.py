#!/usr/bin/env python3
"""Onboard one consuming project onto KHQR Self-Develop.

Creates a Business and PaymentSource through the internal-admin API, then
optionally writes the returned client credentials to an owner-only env file.
It never creates a payment, touches Telegram sessions, or prints secrets.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import httpx


DEFAULT_API_URL = "http://127.0.0.1:8088"
DEFAULT_CURRENCY = "USD"


def _require(response: httpx.Response, expected_status: int = 200) -> dict[str, Any]:
    if response.status_code != expected_status:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise RuntimeError(f"API {response.status_code}: {detail}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("API returned a non-object response")
    return payload


def _write_credentials(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for key, value in values.items():
                if "\n" in value or "\r" in value:
                    raise ValueError(f"{key} contains a newline")
                handle.write(f"{key}={value}\n")
    finally:
        os.chmod(path, 0o600)


def onboard_project(
    *,
    api_base_url: str,
    internal_secret: str,
    project_name: str,
    project_slug: str,
    source_name: str,
    currency: str,
    merchant_alias: str,
    static_khqr: str,
    telegram_group_id: int,
    telegram_sender_id: int | None,
    enabled: bool = True,
    webhook_url: str | None = None,
    output_path: str | Path | None = None,
    timeout: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Create one reusable Business + PaymentSource pair."""
    api_base_url = api_base_url.rstrip("/")
    internal = httpx.Client(
        base_url=api_base_url,
        timeout=timeout,
        transport=transport,
        headers={"X-Internal-Secret": internal_secret},
    )
    try:
        business = _require(
            internal.post(
                "/internal/businesses",
                json={
                    "name": project_name,
                    "slug": project_slug,
                    "webhook_url": webhook_url,
                },
            )
        )
        business_id = str(business["id"])
        api_key = str(business["api_key"])
        webhook_secret = str(business.get("webhook_secret") or "")
        source = _require(
            internal.post(
                f"/internal/businesses/{business_id}/sources",
                json={
                    "name": source_name,
                    "currency": currency.upper(),
                    "telegram_group_id": telegram_group_id,
                    "telegram_sender_id": telegram_sender_id,
                    "merchant_alias": merchant_alias,
                    "static_khqr": static_khqr,
                    "enabled": enabled,
                },
            )
        )
        if enabled and not source.get("ready"):
            raise RuntimeError("enabled payment source is not ready")

        result: dict[str, Any] = {
            "business_id": business_id,
            "source_id": str(source["id"]),
            "source_enabled": bool(source.get("enabled")),
            "source_ready": bool(source.get("ready")),
            "currency": currency.upper(),
            "credentials": {
                "api_key": api_key,
                "webhook_secret": webhook_secret,
            },
        }

        if output_path:
            env_path = Path(output_path)
            _write_credentials(
                env_path,
                {
                    "KHQR_API_URL": api_base_url,
                    "KHQR_API_KEY": api_key,
                    "KHQR_WEBHOOK_SECRET": webhook_secret,
                    "KHQR_SOURCE_ID": str(source["id"]),
                    "KHQR_BUSINESS_ID": business_id,
                    "KHQR_PROJECT_SLUG": project_slug,
                    "KHQR_CURRENCY": currency.upper(),
                },
            )
            result["credential_file"] = str(env_path)

        result["redacted"] = {
            key: value for key, value in result.items() if key != "credentials"
        }
        return result
    finally:
        internal.close()


def _required_value(parser: argparse.ArgumentParser, value: str | None, name: str) -> str:
    if value is None or not str(value).strip():
        parser.error(f"{name} is required")
    return str(value).strip()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Onboard a consuming project onto KHQR Self-Develop"
    )
    parser.add_argument(
        "--api-base-url",
        default=os.getenv("KHQR_API_URL", DEFAULT_API_URL),
    )
    parser.add_argument("--internal-secret", default=os.getenv("INTERNAL_SECRET"))
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--project-slug", required=True)
    parser.add_argument("--source-name", default="ABA PayWay")
    parser.add_argument("--currency", default=os.getenv("KHQR_CURRENCY", DEFAULT_CURRENCY))
    parser.add_argument("--merchant-alias", default=os.getenv("KHQR_MERCHANT_ALIAS"))
    parser.add_argument("--static-khqr", default=os.getenv("KHQR_STATIC_KHQR"))
    parser.add_argument(
        "--telegram-group-id",
        type=int,
        default=os.getenv("KHQR_TELEGRAM_GROUP_ID"),
    )
    parser.add_argument(
        "--telegram-sender-id",
        type=int,
        default=os.getenv("KHQR_TELEGRAM_SENDER_ID"),
    )
    parser.add_argument("--webhook-url", default=os.getenv("KHQR_WEBHOOK_URL"))
    parser.add_argument(
        "--disabled",
        action="store_true",
        help="Create a staged disabled source; sender ID may be omitted until observed",
    )
    parser.add_argument(
        "--output-file",
        help="Optional owner-only client env file; secrets never print to stdout",
    )
    args = parser.parse_args()

    if not args.internal_secret:
        parser.error("INTERNAL_SECRET is required")
    merchant_alias = _required_value(
        parser, args.merchant_alias, "--merchant-alias or KHQR_MERCHANT_ALIAS"
    )
    static_khqr = _required_value(
        parser, args.static_khqr, "--static-khqr or KHQR_STATIC_KHQR"
    )
    if args.telegram_group_id is None:
        parser.error("--telegram-group-id or KHQR_TELEGRAM_GROUP_ID is required")
    if args.telegram_sender_id is None and not args.disabled:
        parser.error("--telegram-sender-id or KHQR_TELEGRAM_SENDER_ID is required unless --disabled")

    try:
        result = onboard_project(
            api_base_url=args.api_base_url,
            internal_secret=args.internal_secret,
            project_name=args.project_name,
            project_slug=args.project_slug,
            source_name=args.source_name,
            currency=args.currency,
            merchant_alias=merchant_alias,
            static_khqr=static_khqr,
            telegram_group_id=args.telegram_group_id,
            telegram_sender_id=args.telegram_sender_id,
            enabled=not args.disabled,
            webhook_url=args.webhook_url,
            output_path=args.output_file,
        )
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("KHQR ONBOARDING PASS")
    print(f"- business_id={result['business_id']}")
    print(f"- source_id={result['source_id']}")
    print(f"- source_enabled={str(result['source_enabled']).lower()}")
    print(f"- source_ready={str(result['source_ready']).lower()}")
    if result.get("credential_file"):
        print(f"- credential_file={result['credential_file']} (mode 0600)")
    print("- secrets=redacted-from-stdout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
