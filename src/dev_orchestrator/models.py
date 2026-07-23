"""Domain dataclasses shared across the orchestrator.

The ReviewResult / Finding / Severity shapes are adapted from simion/reviewd
(MIT) — see src/dev_orchestrator/vendor/README.md for attribution.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

# ── Linear planning side ────────────────────────────────────────────────────

class Size(enum.StrEnum):
    XS = "xs"
    S = "s"
    M = "m"
    L = "l"
    XL = "xl"


class Risk(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Workflow states used by the orchestrator (see design §3). The concrete Linear
# state *names* are matched case-insensitively; state IDs are discovered via the
# Linear API at runtime (config pins them if you prefer).
class WorkflowState(enum.StrEnum):
    TRIAGE = "Triage"
    READY_FOR_SPECIFICATION = "Ready for Specification"
    SPECIFIED = "Specified"
    READY_FOR_AI = "Ready for AI"
    AI_IN_PROGRESS = "AI In Progress"
    PR_OPEN = "PR Open"
    AI_REVIEW = "AI Review"
    NEEDS_FIXES = "Needs Fixes"
    HUMAN_REVIEW = "Human Review"
    READY_TO_MERGE = "Ready to Merge"
    DONE = "Done"


@dataclass
class LinearIssue:
    """A Linear issue projected onto the fields the orchestrator cares about."""

    id: str                       # Linear internal UUID
    identifier: str               # human key, e.g. "DEV-123"
    title: str
    description: str
    state_name: str
    url: str
    labels: list[str] = field(default_factory=list)
    # Parsed from labels (or custom fields) — None when absent/unparseable.
    size: Size | None = None
    risk: Risk | None = None
    agent_hint: str | None = None     # explicit "agent:opus-4.8" style override
    repo: str | None = None           # "workspace/repo"
    branch: str | None = None
    pr_url: str | None = None

    def missing_required_fields(self) -> list[str]:
        """Fields required before an issue may be dispatched to an agent (design §3.2)."""
        missing: list[str] = []
        if self.size is None:
            missing.append("size")
        if self.risk is None:
            missing.append("risk")
        if not self.repo:
            missing.append("repo")
        if not (self.description or "").strip():
            missing.append("description / acceptance criteria")
        return missing


# ── Routing decision (output of the pure routing function) ──────────────────

@dataclass
class RoutingDecision:
    agent: str | None          # resolved model env value, e.g. "codex-5.4" / "opus-4.8"
    reviewer: str              # MODEL_REVIEW value
    branch: str                # ai/<KEY>-<slug>
    human_gate: bool           # True → require explicit human approval before AI runs
    reason: str


# ── Bitbucket code side ─────────────────────────────────────────────────────

@dataclass
class PRInfo:
    repo_slug: str
    pr_id: int
    title: str
    author: str
    source_branch: str
    destination_branch: str
    source_commit: str
    url: str
    draft: bool = False


# ── AI review contract (JSON emitted by the reviewer, parsed by review_parser) ─

class Severity(enum.StrEnum):
    CRITICAL = "critical"
    SUGGESTION = "suggestion"
    NITPICK = "nitpick"
    GOOD = "good"


SEVERITY_ORDER = {"good": 0, "nitpick": 1, "suggestion": 2, "critical": 3}


class Decision(enum.StrEnum):
    PASS = "pass"
    REQUEST_CHANGES = "request_changes"
    REQUIRE_HUMAN_REVIEW = "require_human_review"


@dataclass
class Finding:
    severity: Severity
    category: str
    title: str
    file: str
    line: int | None
    end_line: int | None
    issue: str
    fix: str | None = None


@dataclass
class ReviewResult:
    overview: str
    findings: list[Finding]
    summary: str
    decision: Decision
    tests_passed: bool | None = None
    acceptance_criteria: list[str] = field(default_factory=list)  # human-readable lines
    duration_seconds: float | None = None

    @property
    def blocking_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.CRITICAL]
