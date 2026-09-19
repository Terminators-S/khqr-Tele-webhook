from pathlib import Path
import os
import stat

from sqlalchemy import create_engine, make_url, text

from app import __version__
from app.config import get_settings
from app.telegram_credentials import load_telegram_credentials


LOCKED = {
    "checkout_ttl_seconds": 480,
    "late_match_grace_seconds": 300,
    "history_recovery_seconds": 604800,
    "offset_max_minor": 29,
}


def main() -> int:
    settings = get_settings()
    errors: list[str] = []
    warnings: list[str] = []

    try:
        url = make_url(settings.database_url)
    except Exception:
        errors.append("DATABASE_URL is invalid")
        url = None

    if url is not None:
        if not url.drivername.startswith("postgresql"):
            errors.append("release requires PostgreSQL")
        if url.host not in {"127.0.0.1", "localhost"} or url.port != 55432:
            errors.append("release gate must target isolated loopback PostgreSQL on port 55432")


    weak_secrets = {
        "",
        "dev-internal-secret-change-me",
        "test-internal-secret-32-characters",
        "replace-with-at-least-48-random-characters",
    }
    if settings.internal_secret in weak_secrets or len(settings.internal_secret) < 48:
        errors.append("INTERNAL_SECRET must be a non-default secret with at least 48 characters")

    for name, expected in LOCKED.items():
        actual = getattr(settings, name)
        if actual != expected:
            errors.append(f"{name} must remain {expected} for v0.1; got {actual}")

    if not settings.telegram_shadow_only:
        errors.append("TELEGRAM_SHADOW_ONLY must be true for the core RC")
    if settings.allow_live_telegram:
        errors.append("ALLOW_LIVE_TELEGRAM must be false for the core RC")
    if settings.allow_shadow_promotion:
        errors.append("ALLOW_SHADOW_PROMOTION must be false for the core RC")

    env_path = Path(".env")
    if not env_path.exists():
        errors.append(".env is missing")
    else:
        mode = stat.S_IMODE(env_path.stat().st_mode)
        if mode & 0o077:
            errors.append(f".env permissions must be owner-only; got {oct(mode)}")


    if url is not None and not errors:
        try:
            engine = create_engine(settings.database_url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:
            errors.append(f"PostgreSQL connectivity failed: {type(exc).__name__}")

    if not load_telegram_credentials():
        warnings.append("Telegram API credentials missing; core release is still allowed")

    if errors:
        print(f"KHQR CORE RELEASE PREFLIGHT BLOCKED version={__version__}")
        for error in errors:
            print(f"- ERROR: {error}")
        for warning in warnings:
            print(f"- WARN: {warning}")
        return 1

    print(f"KHQR CORE RELEASE PREFLIGHT PASS version={__version__}")
    print("- database=isolated-postgresql")
    print("- secrets=strong-and-owner-only")
    print("- payment-contract=locked")
    print("- telegram=shadow-safe")
    for warning in warnings:
        print(f"- WARN: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
