"""Durable session↔ticket index: survives clearing the runs log."""

from dev_orchestrator import session_index


def test_record_and_get(tmp_path):
    path = str(tmp_path / "session_index.json")
    assert session_index.load(path) == {}
    session_index.record(path, "BRI-61", "992e8dbc", "claude", "opus-4.8")
    entry = session_index.get(path, "BRI-61")
    assert entry == {"session_id": "992e8dbc", "cli": "claude", "agent": "opus-4.8"}
    assert session_index.get(path, "BRI-99") is None


def test_latest_write_wins(tmp_path):
    path = str(tmp_path / "session_index.json")
    session_index.record(path, "BRI-61", "sess-old", "claude", "opus-4.8")
    session_index.record(path, "BRI-61", "sess-new", "claude", "opus-4.8")
    assert session_index.get(path, "BRI-61")["session_id"] == "sess-new"


def test_incomplete_records_are_ignored(tmp_path):
    path = str(tmp_path / "session_index.json")
    session_index.record(path, "BRI-61", "", "claude", "opus-4.8")   # no session
    session_index.record(path, "", "sess-x", "claude", "opus-4.8")   # no key
    assert session_index.load(path) == {}


def test_by_session_reverse_map(tmp_path):
    path = str(tmp_path / "session_index.json")
    session_index.record(path, "BRI-61", "sess-a", "claude", "opus-4.8")
    session_index.record(path, "BRI-70", "sess-b", "claude", "opus-4.8")
    assert session_index.by_session(path) == {"sess-a": "BRI-61", "sess-b": "BRI-70"}
