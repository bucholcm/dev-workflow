"""Parse the reviewer's JSON output into a ReviewResult + Decision.

Robust LLM-JSON extraction (fenced block → raw-object fallback, trailing-comma
repair, strict=False) is adapted from simion/reviewd (MIT). Fail-safe rule from
design §5: any parse failure → require_human_review (never fail open).
"""

from __future__ import annotations

import json
import logging
import re

from .models import Decision, Finding, ReviewResult, Severity

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)


def _find_last_json_object(output: str) -> str | None:
    """Find the last balanced-looking JSON object in free text (no fences)."""
    last = output.rfind("}")
    if last == -1:
        return None
    pos = last
    while True:
        pos = output.rfind("{", 0, pos)
        if pos == -1:
            return None
        candidate = output[pos : last + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue


def extract_json(output: str) -> dict:
    """Extract the review JSON object from raw agent stdout. Raises ValueError on failure."""
    matches = _JSON_BLOCK_RE.findall(output)
    if not matches:
        raw = _find_last_json_object(output)
        if raw:
            matches = [raw]
    if not matches:
        raise ValueError("no JSON block found in AI output")
    raw = matches[-1]
    try:
        return json.loads(raw, strict=False)
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", raw)  # strip trailing commas
        return json.loads(fixed, strict=False)


def _decision_from(data: dict, findings: list[Finding]) -> Decision:
    """Map the reviewer's declared decision (with a safety net) to our enum."""
    raw = str(data.get("decision") or data.get("final_decision") or "").strip().lower()
    mapping = {
        "pass": Decision.PASS,
        "request_changes": Decision.REQUEST_CHANGES,
        "needs_fixes": Decision.REQUEST_CHANGES,
        "require_human_review": Decision.REQUIRE_HUMAN_REVIEW,
        "human_review_required": Decision.REQUIRE_HUMAN_REVIEW,
    }
    if raw in mapping:
        decision = mapping[raw]
    else:
        # No explicit/parseable decision → infer conservatively from findings.
        decision = Decision.REQUEST_CHANGES if any(f.severity == Severity.CRITICAL for f in findings) else Decision.PASS
    # Safety net: a declared pass that still carries critical findings is not a pass.
    if decision == Decision.PASS and any(f.severity == Severity.CRITICAL for f in findings):
        return Decision.REQUEST_CHANGES
    return decision


def parse_review_result(data: dict) -> ReviewResult:
    findings: list[Finding] = []
    for f in data.get("findings", []):
        try:
            severity = Severity(str(f.get("severity", "suggestion")).lower())
        except ValueError:
            severity = Severity.SUGGESTION
        findings.append(
            Finding(
                severity=severity,
                category=f.get("category", "General"),
                title=f.get("title", ""),
                file=f.get("file", ""),
                line=f.get("line"),
                end_line=f.get("end_line"),
                issue=f.get("issue", ""),
                fix=f.get("fix"),
            )
        )
    return ReviewResult(
        overview=data.get("overview", ""),
        findings=findings,
        summary=data.get("summary", ""),
        decision=_decision_from(data, findings),
        tests_passed=data.get("tests_passed"),
        acceptance_criteria=list(data.get("acceptance_criteria", [])),
    )


def parse_review_output(raw_output: str) -> ReviewResult:
    """Full pipeline: raw stdout → ReviewResult. Parse failure → require_human_review."""
    try:
        data = extract_json(raw_output)
        return parse_review_result(data)
    except (ValueError, json.JSONDecodeError, TypeError) as e:
        logger.error("Review output unparseable (%s) → require_human_review", e)
        return ReviewResult(
            overview="",
            findings=[],
            summary=f"Reviewer output could not be parsed ({e}). Escalating to human review.",
            decision=Decision.REQUIRE_HUMAN_REVIEW,
        )
