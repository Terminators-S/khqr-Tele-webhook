import asyncio
import os

from app.config import get_settings
from app.telegram_session import session_location


async def main() -> int:
    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("Telegram API credentials are not configured.")
        return 1
    if not settings.telegram_shadow_only:
        print("Refusing session bootstrap while TELEGRAM_SHADOW_ONLY is false.")
        return 1
    if settings.allow_live_telegram:
        print("Refusing session bootstrap while ALLOW_LIVE_TELEGRAM is true.")
        return 1
    if settings.allow_shadow_promotion:
        print("Refusing session bootstrap while ALLOW_SHADOW_PROMOTION is true.")
        return 1

    session_path, client_name, workdir = session_location(
        settings.telegram_session_name
    )
    workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(workdir, 0o700)

    from pyrogram import Client

    old_umask = os.umask(0o077)
    client = Client(
        client_name,
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        workdir=str(workdir),
    )
    try:
        print("Starting dedicated Telegram session bootstrap.")
        print(
            "Complete login directly in this terminal; "
            "do not paste Telegram login codes into chat."
        )
        await client.start()
        me = await client.get_me()
        print(
            "Dedicated session authorized for "
            f"account id={me.id} username={me.username or '-'}"
        )
    finally:
        try:
            if getattr(client, "is_connected", False):
                await client.stop()
        finally:
            os.umask(old_umask)
            if session_path.exists():
                os.chmod(session_path, 0o600)

    print(f"Session saved at: {session_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
