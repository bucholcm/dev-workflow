"""Prompt templates for implement / review / fix. Pure string builders.

The review prompt forces a JSON contract (parsed by review_parser) rather than
free-form markdown — far more reliable to machine-read. The decision field maps
to design §5's pass | request_changes | require_human_review.
"""

from __future__ import annotations

from .models import LinearIssue

# Appended to write-enabled (implement / fix / answer) prompts. Lets the agent
# STOP and ask for a human decision in a machine-readable way instead of guessing;
# parsed by escalation_parser. A missing block is treated as "done".
_ESCALATION_CONTRACT = """
## Signalling completion or a blocker — MANDATORY
End your response with EXACTLY ONE fenced ```json block:
- Finished the work (committed): {"status": "done"}
- Cannot proceed without a human decision (risky/ambiguous/blocked): \
{"status": "needs_input", "questions": ["specific question 1", "..."], "context": "one line of context"}
Do not guess when blocked — emit needs_input and stop."""

_REVIEW_JSON_SCHEMA = """{
  "overview": "one-paragraph summary of the change",
  "findings": [
    {
      "severity": "critical|suggestion|nitpick|good",
      "category": "Correctness|Security|Tests|Performance|Style|...",
      "title": "short title",
      "file": "path/to/file",
      "line": 123,
      "end_line": null,
      "issue": "what is wrong and under what conditions it manifests",
      "fix": "concrete suggested fix (optional)"
    }
  ],
  "acceptance_criteria": ["[x] criterion met", "[ ] criterion not met"],
  "tests_passed": true,
  "summary": "human-readable wrap-up",
  "decision": "pass|request_changes|require_human_review"
}"""


def build_implement_prompt(issue: LinearIssue, *, is_complex: bool) -> str:
    role = "complex, multi-file" if is_complex else "small, well-scoped"
    return f"""You are the implementation agent for Linear issue {issue.identifier}.

This is a {role} change. Follow the repository's AGENTS.md / CLAUDE.md conventions.

## Linear issue: {issue.identifier} — {issue.title}

{issue.description or "(no description provided)"}

## Rules
- Restate the requirement in one line, then implement the SMALLEST safe change.
- Do not broaden scope or reformat unrelated files.
- Add or update tests when behavior changes.
- Commit your work on the current branch with a clear message referencing {issue.identifier}.
- If the change is risky (auth, permissions, migrations, data deletion) or the
  requirement is unclear, STOP and explain what needs human input instead of guessing.
{_ESCALATION_CONTRACT}
"""


def build_review_prompt(issue: LinearIssue | None, pr_title: str, diff_hint: str = "") -> str:
    ac = issue.description if issue else "(acceptance criteria unavailable — infer from the diff)"
    return f"""You are the delivery-review agent. Review the pull request in this checkout.

PR: {pr_title}
{f"Linear issue: {issue.identifier} — {issue.title}" if issue else ""}

## Acceptance criteria to check
{ac}

{diff_hint}

## What to flag
- critical = correctness bugs, regressions, security issues, data loss, breaking API/behavior, missing tests for changed behavior.
- suggestion / nitpick = non-blocking improvements.
- Set decision = require_human_review for auth/permissions/billing/migrations/destructive changes.

## Output — MANDATORY
Respond with ONLY a fenced ```json block matching this schema (no prose outside it):

```json
{_REVIEW_JSON_SCHEMA}
```
"""


def build_fix_prompt(issue: LinearIssue | None, unresolved: str) -> str:
    return f"""You are the ORIGINAL author agent. Address the unresolved PR review comments below.

{f"Linear issue: {issue.identifier} — {issue.title}" if issue else ""}

## Unresolved comments / tasks
{unresolved or "(none supplied)"}

## Rules
- Make the SMALLEST possible fix for each comment. Do not do unrelated cleanup.
- Push commits to the same PR branch.
- After fixing, write one short summary line per comment describing what you changed.
{_ESCALATION_CONTRACT}
"""


def build_answer_prompt(answer: str) -> str:
    """Continuation turn: feed the human's answer back into a resumed session."""
    return f"""The human has answered the question(s) you raised. Here is their decision:

{answer}

Proceed with the implementation using this guidance. Follow all earlier rules
(smallest safe change, commit referencing the issue, add/update tests).
{_ESCALATION_CONTRACT}
"""
