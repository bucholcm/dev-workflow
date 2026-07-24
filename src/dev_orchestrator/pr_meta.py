"""PR description meta-block: parse + format (design §6). Pure functions.

Every AI-created PR carries a machine-readable HTML comment so later steps
(review, fix) can recover who authored it without a database:

    <!-- ai-workflow
    linear_issue: DEV-123
    linear_issue_id: <linear-id>
    ai_author: codex-5.4
    ai_reviewer: codex-5.5
    ai_complexity: s
    ai_risk: low
    ai_session_id: 9dd4b6bc-27b1-4621-8990-fa2831388323
    ai_cli: claude
    source: dev-orchestrator
    -->
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

_BLOCK_RE = re.compile(r"<!--\s*ai-workflow\s*\n(.*?)\n\s*-->", re.DOTALL)
_LINE_RE = re.compile(r"^\s*([a-z_]+)\s*:\s*(.*?)\s*$")


@dataclass
class PRMeta:
    linear_issue: str = ""
    linear_issue_id: str = ""
    ai_author: str = ""
    ai_reviewer: str = ""
    ai_complexity: str = ""
    ai_risk: str = ""
    ai_session_id: str = ""   # pinned Claude session id (claude CLI only) → resume on fix
    ai_cli: str = ""          # "claude" | "codex" — which engine authored (resume is claude-only)
    source: str = "dev-orchestrator"

    def to_block(self) -> str:
        lines = "\n".join(f"{k}: {v}" for k, v in asdict(self).items())
        return f"<!-- ai-workflow\n{lines}\n-->"


def format_meta(meta: PRMeta) -> str:
    return meta.to_block()


def parse_meta(pr_description: str) -> PRMeta | None:
    """Recover the meta-block from a PR description; None if absent."""
    m = _BLOCK_RE.search(pr_description or "")
    if not m:
        return None
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        lm = _LINE_RE.match(line)
        if lm:
            fields[lm.group(1)] = lm.group(2)
    known = {f.name for f in PRMeta.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return PRMeta(**{k: v for k, v in fields.items() if k in known})


def build_pr_description(
    *,
    meta: PRMeta,
    summary: str,
    acceptance_criteria: list[str],
    test_plan: list[str] | None = None,
) -> str:
    """Assemble the full PR body: machine block + human-readable sections (design §6)."""
    ac = "\n".join(f"- [ ] {c}" for c in acceptance_criteria) or "- [ ] (none specified)"
    tp = "\n".join(f"- [ ] {t}" for t in (test_plan or [])) or "- [ ] (add test plan)"
    return f"""{meta.to_block()}

## Summary

{summary or "(fill in)"}

## Linear issue

{meta.linear_issue}

## Acceptance criteria

{ac}

## Test plan

{tp}

## AI metadata

Authoring agent: {meta.ai_author}
Review agent: {meta.ai_reviewer}
Risk: {meta.ai_risk}
Complexity: {meta.ai_complexity}
"""
