"""Claude session radar: scan ~/.claude/projects transcripts and flag which ones
need the human's attention (design: attention radar).

Claude Code writes one JSONL transcript per session at
    ~/.claude/projects/<dashed-cwd>/<session-uuid>.jsonl
Each line is a record with a `type` field; content records carry `cwd`,
`gitBranch`, `timestamp`, `message.role` and `message.content[].type`
(text|thinking|tool_use|tool_result). We read only the TAIL of each file (these
grow to multiple MB) and derive a coarse attention class:

  running          — transcript modified within `idle_seconds` (active; leave it)
  waiting_approval — last act is an assistant tool_use with no following
                     tool_result, and the file is idle → a tool call is parked
                     waiting for the human to approve it
  waiting_input    — an assistant text block is the last thing said (Claude spoke
                     last, no later user turn) and the file is idle → the ball is
                     in the human's court
  idle             — nothing pending

Matching to orchestrator work is best-effort: cwd under TARGET_REPO_PATH (or its
worktrees), an `ai/<KEY>-…` branch → Linear key, or a pinned app session id.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import deque
from pathlib import Path

from .models import Attention, SessionInfo

logger = logging.getLogger(__name__)

_TAIL_LINES = 60
_BRANCH_KEY_RE = re.compile(r"ai/([A-Za-z]+-\d+)-")
# A trailing assistant message that reads like it is handing the turn back to the human.
_QUESTION_RE = re.compile(r"\?\s*$")
_ASK_RE = re.compile(
    r"\b(let me know|which (would|option|one)|do you want|should i|would you like|"
    r"waiting (for|on) (your|you)|need(s)? your (input|answer|decision)|"
    r"please (confirm|clarify|advise|choose)|how would you like|shall i)\b",
    re.IGNORECASE,
)


def _tail(path: Path, n: int = _TAIL_LINES) -> list[str]:
    """Return the last ~n lines of a possibly large file, cheaply."""
    try:
        with path.open("r", errors="replace") as fh:
            return list(deque(fh, maxlen=n))
    except OSError:
        return []


def _content_types(rec: dict) -> list[str]:
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if isinstance(content, list):
        return [b.get("type") for b in content if isinstance(b, dict)]
    return []


def _text_of(rec: dict) -> str:
    """Concatenated text of a record's text blocks (empty if none)."""
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _role(rec: dict) -> str | None:
    msg = rec.get("message")
    return msg.get("role") if isinstance(msg, dict) else None


def _looks_like_a_question(text: str) -> bool:
    """True when the assistant's closing message hands the turn back to the human."""
    tail = (text or "").strip()[-400:]
    return bool(_QUESTION_RE.search(tail) or _ASK_RE.search(tail))


def _classify(records: list[dict], *, idle: bool, recent: bool) -> Attention:
    """Attention class from the tail records.

    `idle`   — file older than the running threshold (not actively streaming).
    `recent` — file young enough that a pending turn is still worth surfacing;
               stale sessions are treated as done (IDLE), not "waiting on you".
    """
    if not idle:
        return Attention.RUNNING
    # Index of the last content-bearing assistant/user turn.
    last_i = None
    for i in range(len(records) - 1, -1, -1):
        if _role(records[i]) in ("assistant", "user") and _content_types(records[i]):
            last_i = i
            break
    if last_i is None:
        return Attention.IDLE
    rec = records[last_i]
    if _role(rec) != "assistant":
        return Attention.IDLE  # user spoke last → they've already replied / are mid-turn
    types = _content_types(rec)
    # A tool_use with no later tool_result → parked awaiting the human's approval
    # (strong signal; surfaced whenever recent, no question heuristic needed).
    if "tool_use" in types:
        later = records[last_i + 1:]
        has_result = any("tool_result" in _content_types(r) for r in later)
        if not has_result:
            return Attention.WAITING_APPROVAL if recent else Attention.IDLE
    # Assistant spoke last: only "needs you" if it actually asked something AND is recent.
    if "text" in types and recent and _looks_like_a_question(_text_of(rec)):
        return Attention.WAITING_INPUT
    return Attention.IDLE


def _scan_file(path: Path, idle_seconds: int, waiting_max_age: int, now: float) -> SessionInfo | None:
    lines = _tail(path)
    if not lines:
        return None
    records: list[dict] = []
    title = cwd = git_branch = mode = last_active = ""
    session_id = path.stem
    for ln in lines:
        try:
            rec = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        records.append(rec)
        t = rec.get("type")
        if t == "ai-title" and rec.get("aiTitle"):
            title = str(rec["aiTitle"])
        if t == "mode" and rec.get("mode"):
            mode = str(rec["mode"])
        if rec.get("cwd"):
            cwd = str(rec["cwd"])
        if rec.get("gitBranch"):
            git_branch = str(rec["gitBranch"])
        if rec.get("sessionId"):
            session_id = str(rec["sessionId"])
        ts = rec.get("timestamp")
        if ts and str(ts) > last_active:
            last_active = str(ts)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    age = now - mtime
    idle = age > idle_seconds
    recent = age <= waiting_max_age
    attention = _classify(records, idle=idle, recent=recent)
    km = _BRANCH_KEY_RE.search(git_branch or "")
    return SessionInfo(
        session_id=session_id,
        title=title or "(untitled session)",
        cwd=cwd,
        git_branch=git_branch,
        last_active=last_active,
        attention=attention,
        mode=mode,
        file=str(path),
        linear_key=km.group(1) if km else None,
    )


_ATTENTION_RANK = {
    Attention.WAITING_APPROVAL: 0,
    Attention.WAITING_INPUT: 1,
    Attention.RUNNING: 2,
    Attention.IDLE: 3,
}


def scan_sessions(
    projects_dir: str,
    *,
    idle_seconds: int = 45,
    waiting_max_age_seconds: int = 172800,  # 2 days — older pending turns aren't "waiting on you"
    now: float,
    target_repo_path: str = "",
    app_session_ids: set[str] | None = None,
    limit: int = 60,
) -> list[SessionInfo]:
    """Scan all session transcripts and return them, attention-first then most-recent.

    `now` is injected (epoch seconds) to keep this testable. `app_session_ids` are
    orchestrator-pinned ids used to mark sessions as app-managed.
    """
    root = Path(projects_dir).expanduser()
    if not root.is_dir():
        return []
    app_ids = app_session_ids or set()
    target = os.path.abspath(target_repo_path) if target_repo_path else ""

    files = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    out: list[SessionInfo] = []
    for path in files[: limit * 2]:  # scan a bit extra; we sort + trim below
        info = _scan_file(path, idle_seconds, waiting_max_age_seconds, now)
        if not info:
            continue
        if target and info.cwd:
            info.repo_matched = os.path.abspath(info.cwd).startswith(target)
        info.app_managed = info.session_id in app_ids
        out.append(info)

    # Two stable passes: newest-first, then attention-tier-first (tier wins, recency breaks ties).
    out.sort(key=lambda s: s.last_active, reverse=True)
    out.sort(key=lambda s: _ATTENTION_RANK.get(s.attention, 9))
    return out[:limit]


def needs_attention(sessions: list[SessionInfo]) -> list[SessionInfo]:
    return [s for s in sessions if s.attention in (Attention.WAITING_APPROVAL, Attention.WAITING_INPUT)]
