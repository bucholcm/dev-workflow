"""Per-PR review state so review is multi-turn, not a one-shot verdict.

Tracks, for each PR, the pinned review session (claude → resumable) and how many
review turns have run, so round N remembers rounds 1…N-1 instead of reviewing
from scratch. Stored in its own file (survives clearing runs/*.jsonl, gitignored).

    { "66": {"session_id": "…", "cli": "codex", "turns": 2} }
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


def get(path: str, pr_id: int) -> dict | None:
    return load(path).get(str(pr_id))


def record(path: str, pr_id: int, session_id: str, cli: str, turns: int) -> None:
    idx = load(path)
    idx[str(pr_id)] = {"session_id": session_id, "cli": cli, "turns": turns}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(idx, indent=2))
