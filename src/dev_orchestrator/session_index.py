"""Durable session↔ticket index (design: session management).

Maps a Linear issue key → the Claude session that implemented it, independent of
the runs activity log. This is the authoritative recovery memory for Fix/resume
and for labelling sessions in the radar. Kept in its own file so clearing the
runs table (runs/*.jsonl) never wipes the recovery mapping.

    { "BRI-61": {"session_id": "992e8dbc-…", "cli": "claude", "agent": "opus-4.8"} }
"""

from __future__ import annotations

import json
from pathlib import Path


def load(path: str) -> dict[str, dict]:
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record(path: str, issue_key: str, session_id: str, cli: str, agent: str) -> None:
    """Upsert the session that implemented `issue_key` (latest wins). No-op if incomplete."""
    if not (issue_key and session_id):
        return
    idx = load(path)
    idx[issue_key] = {"session_id": session_id, "cli": cli, "agent": agent}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(idx, indent=2))


def get(path_or_index: str | dict, issue_key: str) -> dict | None:
    idx = path_or_index if isinstance(path_or_index, dict) else load(path_or_index)
    return idx.get(issue_key)


def by_session(path: str) -> dict[str, str]:
    """session_id → issue_key (for labelling sessions in the radar)."""
    return {v["session_id"]: k for k, v in load(path).items() if v.get("session_id")}
