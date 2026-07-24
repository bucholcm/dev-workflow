"""Session radar: attention classification from transcript tails (heuristic)."""

import json
import os

from dev_orchestrator.models import Attention
from dev_orchestrator.sessions import scan_sessions

NOW = 1_000_000.0


def _asst(blocks, branch="ai/BRI-59-add-thing"):
    return {"type": "assistant", "cwd": "/repo", "gitBranch": branch, "timestamp": "2026-07-23T20:00:00Z",
            "message": {"role": "assistant", "content": blocks}}


def _user(blocks):
    return {"type": "user", "cwd": "/repo", "timestamp": "2026-07-23T20:01:00Z",
            "message": {"role": "user", "content": blocks}}


def _write(root, name, records, *, age_seconds):
    d = root / "-repo"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    mtime = NOW - age_seconds
    os.utime(f, (mtime, mtime))
    return f


def _scan(root, **kw):
    return scan_sessions(str(root), now=NOW, idle_seconds=45, waiting_max_age_seconds=172800, **kw)


def _by_id(sessions):
    return {s.session_id: s for s in sessions}


def test_waiting_input_when_assistant_ends_with_question(tmp_path):
    _write(tmp_path, "aaaa", [_asst([{"type": "text", "text": "Which option do you prefer?"}])], age_seconds=300)
    s = _by_id(_scan(tmp_path))["aaaa"]
    assert s.attention == Attention.WAITING_INPUT
    assert s.linear_key == "BRI-59"  # recovered from ai/<KEY>- branch


def test_statement_ending_is_not_attention(tmp_path):
    _write(tmp_path, "bbbb", [_asst([{"type": "text", "text": "All done, tests pass."}])], age_seconds=300)
    assert _by_id(_scan(tmp_path))["bbbb"].attention == Attention.IDLE


def test_pending_tool_use_is_waiting_approval(tmp_path):
    _write(tmp_path, "cccc", [_asst([{"type": "tool_use"}])], age_seconds=300)
    assert _by_id(_scan(tmp_path))["cccc"].attention == Attention.WAITING_APPROVAL


def test_tool_use_with_result_is_not_approval(tmp_path):
    _write(tmp_path, "dddd",
           [_asst([{"type": "tool_use"}]), _user([{"type": "tool_result"}])], age_seconds=300)
    # user replied (tool_result) → not waiting on approval, and user spoke last → idle
    assert _by_id(_scan(tmp_path))["dddd"].attention == Attention.IDLE


def test_recent_file_is_running(tmp_path):
    _write(tmp_path, "eeee", [_asst([{"type": "text", "text": "Working on it?"}])], age_seconds=10)
    assert _by_id(_scan(tmp_path))["eeee"].attention == Attention.RUNNING


def test_stale_question_is_not_attention(tmp_path):
    _write(tmp_path, "ffff", [_asst([{"type": "text", "text": "Which one?"}])], age_seconds=300000)
    assert _by_id(_scan(tmp_path))["ffff"].attention == Attention.IDLE


def test_attention_sorted_first_and_app_managed_flag(tmp_path):
    _write(tmp_path, "idle1", [_asst([{"type": "text", "text": "Done."}])], age_seconds=300)
    _write(tmp_path, "wait1", [_asst([{"type": "text", "text": "Proceed?"}])], age_seconds=300)
    sessions = _scan(tmp_path, app_session_ids={"wait1"})
    assert sessions[0].session_id == "wait1"  # waiting sorts before idle
    assert sessions[0].app_managed is True


def test_missing_projects_dir_is_empty(tmp_path):
    assert _scan(tmp_path / "does-not-exist") == []
