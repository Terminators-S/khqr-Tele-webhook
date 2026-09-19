from pathlib import Path

from app.telegram_session import session_location


def test_relative_session_path_is_split_for_pyrogram():
    path, name, workdir = session_location("runtime/khqr_collector")
    assert path.name == "khqr_collector.session"
    assert name == "khqr_collector"
    assert workdir == path.parent
    assert workdir.name == "runtime"


def test_existing_session_suffix_is_not_duplicated():
    path, name, workdir = session_location("runtime/khqr_collector.session")
    assert path.name == "khqr_collector.session"
    assert name == "khqr_collector"
    assert workdir == path.parent


def test_absolute_session_path_keeps_explicit_workdir():
    path, name, workdir = session_location("/runtime/khqr_collector")
    assert path == Path("/runtime/khqr_collector.session")
    assert name == "khqr_collector"
    assert workdir == Path("/runtime")
