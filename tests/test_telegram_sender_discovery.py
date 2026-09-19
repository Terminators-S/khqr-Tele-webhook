from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import telegram_sender_discovery as discovery


def message(message_id, sender_id, text):
    sender = None if sender_id is None else SimpleNamespace(id=sender_id)
    return SimpleNamespace(
        id=message_id,
        from_user=sender,
        sender_chat=None,
        text=text,
        caption=None,
        date=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )


def test_summary_reports_one_unanimous_sender():
    rows = [
        message(1, 777, "$1.41 paid via ABA PAY. Trx. ID: 178972245859414"),
        message(2, 777, "USD 2.50 paid. Transaction ID: 178972245859415"),
        message(3, 999, "not a payment notification"),
    ]
    result = discovery.summarize_messages(rows)

    assert result["parsed_messages"] == 2
    assert result["candidate_senders"] == [{"sender_id": 777, "count": 2}]
    assert len(result["rows"]) == 2
    assert all(row["sender_id"] == 777 for row in result["rows"])


def test_summary_exposes_ambiguous_senders():
    rows = [
        message(1, 777, "$1.00 paid. Trx. ID: 12345678"),
        message(2, 888, "$2.00 paid. Trx. ID: 87654321"),
    ]
    result = discovery.summarize_messages(rows)

    assert result["parsed_messages"] == 2
    assert result["candidate_senders"] == [
        {"sender_id": 777, "count": 1},
        {"sender_id": 888, "count": 1},
    ]


def test_summary_counts_parseable_message_without_sender():
    rows = [
        message(1, None, "$3.00 paid. Trx. ID: 123456789"),
    ]
    result = discovery.summarize_messages(rows)

    assert result["parsed_messages"] == 1
    assert result["candidate_senders"] == []

def test_session_path_must_live_directly_under_runtime(monkeypatch):
    fake = SimpleNamespace(telegram_session_name="runtime/khqr_collector")
    monkeypatch.setattr(discovery, "get_settings", lambda: fake)
    assert discovery._session_path() == (
        discovery.REPO_ROOT / "runtime" / "khqr_collector.session"
    ).resolve()

    unsafe = SimpleNamespace(telegram_session_name="/root/CreativeStudioWeb/backend/admin_session")
    monkeypatch.setattr(discovery, "get_settings", lambda: unsafe)
    with pytest.raises(RuntimeError, match="runtime"):
        discovery._session_path()


def test_result_file_is_owner_only(tmp_path):
    path = tmp_path / "discovery.json"
    discovery._write_result(path, {"trusted_sender_id": 777})
    assert path.exists()
    assert path.stat().st_mode & 0o077 == 0
