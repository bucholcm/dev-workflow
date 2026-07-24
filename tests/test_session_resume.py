"""Session pinning & resume: fix resumes the original claude session; DRY_RUN is inert."""

from dev_orchestrator import gitops, runner
from dev_orchestrator.config import Settings
from dev_orchestrator.models import LinearIssue, Risk, Size
from dev_orchestrator.orchestrator import Orchestrator
from dev_orchestrator.pr_meta import PRMeta, build_pr_description
from dev_orchestrator.runs import RunRegistry

DONE = '```json\n{"status": "done"}\n```'


class _PR:
    source_branch = "ai/DEV-1-add-thing"
    url = "http://pr/1"


class _FakeBB:
    def __init__(self, meta: PRMeta):
        self._body = build_pr_description(meta=meta, summary="s", acceptance_criteria=["x"])
        self.comments = []

    def get_pr(self, _):
        return _PR()

    def get_pr_description(self, _):
        return self._body

    def list_tasks(self, _):
        return []

    def list_comments(self, _):
        return []

    def post_comment(self, _pr, body):
        self.comments.append(body)


def _live_orch(tmp_path, meta):
    s = Settings(dry_run=False, runs_dir=str(tmp_path / "runs"), target_repo_path=str(tmp_path),
                 model_complex_implementation="opus-4.8", model_simple_implementation="codex-5.4",
                 model_review="codex-5.5")
    runs = RunRegistry(s.runs_dir, clock=lambda: "T")
    bb = _FakeBB(meta)
    return Orchestrator(s, runs, linear=None, make_bitbucket=lambda repo: bb), runs, bb


def _issue(**kw):
    base = dict(id="u", identifier="DEV-1", title="Add thing", description="- crit",
                state_name="Needs Fixes", url="", repo="ws/repo")
    base.update(kw)
    return LinearIssue(**base)


def test_fix_resumes_original_claude_session(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(runner, "resolve_cli", lambda family, **k: family)
    monkeypatch.setattr(runner, "run_agent", lambda **kw: captured.append(kw) or DONE)
    monkeypatch.setattr(gitops, "worktree_for", lambda *a, **k: "/wt")
    monkeypatch.setattr(gitops, "push_branch", lambda *a, **k: None)

    meta = PRMeta(linear_issue="DEV-1", ai_author="opus-4.8", ai_reviewer="codex-5.5",
                  ai_session_id="sess-123", ai_cli="claude")
    orch, runs, _ = _live_orch(tmp_path, meta)
    orch.dispatch_fix(_issue(), pr_id=1, repo_slug="ws/repo")

    assert len(captured) == 1
    assert captured[0]["cli"] == "claude"
    assert captured[0]["resume"] is True
    assert captured[0]["session_id"] == "sess-123"


def test_fix_with_codex_meta_does_not_resume(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(runner, "resolve_cli", lambda family, **k: family)
    monkeypatch.setattr(runner, "run_agent", lambda **kw: captured.append(kw) or DONE)
    monkeypatch.setattr(gitops, "worktree_for", lambda *a, **k: "/wt")
    monkeypatch.setattr(gitops, "push_branch", lambda *a, **k: None)

    meta = PRMeta(linear_issue="DEV-1", ai_author="codex-5.4", ai_reviewer="codex-5.5",
                  ai_session_id="sess-xyz", ai_cli="codex")
    orch, runs, _ = _live_orch(tmp_path, meta)
    orch.dispatch_fix(_issue(), pr_id=1, repo_slug="ws/repo")

    assert captured[0]["cli"] == "codex"
    assert captured[0]["resume"] is False


def test_complex_dispatch_pins_a_session_in_dryrun(tmp_path):
    s = Settings(dry_run=True, runs_dir=str(tmp_path / "runs"),
                 model_complex_implementation="opus-4.8", model_simple_implementation="codex-5.4",
                 model_review="codex-5.5")
    runs = RunRegistry(s.runs_dir, clock=lambda: "T")
    orch = Orchestrator(s, runs, linear=None, make_bitbucket=lambda r: None)
    run_id = orch.dispatch_issue(_issue(size=Size.L, risk=Risk.LOW, state_name="Ready for AI"))
    run = runs.get(run_id)
    assert run.status == "dry_run"
    assert run.session_id and run.cli == "claude"  # claude family → pinned


def test_dispatch_answer_dryrun_is_inert(tmp_path):
    s = Settings(dry_run=True, runs_dir=str(tmp_path / "runs"),
                 model_complex_implementation="opus-4.8", model_simple_implementation="codex-5.4",
                 model_review="codex-5.5")
    runs = RunRegistry(s.runs_dir, clock=lambda: "T")
    orch = Orchestrator(s, runs, linear=None, make_bitbucket=lambda r: None)
    issue = _issue(size=Size.L, risk=Risk.LOW, state_name="Ready for AI")
    orch.dispatch_issue(issue)  # pins a session
    answer_run = orch.dispatch_answer(issue, "use a soft delete")
    run = runs.get(answer_run)
    assert run.kind == "resume"
    assert run.status == "dry_run"
    assert any("would resume" in e.message for e in run.events)
