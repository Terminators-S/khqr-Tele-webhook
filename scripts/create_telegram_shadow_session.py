import asyncio
from pathlib import Path

from app.config import get_settings


async def main() -> int:
    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print("Telegram API credentials are not configured.")
        return 1
    if not settings.telegram_shadow_only:
        print("Refusing session bootstrap while TELEGRAM_SHADOW_ONLY is false.")
        return 1

    session_base = Path(settings.telegram_session_name).expanduser()
    session_path = session_base if session_base.suffix == ".session" else Path(str(session_base) + ".session")
    session_path.parent.mkdir(parents=True, exist_ok=True)

    if session_path.exists():
        print(f"Dedicated session already exists: {session_path}")
        return 0

    from pyrogram import Client

    client = Client(
        str(session_base),
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
    )
    print("Starting one-time Telegram session bootstrap.")
    print("Complete the login prompts directly in this terminal; do not paste login codes into chat.")
    await client.start()
    me = await client.get_me()
    print(f"Session created for Telegram account id={me.id} username={me.username or '-'}")
    await client.stop()
    print(f"Session saved at: {session_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
