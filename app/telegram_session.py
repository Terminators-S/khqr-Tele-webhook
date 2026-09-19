from __future__ import annotations

from pathlib import Path


def session_location(session_name: str) -> tuple[Path, str, Path]:
    """Return the concrete session path plus Pyrogram name/workdir.

    Pyrogram expects the session name and workdir separately. Passing a
    path-like name can fail to create the SQLite session file even when the
    parent directory exists.
    """
    base = Path(session_name).expanduser()
    session_path = (
        base if base.suffix == ".session" else Path(str(base) + ".session")
    ).resolve()
    client_name = session_path.name.removesuffix(".session")
    workdir = session_path.parent
    if not client_name:
        raise ValueError("Telegram session name is empty")
    return session_path, client_name, workdir
