"""Multi-turn review (session continuity + turn tracking) and the Release action."""

from dev_orchestrator import gitops, review_index, runner
from dev_orchestrator.config import Settings
from dev_orchestrator.models import LinearIssue, WorkflowState
from dev_orchestrator.orchestrator import Orchestrator
from dev_orchestrator.runs import RunRegistry

REVIEW_PASS = ('```json\n{"decision":"pass","findings":[],"summary":"ok",'
               '"acceptance_criteria":[],"tests_passed":true,"overview":""}\n```')


class _PR:
    title = "[review] BRI-61: carve"
    source_branch = "docs/core-instance-split-m1"
    url = "http://pr/66"


class _BB:
    def __init__(self):
        self.comments = []

    def get_pr(self, _):
        return _PR()

    def get_pr_description(self, _):
        return "plain body"

    def list_tasks(self, _):
        return []

    def list_comments(self, _):
        return []

    def post_comment(self, _pr, body):
        self.comments.append(body)


class _RecordingLinear:
    def __init__(self):
        self.states = []

    def set_state(self, issue_id, state_id):
        self.states.append(state_id)
        return True

    def comment(self, *a):
        return True


def _issue():
    return LinearIssue(id="u", identifier="BRI-61", title="carve", description="- crit",
                       state_name="AI Review", url="", repo="ws/repo")


def _orch(tmp_path, bb, linear=None, **settings):
    settings.setdefault("dry_run", False)
    settings.setdefault("cli_for_review", "codex")
    settings.setdefault("model_review", "codex-5.5")
    s = Settings(_env_file=None, runs_dir=str(tmp_path / "runs"),
                 target_repo_path=str(tmp_path), **settings)
    runs = RunRegistry(s.runs_dir, clock=lambda: "T")
    state_ids = {str(w): w.name for w in WorkflowState}  # dummy ids so _set_state proceeds
    return Orchestrator(s, runs, linear=linear, make_bitbucket=lambda r: bb, state_ids=state_ids), runs, s


# ── review index ──

def test_review_index_roundtrip(tmp_path):
    path = str(tmp_path / "review_index.json")
    assert review_index.get(path, 66) is None
    review_index.record(path, 66, "sess-r", "claude", 2)
    assert review_index.get(path, 66) == {"session_id": "sess-r", "cli": "claude", "turns": 2}


# ── multi-turn review ──

def test_review_increments_turn_and_passes_prior_context(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "resolve_cli", lambda fam, **k: "codex")
    monkeypatch.setattr(runner, "run_agent", lambda **kw: calls.append(kw) or REVIEW_PASS)
    monkeypatch.setattr(gitops, "ensure_worktree_for_branch", lambda *a, **k: "/wt")

    bb = _BB()
    orch, runs, s = _orch(tmp_path, bb)
    orch.dispatch_review(_issue(), 66, "ws/repo")
    orch.dispatch_review(_issue(), 66, "ws/repo")

    assert review_index.get(s.review_index_path, 66)["turns"] == 2
    # Round 2 prompt announces the round and asks to verify prior points.
    assert "round 2" in calls[1]["prompt"]
    assert "round 2" not in calls[0]["prompt"]


def test_claude_reviewer_pins_and_resumes_session(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "resolve_cli", lambda fam, **k: "claude")
    monkeypatch.setattr(runner, "run_agent", lambda **kw: calls.append(kw) or REVIEW_PASS)
    monkeypatch.setattr(gitops, "ensure_worktree_for_branch", lambda *a, **k: "/wt")

    orch, runs, s = _orch(tmp_path, _BB(), cli_for_review="claude")
    orch.dispatch_review(_issue(), 66, "ws/repo")
    orch.dispatch_review(_issue(), 66, "ws/repo")

    first_session = calls[0]["session_id"]
    assert first_session and calls[0]["resume"] is False      # round 1 pins a new session
    assert calls[1]["session_id"] == first_session            # round 2 reuses it
    assert calls[1]["resume"] is True                         # …by resuming


def test_codex_reviewer_does_not_resume(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, "resolve_cli", lambda fam, **k: "codex")
    monkeypatch.setattr(runner, "run_agent", lambda **kw: calls.append(kw) or REVIEW_PASS)
    monkeypatch.setattr(gitops, "ensure_worktree_for_branch", lambda *a, **k: "/wt")
    orch, runs, s = _orch(tmp_path, _BB())
    orch.dispatch_review(_issue(), 66, "ws/repo")
    orch.dispatch_review(_issue(), 66, "ws/repo")
    assert all(c["resume"] is False and c["session_id"] is None for c in calls)


# ── release ──

def test_release_sets_ready_to_merge(tmp_path):
    bb, linear = _BB(), _RecordingLinear()
    orch, runs, s = _orch(tmp_path, bb, linear=linear)
    rid = orch.dispatch_release(_issue(), 66, "ws/repo")
    assert runs.get(rid).status == "released"
    assert WorkflowState.READY_TO_MERGE.name in linear.states  # moved, not merged
    assert any("Merge this PR in Bitbucket" in c for c in bb.comments)  # human merges


def test_release_dry_run_is_inert(tmp_path):
    bb, linear = _BB(), _RecordingLinear()
    orch, runs, s = _orch(tmp_path, bb, linear=linear, dry_run=True)
    rid = orch.dispatch_release(_issue(), 66, "ws/repo")
    assert runs.get(rid).status == "dry_run"
    assert linear.states == [] and bb.comments == []
