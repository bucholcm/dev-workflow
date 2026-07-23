"""The choreographer — the only thing that sequences clients + runner (design §2).

Every external mutation is guarded by DRY_RUN: when set, intended actions are
logged to the run and no Linear/Bitbucket/git writes happen. Failure modes follow
design §7 (missing fields → don't run; agent/parse failure → Human Review).
"""

from __future__ import annotations

import logging
import uuid

from . import gitops, prompts, runner
from .config import Settings
from .models import Decision, LinearIssue, ReviewResult, Severity, WorkflowState
from .pr_meta import PRMeta, build_pr_description, parse_meta
from .review_parser import parse_review_output
from .routing import route
from .runs import RunRegistry

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, settings: Settings, runs: RunRegistry, linear=None, make_bitbucket=None, state_ids=None):
        self.s = settings
        self.runs = runs
        self.linear = linear                      # LinearClient | None (None in pure DRY_RUN)
        self._make_bb = make_bitbucket            # callable(repo_slug) -> BitbucketClient
        self._state_ids = state_ids or {}         # {state_name: id}

    # ── helpers ──
    def _new_run_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def _set_state(self, run_id: str, issue: LinearIssue, state: WorkflowState) -> None:
        if self.s.dry_run:
            self.runs.event(run_id, "linear", f"[dry-run] would set {issue.identifier} → {state}")
            return
        if not self.linear:
            self.runs.event(run_id, "linear", f"[no-linear] skip set state → {state}")
            return
        sid = self._state_ids.get(str(state))
        if not sid:
            self.runs.event(run_id, "linear", f"[warn] no state id for '{state}'; skipping")
            return
        self.linear.set_state(issue.id, sid)
        self.runs.event(run_id, "linear", f"set {issue.identifier} → {state}")

    def _comment(self, run_id: str, issue: LinearIssue, body: str) -> None:
        if self.s.dry_run or not self.linear:
            self.runs.event(run_id, "linear", f"[dry-run] comment: {body[:120]}")
            return
        self.linear.comment(issue.id, body)

    def _cli_family(self, agent: str) -> str:
        """Map a resolved model value → CLI family using config heuristics."""
        a = agent.lower()
        if "codex" in a:
            return "codex"
        if "opus" in a or "claude" in a or "sonnet" in a:
            return "claude"
        return self.s.cli_for_complex  # sensible default

    # ── 1. implement ──
    def dispatch_issue(self, issue: LinearIssue) -> str:
        run_id = self._new_run_id()
        if self.runs.active_for_issue(issue.identifier):
            self.runs.event(run_id, "guard", f"{issue.identifier} already has an active run; skipping")
        decision = route(issue, self.s)
        self.runs.start(run_id, "implement", issue.identifier, agent=decision.agent or "human")
        self.runs.event(run_id, "route", decision.reason)

        # Validate required fields (design §3.2).
        missing = issue.missing_required_fields()
        if missing:
            self._comment(run_id, issue, f"Cannot dispatch: missing fields → {', '.join(missing)}")
            self._set_state(run_id, issue, WorkflowState.READY_FOR_SPECIFICATION)
            self.runs.finish(run_id, "failed")
            return run_id

        if decision.human_gate:
            self._comment(run_id, issue, f"Routing gated to human: {decision.reason}")
            self._set_state(run_id, issue, WorkflowState.HUMAN_REVIEW)
            self.runs.finish(run_id, "human_review")
            return run_id

        self._set_state(run_id, issue, WorkflowState.AI_IN_PROGRESS)

        if self.s.dry_run:
            self.runs.event(run_id, "implement", f"[dry-run] would run {decision.agent} on branch {decision.branch}")
            self.runs.event(run_id, "pr", "[dry-run] would create PR with meta-block")
            self.runs.finish(run_id, "dry_run")
            return run_id

        # Live path.
        try:
            fallback = self.s.codex_fallback_to_claude
            cli = runner.resolve_cli(self._cli_family(decision.agent), allow_claude_fallback=fallback)
            wt = gitops.prepare_worktree(self.s.target_repo_path, decision.branch)
            is_complex = decision.agent == self.s.model_complex_implementation
            prompt = prompts.build_implement_prompt(issue, is_complex=is_complex)
            self.runs.event(run_id, "implement", f"running {cli} ({decision.agent})")
            runner.run_agent(
                cli=cli, prompt=prompt, cwd=wt, model=None, read_only=False,
                timeout=self.s.agent_timeout_seconds,
                on_event=lambda p, m: self.runs.event(run_id, p, m),
            )
            if not gitops.has_commits(wt):
                raise RuntimeError("agent produced no commits")
            gitops.push_branch(wt, decision.branch)
            pr = self._open_pr(run_id, issue, decision)
            self._set_state(run_id, issue, WorkflowState.PR_OPEN)
            self.runs.finish(run_id, "passed", pr_url=pr.url)
        except Exception as e:  # noqa: BLE001 — any failure escalates to human review
            logger.exception("implement failed for %s", issue.identifier)
            self._comment(run_id, issue, f"AI implementation failed: {e}")
            self._set_state(run_id, issue, WorkflowState.HUMAN_REVIEW)
            self.runs.finish(run_id, "failed")
        return run_id

    def _open_pr(self, run_id: str, issue: LinearIssue, decision):
        bb = self._make_bb(issue.repo)
        meta = PRMeta(
            linear_issue=issue.identifier,
            linear_issue_id=issue.id,
            ai_author=decision.agent,
            ai_reviewer=decision.reviewer,
            ai_complexity=str(issue.size or ""),
            ai_risk=str(issue.risk or ""),
        )
        criteria = [ln for ln in (issue.description or "").splitlines() if ln.strip().startswith("-")]
        body = build_pr_description(meta=meta, summary=issue.title, acceptance_criteria=criteria)
        pr = bb.create_pr(
            title=f"[review] {issue.identifier}: {issue.title}",
            source_branch=decision.branch,
            dest_branch="main",
            description=body,
        )
        self.runs.event(run_id, "pr", f"opened {pr.url}")
        return pr

    # ── 2. review ──
    def dispatch_review(self, issue: LinearIssue | None, pr_id: int, repo_slug: str) -> str:
        key = issue.identifier if issue else f"PR-{pr_id}"
        run_id = self._new_run_id()
        self.runs.start(run_id, "review", key, agent=self.s.model_review)

        if self.s.dry_run:
            self.runs.event(run_id, "review", f"[dry-run] would review PR #{pr_id} with {self.s.model_review}")
            self.runs.finish(run_id, "dry_run")
            return run_id

        try:
            bb = self._make_bb(repo_slug)
            pr = bb.get_pr(pr_id)
            wt = gitops.worktree_for(self.s.target_repo_path, pr.source_branch)
            cli = runner.resolve_cli(self.s.cli_for_review, allow_claude_fallback=self.s.codex_fallback_to_claude)
            prompt = prompts.build_review_prompt(issue, pr.title)
            self.runs.event(run_id, "review", f"running {cli} ({self.s.model_review})")
            raw = runner.run_agent(
                cli=cli, prompt=prompt, cwd=wt, read_only=True,
                timeout=self.s.agent_timeout_seconds,
                on_event=lambda p, m: self.runs.event(run_id, p, m),
            )
            result = parse_review_output(raw)
            self._post_review(run_id, bb, pr_id, result)
            self._apply_review_decision(run_id, issue, result)
        except Exception as e:  # noqa: BLE001
            logger.exception("review failed for PR #%d", pr_id)
            if issue:
                self._comment(run_id, issue, f"AI review failed: {e}. Escalating to human review.")
                self._set_state(run_id, issue, WorkflowState.HUMAN_REVIEW)
            self.runs.finish(run_id, "failed")
        return run_id

    def _post_review(self, run_id: str, bb, pr_id: int, result: ReviewResult) -> None:
        bb.post_comment(pr_id, render_review_markdown(result))
        self.runs.event(run_id, "review", f"decision={result.decision}")

    def _apply_review_decision(self, run_id: str, issue: LinearIssue | None, result: ReviewResult) -> None:
        if result.decision == Decision.PASS:
            if issue:
                self._set_state(run_id, issue, WorkflowState.HUMAN_REVIEW)
            self.runs.finish(run_id, "passed")
        elif result.decision == Decision.REQUEST_CHANGES:
            if issue:
                self._set_state(run_id, issue, WorkflowState.NEEDS_FIXES)
            self.runs.finish(run_id, "needs_fixes")
        else:  # require_human_review
            if issue:
                self._set_state(run_id, issue, WorkflowState.HUMAN_REVIEW)
            self.runs.finish(run_id, "human_review")

    # ── 3. fix ──
    def dispatch_fix(self, issue: LinearIssue | None, pr_id: int, repo_slug: str) -> str:
        key = issue.identifier if issue else f"PR-{pr_id}"
        run_id = self._new_run_id()
        self.runs.start(run_id, "fix", key)

        if self.s.dry_run:
            self.runs.event(run_id, "fix", f"[dry-run] would fix PR #{pr_id} with original author")
            self.runs.finish(run_id, "dry_run")
            return run_id

        try:
            bb = self._make_bb(repo_slug)
            pr = bb.get_pr(pr_id)
            meta = parse_meta(bb.get_pr_description(pr_id))
            if not meta or not meta.ai_author:
                if issue:
                    self._comment(run_id, issue, "No ai_author in PR meta — cannot auto-fix; needs human triage.")
                    self._set_state(run_id, issue, WorkflowState.HUMAN_REVIEW)
                bb.post_comment(pr_id, "Cannot auto-fix: original authoring agent unknown (missing PR meta).")
                self.runs.finish(run_id, "human_review")
                return run_id

            unresolved = self._summarize_unresolved(bb, pr_id)
            wt = gitops.worktree_for(self.s.target_repo_path, pr.source_branch)
            fallback = self.s.codex_fallback_to_claude
            cli = runner.resolve_cli(self._cli_family(meta.ai_author), allow_claude_fallback=fallback)
            self.runs.event(run_id, "fix", f"running original author {meta.ai_author} ({cli})")
            runner.run_agent(
                cli=cli, prompt=prompts.build_fix_prompt(issue, unresolved), cwd=wt, read_only=False,
                timeout=self.s.agent_timeout_seconds,
                on_event=lambda p, m: self.runs.event(run_id, p, m),
            )
            gitops.push_branch(wt, pr.source_branch)
            bb.post_comment(pr_id, "Original author agent pushed fixes; re-review triggered.")
            if issue:
                self._set_state(run_id, issue, WorkflowState.AI_REVIEW)
            self.runs.finish(run_id, "passed")
        except Exception as e:  # noqa: BLE001
            logger.exception("fix failed for PR #%d", pr_id)
            if issue:
                self._comment(run_id, issue, f"AI fix failed: {e}. Escalating to human review.")
                self._set_state(run_id, issue, WorkflowState.HUMAN_REVIEW)
            self.runs.finish(run_id, "failed")
        return run_id

    def _summarize_unresolved(self, bb, pr_id: int) -> str:
        lines: list[str] = []
        for t in bb.list_tasks(pr_id):
            if t.get("state") != "RESOLVED":
                lines.append(f"- [task] {t.get('content', {}).get('raw', '')}")
        for c in bb.list_comments(pr_id):
            raw = c.get("content", {}).get("raw", "")
            if raw and "[](dev-orchestrator)" not in raw:
                lines.append(f"- [comment] {raw}")
        return "\n".join(lines)


def render_review_markdown(result: ReviewResult) -> str:
    """Render the JSON ReviewResult into the design §5 human-readable PR comment."""
    status = {
        Decision.PASS: "pass",
        Decision.REQUEST_CHANGES: "needs_fixes",
        Decision.REQUIRE_HUMAN_REVIEW: "human_review_required",
    }[result.decision]
    blocking = "\n".join(
        f"- **{f.title}** ({f.file}:{f.line}) — {f.issue}" for f in result.blocking_findings
    ) or "- none"
    nonblocking = "\n".join(
        f"- {f.title} ({f.file}) — {f.issue}"
        for f in result.findings
        if f not in result.blocking_findings and f.severity != Severity.GOOD
    ) or "- none"
    ac = "\n".join(result.acceptance_criteria) or "- (not assessed)"
    return f"""## AI Review Result

Status: {status}

## Blocking findings

{blocking}

## Non-blocking findings

{nonblocking}

## Acceptance criteria check

{ac}

## Test assessment

- Tests passed: {result.tests_passed}

## Summary

{result.summary}

## Final decision

{result.decision}
"""
