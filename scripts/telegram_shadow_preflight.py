from pathlib import Path

from sqlalchemy import select

from app import core, models
from app.config import get_settings
from app.db import SessionLocal


def main() -> int:
    settings = get_settings()
    errors: list[str] = []

    if not settings.telegram_shadow_only:
        errors.append("TELEGRAM_SHADOW_ONLY must be true for shadow preflight")
    if settings.allow_live_telegram:
        errors.append("ALLOW_LIVE_TELEGRAM must remain false during shadow phase")
    if settings.allow_shadow_promotion:
        errors.append("ALLOW_SHADOW_PROMOTION must remain false during shadow phase")
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        errors.append("Telegram API credentials are not configured")

    session_base = Path(settings.telegram_session_name).expanduser()
    session_path = session_base if session_base.suffix == ".session" else Path(str(session_base) + ".session")
    if not session_path.exists():
        errors.append(f"dedicated Telegram session is missing: {session_path}")

    db = SessionLocal()
    try:
        sources = list(
            db.scalars(select(models.PaymentSource).where(models.PaymentSource.enabled.is_(True)))
        )
        ready = [source for source in sources if core.source_ready(source)]
        if not ready:
            errors.append("no enabled, fully configured payment source is ready")
    finally:
        db.close()

    if errors:
        print("TELEGRAM_SHADOW_PREFLIGHT BLOCKED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("TELEGRAM_SHADOW_PREFLIGHT PASS")
    print(f"- ready_sources={len(ready)}")
    print(f"- session={session_path}")
    print("- shadow_only=true")
    print("- live_activation=false")
    print("- shadow_promotion=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
