"""Run hydration, fix session-recovery without a meta-block, and bounded auto-routing."""

from dev_orchestrator import gitops, runner
from dev_orchestrator.config import Settings
from dev_orchestrator.models import Decision, LinearIssue, ReviewResult
from dev_orchestrator.orchestrator import Orchestrator
from dev_orchestrator.pr_meta import PRMeta, build_pr_description
from dev_orchestrator.runs import RunRegistry

DONE = '```json\n{"status": "done"}\n```'


def _issue(**kw):
    base = dict(id="u", identifier="BRI-61", title="Carve", description="- crit",
                state_name="Human Review", url="", repo="ws/repo")
    base.update(kw)
    return LinearIssue(**base)


# ── hydration ──────────────────────────────────────────────────────────────

def test_hydrate_restores_runs_and_session(tmp_path):
    d = str(tmp_path / "runs")
    reg = RunRegistry(d, clock=lambda: "T")
    reg.start("run-a", "implement", "BRI-61", agent="opus-4.8", session_id="sess-9", cli="claude")
    reg.finish("run-a", "failed")
    # Fresh registry over the same dir → should recover the run from JSONL.
    reg2 = RunRegistry(d, clock=lambda: "T")
    assert reg2.get("run-a") is None
    assert reg2.hydrate() == 1
    got = reg2.get("run-a")
    assert got and got.session_id == "sess-9" and got.cli == "claude" and got.status == "failed"
    assert reg2.latest_session_for_issue("BRI-61").session_id == "sess-9"


# ── fix session recovery without a PR meta-block ───────────────────────────

class _PR:
    source_branch = "docs/core-instance-split-m1"
    url = "http://pr/66"


class _NoMetaBB:
    """A PR whose description carries NO orchestrator meta-block (out-of-band PR)."""
    def __init__(self):
        self.comments = []

    def get_pr(self, _):
        return _PR()

    def get_pr_description(self, _):
        return "Plain PR body, opened by the agent. No ai-workflow block."

    def list_tasks(self, _):
        return []

    def list_comments(self, _):
        return []

    def post_comment(self, _pr, body):
        self.comments.append(body)


def _live_orch(tmp_path, bb, **settings):
    s = Settings(dry_run=False, runs_dir=str(tmp_path / "runs"), target_repo_path=str(tmp_path),
                 model_complex_implementation="opus-4.8", model_simple_implementation="codex-5.4",
                 model_review="codex-5.5", **settings)
    runs = RunRegistry(s.runs_dir, clock=lambda: "T")
    return Orchestrator(s, runs, linear=None, make_bitbucket=lambda repo: bb), runs


def test_fix_recovers_session_from_run_history(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(runner, "resolve_cli", lambda family, **k: family)
    monkeypatch.setattr(runner, "run_agent", lambda **kw: captured.append(kw) or DONE)
    monkeypatch.setattr(gitops, "ensure_worktree_for_branch", lambda *a, **k: "/wt")
    monkeypatch.setattr(gitops, "push_branch", lambda *a, **k: None)

    orch, runs = _live_orch(tmp_path, _NoMetaBB())
    # Prior implement run pinned a claude session for BRI-61 (no PR meta-block exists).
    runs.start("impl-1", "implement", "BRI-61", agent="opus-4.8", session_id="sess-992", cli="claude")
    runs.finish("impl-1", "failed")

    orch.dispatch_fix(_issue(), pr_id=66, repo_slug="ws/repo")
    assert len(captured) == 1
    assert captured[0]["cli"] == "claude"
    assert captured[0]["resume"] is True
    assert captured[0]["session_id"] == "sess-992"  # recovered from run history, not a meta-block


def test_fix_bounces_when_no_meta_and_no_history(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(runner, "run_agent", lambda **kw: ran.append(kw) or DONE)
    orch, runs = _live_orch(tmp_path, _NoMetaBB())
    rid = orch.dispatch_fix(_issue(), pr_id=66, repo_slug="ws/repo")
    assert runs.get(rid).status == "human_review"
    assert ran == []  # never ran an agent — correctly could not identify the author


# ── bounded auto-routing ───────────────────────────────────────────────────

def _meta_bb():
    class _MetaBB(_NoMetaBB):
        def get_pr_description(self, _):
            return build_pr_description(
                meta=PRMeta(linear_issue="BRI-61", ai_author="opus-4.8", ai_reviewer="codex-5.5",
                            ai_session_id="sess-1", ai_cli="claude"),
                summary="s", acceptance_criteria=["x"])
    return _MetaBB()


def test_auto_fix_disabled_by_default(tmp_path):
    orch, runs = _live_orch(tmp_path, _meta_bb())  # auto_fix_on_review defaults False
    assert orch._maybe_auto_fix(_issue(), 66, "ws/repo") is False


def test_auto_fix_is_bounded(tmp_path, monkeypatch):
    # Never actually spawn threads; just assert the round cap gating.
    monkeypatch.setattr(Orchestrator, "_spawn", lambda self, fn, *a: None)
    orch, runs = _live_orch(tmp_path, _meta_bb(), auto_fix_on_review=True, max_auto_review_rounds=2)
    issue = _issue()
    assert orch._maybe_auto_fix(issue, 66, "ws/repo") is True   # round 1
    assert orch._maybe_auto_fix(issue, 66, "ws/repo") is True   # round 2
    assert orch._maybe_auto_fix(issue, 66, "ws/repo") is False  # exhausted → stop for a human


def test_auto_fix_triggered_by_request_changes_verdict(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(Orchestrator, "_spawn", lambda self, fn, *a: calls.append((fn.__name__, a)))
    orch, runs = _live_orch(tmp_path, _meta_bb(), auto_fix_on_review=True)
    runs.start("rev-1", "review", "BRI-61")
    result = ReviewResult(overview="", findings=[], summary="", decision=Decision.REQUEST_CHANGES)
    orch._apply_review_decision("rev-1", _issue(), result, pr_id=66, repo_slug="ws/repo")
    assert calls and calls[0][0] == "dispatch_fix"
