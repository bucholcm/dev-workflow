from dev_orchestrator.config import Settings
from dev_orchestrator.models import LinearIssue, Risk, Size
from dev_orchestrator.routing import branch_name, route, slugify


def _settings():
    return Settings(_env_file=None,
        model_simple_implementation="codex-5.4",
        model_complex_implementation="opus-4.8",
        model_review="codex-5.5",
    )


def _issue(**kw):
    base = dict(id="uuid", identifier="DEV-123", title="Fix login bug", description="- do x",
                state_name="Ready for AI", url="")
    base.update(kw)
    return LinearIssue(**base)


def test_slugify_and_branch():
    assert slugify("Fix Payment Validation!") == "fix-payment-validation"
    assert branch_name("DEV-1", "Add OAuth flow") == "ai/DEV-1-add-oauth-flow"


def test_small_routes_to_simple():
    d = route(_issue(size=Size.S, risk=Risk.LOW), _settings())
    assert d.agent == "codex-5.4" and not d.human_gate


def test_large_routes_to_complex():
    d = route(_issue(size=Size.L, risk=Risk.LOW), _settings())
    assert d.agent == "opus-4.8" and not d.human_gate


def test_high_risk_is_human_gated():
    d = route(_issue(size=Size.S, risk=Risk.HIGH), _settings())
    assert d.human_gate and d.agent is None


def test_explicit_agent_hint_overrides_size():
    d = route(_issue(size=Size.S, risk=Risk.LOW, agent_hint="opus-4.8"), _settings())
    assert d.agent == "opus-4.8"


def test_agent_hint_human_gates():
    d = route(_issue(size=Size.S, risk=Risk.LOW, agent_hint="human"), _settings())
    assert d.human_gate and d.agent is None


def test_missing_size_requires_human():
    d = route(_issue(size=None, risk=Risk.LOW), _settings())
    assert d.human_gate


def test_reviewer_is_always_model_review():
    d = route(_issue(size=Size.M, risk=Risk.LOW), _settings())
    assert d.reviewer == "codex-5.5"
