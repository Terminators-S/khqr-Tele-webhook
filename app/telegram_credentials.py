from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .config import get_settings


@dataclass(frozen=True)
class TelegramCredentials:
    api_id: int
    api_hash: str
    source: str


def credentials_path() -> Path:
    return Path(
        get_settings().telegram_credentials_path
    ).expanduser().resolve()


def _validate(api_id, api_hash) -> tuple[int, str]:
    try:
        parsed_id = int(api_id)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Telegram API ID must be an integer") from exc
    parsed_hash = str(api_hash or "").strip()
    if parsed_id <= 0:
        raise RuntimeError("Telegram API ID must be positive")
    if len(parsed_hash) < 16:
        raise RuntimeError("Telegram API hash is too short")
    return parsed_id, parsed_hash


def load_telegram_credentials() -> TelegramCredentials | None:
    settings = get_settings()
    if settings.telegram_api_id and settings.telegram_api_hash:
        api_id, api_hash = _validate(
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        return TelegramCredentials(api_id, api_hash, "environment")

    path = credentials_path()
    if not path.exists():
        return None
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError(
            "Telegram credential file must be owner-only (0600)"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Telegram credential file is invalid") from exc
    api_id, api_hash = _validate(
        payload.get("api_id"),
        payload.get("api_hash"),
    )
    return TelegramCredentials(api_id, api_hash, "runtime")


def save_telegram_credentials(
    api_id: int,
    api_hash: str,
) -> TelegramCredentials:
    parsed_id, parsed_hash = _validate(api_id, api_hash)
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    temp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(
        temp,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {"api_id": parsed_id, "api_hash": parsed_hash},
                handle,
                separators=(",", ":"),
            )
            handle.write("\n")
        os.replace(temp, path)
        os.chmod(path, 0o600)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return TelegramCredentials(parsed_id, parsed_hash, "runtime")


def telegram_credentials_status() -> dict:
    credentials = load_telegram_credentials()
    path = credentials_path()
    return {
        "configured": credentials is not None,
        "source": credentials.source if credentials else None,
        "runtime_file_exists": path.exists(),
        "owner_only": (
            stat.S_IMODE(path.stat().st_mode) == 0o600
            if path.exists()
            else False
        ),
    }
