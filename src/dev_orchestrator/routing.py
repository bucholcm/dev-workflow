"""Deterministic routing — a PURE function of issue fields (design §4). No I/O.

    if risk == high:        → human gate (require explicit approval before AI run)
    if size in [xs, s]:     implementation_agent = MODEL_SIMPLE_IMPLEMENTATION   (codex)
    if size in [m, l, xl]:  implementation_agent = MODEL_COMPLEX_IMPLEMENTATION  (opus)
    reviewer:               MODEL_REVIEW
    fix agent:              same agent that authored the PR (resolved from PR meta)
"""

from __future__ import annotations

import re

from .config import Settings
from .models import LinearIssue, Risk, RoutingDecision, Size

_SIMPLE_SIZES = {Size.XS, Size.S}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, max_len: int = 40) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "change"


def branch_name(identifier: str, title: str) -> str:
    """ai/<LINEAR_KEY>-<short-slug> (design §6)."""
    return f"ai/{identifier}-{slugify(title)}"


def route(issue: LinearIssue, settings: Settings) -> RoutingDecision:
    """Map issue fields → {agent, reviewer, branch, human_gate, reason}."""
    branch = issue.branch or branch_name(issue.identifier, issue.title)
    reviewer = settings.model_review

    # High risk → human gate. No agent is auto-selected; a human must approve/override.
    if issue.risk == Risk.HIGH:
        return RoutingDecision(
            agent=None,
            reviewer=reviewer,
            branch=branch,
            human_gate=True,
            reason="risk=high → human approval required before AI execution",
        )

    # Explicit per-issue override (e.g. label "agent:opus-4.8" or "agent:human").
    if issue.agent_hint:
        hint = issue.agent_hint.strip().lower()
        if hint in {"human", "none"}:
            return RoutingDecision(
                agent=None, reviewer=reviewer, branch=branch, human_gate=True,
                reason=f"explicit agent hint '{issue.agent_hint}' → human gate",
            )
        return RoutingDecision(
            agent=issue.agent_hint, reviewer=reviewer, branch=branch, human_gate=False,
            reason=f"explicit agent hint '{issue.agent_hint}'",
        )

    # Size-based deterministic routing.
    if issue.size in _SIMPLE_SIZES:
        return RoutingDecision(
            agent=settings.model_simple_implementation,
            reviewer=reviewer, branch=branch, human_gate=False,
            reason=f"size={issue.size} → simple implementation ({settings.model_simple_implementation})",
        )
    if issue.size is not None:  # m / l / xl
        return RoutingDecision(
            agent=settings.model_complex_implementation,
            reviewer=reviewer, branch=branch, human_gate=False,
            reason=f"size={issue.size} → complex implementation ({settings.model_complex_implementation})",
        )

    # No size → cannot route safely; require human triage.
    return RoutingDecision(
        agent=None, reviewer=reviewer, branch=branch, human_gate=True,
        reason="size not set → cannot auto-route; human triage required",
    )
