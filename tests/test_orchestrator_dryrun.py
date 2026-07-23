"""DRY_RUN must never touch Linear/Bitbucket/git and must exercise routing."""

from dev_orchestrator.config import Settings
from dev_orchestrator.models import LinearIssue, Risk, Size, WorkflowState
from dev_orchestrator.orchestrator import Orchestrator
from dev_orchestrator.runs import RunRegistry


class _RecordingLinear:
    def __init__(self):
        self.calls = []

    def set_state(self, *a):
        self.calls.append(("set_state", a))
        return True

    def comment(self, *a):
        self.calls.append(("comment", a))
        return True


def _orch(tmp_path):
    s = Settings(dry_run=True, runs_dir=str(tmp_path / "runs"),
                 model_simple_implementation="codex-5.4", model_complex_implementation="opus-4.8",
                 model_review="codex-5.5")
    runs = RunRegistry(s.runs_dir, clock=lambda: "T")
    linear = _RecordingLinear()

    def boom(_):
        raise AssertionError("bitbucket must not be created in dry-run")

    return Orchestrator(s, runs, linear=linear, make_bitbucket=boom), runs, linear


def _issue(**kw):
    base = dict(id="u", identifier="DEV-1", title="Add thing", description="- criterion",
                state_name="Ready for AI", url="", repo="ws/repo")
    base.update(kw)
    return LinearIssue(**base)


def test_dryrun_dispatch_makes_no_external_writes(tmp_path):
    orch, runs, linear = _orch(tmp_path)
    run_id = orch.dispatch_issue(_issue(size=Size.S, risk=Risk.LOW))
    run = runs.get(run_id)
    assert run.status == "dry_run"
    assert linear.calls == []  # no live Linear writes in dry-run


def test_missing_fields_blocks_before_agent(tmp_path):
    orch, runs, _ = _orch(tmp_path)
    run_id = orch.dispatch_issue(_issue(size=None, risk=None, repo=None, description=""))
    run = runs.get(run_id)
    assert run.status == "failed"
    phases = [e.message for e in run.events]
    assert any("missing" in m.lower() for m in phases)


def test_high_risk_goes_to_human(tmp_path):
    orch, runs, _ = _orch(tmp_path)
    run_id = orch.dispatch_issue(_issue(size=Size.S, risk=Risk.HIGH))
    assert runs.get(run_id).status == "human_review"


def test_jsonl_written(tmp_path):
    orch, runs, _ = _orch(tmp_path)
    run_id = orch.dispatch_issue(_issue(size=Size.S, risk=Risk.LOW))
    log = (tmp_path / "runs" / f"{run_id}.jsonl")
    assert log.exists() and log.read_text().strip()
    assert str(WorkflowState.AI_IN_PROGRESS) in log.read_text()  # would-set logged
