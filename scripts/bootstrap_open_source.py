#!/usr/bin/env python3
"""Create a safe first-run .env for open-source installations."""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"


def generate_values(
    telegram_api_id: str | None = None,
    telegram_api_hash: str | None = None,
    cookie_secure: bool = False,
) -> dict[str, str]:
    postgres_password = secrets.token_urlsafe(36)
    internal_secret = secrets.token_urlsafe(48)
    return {
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": (
            "postgresql+psycopg2://khqr:"
            + postgres_password
            + "@127.0.0.1:55432/khqr"
        ),
        "INTERNAL_SECRET": internal_secret,
        "TELEGRAM_API_ID": str(telegram_api_id or ""),
        "TELEGRAM_API_HASH": str(telegram_api_hash or ""),
        "DASHBOARD_COOKIE_SECURE": "true" if cookie_secure else "false",
    }


def render_env(template: str, values: dict[str, str]) -> str:
    output: list[str] = []
    seen: set[str] = set()
    for line in template.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            output.append(line)
            continue
        key, _value = line.split("=", 1)
        if key in values:
            output.append(key + "=" + values[key])
            seen.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in seen:
            output.append(key + "=" + value)
    return "\n".join(output).rstrip() + "\n"


def write_env(
    target: Path,
    template_path: Path,
    values: dict[str, str],
    force: bool,
) -> None:
    if target.exists() and not force:
        raise RuntimeError(
            f"{target} already exists; refusing to overwrite it without --force"
        )
    template = template_path.read_text(encoding="utf-8")
    target.write_text(render_env(template, values), encoding="utf-8")
    os.chmod(target, 0o600)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bootstrap a secure KHQR Self-Develop installation"
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--telegram-api-id")
    parser.add_argument("--telegram-api-hash")
    parser.add_argument(
        "--cookie-secure",
        action="store_true",
        help="Set DASHBOARD_COOKIE_SECURE=true for an HTTPS deployment",
    )
    parser.add_argument(
        "--no-secret-output",
        action="store_true",
        help="Do not print the generated dashboard/internal secret",
    )
    args = parser.parse_args()

    if bool(args.telegram_api_id) != bool(args.telegram_api_hash):
        parser.error(
            "--telegram-api-id and --telegram-api-hash must be supplied together"
        )

    values = generate_values(
        telegram_api_id=args.telegram_api_id,
        telegram_api_hash=args.telegram_api_hash,
        cookie_secure=args.cookie_secure,
    )
    try:
        write_env(TARGET, EXAMPLE, values, args.force)
    except RuntimeError as exc:
        print(f"BOOTSTRAP BLOCKED: {exc}")
        return 1

    print("KHQR OPEN-SOURCE BOOTSTRAP COMPLETE")
    print(f"- env={TARGET} mode=0600")
    print("- dashboard=http://127.0.0.1:8088/dashboard")
    if not args.no_secret_output:
        print(f"- dashboard_secret={values['INTERNAL_SECRET']}")
    print("- next=./scripts/install.sh")
    if not args.telegram_api_id:
        print("- telegram=not configured; add API ID/hash before Telegram setup")
    print("- live_telegram=false")
    print("- shadow_promotion=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
