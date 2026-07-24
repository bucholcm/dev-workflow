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
from .escalation_parser import parse_escalation
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
        # Pin a Claude session up front so the fix/answer turn can resume it later.
        # Session pinning is claude-only; codex work runs unpinned (no resume support).
        intended_family = self._cli_family(decision.agent) if decision.agent else ""
        session_id = str(uuid.uuid4()) if intended_family == "claude" else ""
        self.runs.start(
            run_id, "implement", issue.identifier,
            agent=decision.agent or "human", session_id=session_id, cli=intended_family,
        )
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
            pin = f" (session {session_id[:8]}…)" if session_id else " (unpinned)"
            msg = f"[dry-run] would run {decision.agent} on branch {decision.branch}{pin}"
            self.runs.event(run_id, "implement", msg)
            self.runs.event(run_id, "pr", "[dry-run] would create PR with meta-block")
            self.runs.finish(run_id, "dry_run")
            return run_id

        # Live path.
        try:
            fallback = self.s.codex_fallback_to_claude
            cli = runner.resolve_cli(self._cli_family(decision.agent), allow_claude_fallback=fallback)
            # Only a claude run can carry the pinned session; a codex fallback runs unpinned.
            run_session = session_id if cli == "claude" else ""
            wt = gitops.prepare_worktree(self.s.target_repo_path, decision.branch)
            is_complex = decision.agent == self.s.model_complex_implementation
            prompt = prompts.build_implement_prompt(issue, is_complex=is_complex)
            self.runs.event(run_id, "implement", f"running {cli} ({decision.agent})")
            raw = runner.run_agent(
                cli=cli, prompt=prompt, cwd=wt, model=None, read_only=False,
                timeout=self.s.agent_timeout_seconds,
                on_event=lambda p, m: self.runs.event(run_id, p, m),
                session_id=run_session or None,
            )
            # The agent may STOP and ask for a human decision instead of coding.
            if self._handle_escalation(run_id, issue, raw):
                return run_id
            if not gitops.has_commits(wt):
                raise RuntimeError("agent produced no commits")
            gitops.push_branch(wt, decision.branch)
            pr = self._open_pr(run_id, issue, decision, session_id=run_session, cli=cli)
            self._set_state(run_id, issue, WorkflowState.PR_OPEN)
            self.runs.finish(run_id, "passed", pr_url=pr.url)
        except Exception as e:  # noqa: BLE001 — any failure escalates to human review
            logger.exception("implement failed for %s", issue.identifier)
            self._comment(run_id, issue, f"AI implementation failed: {e}")
            self._set_state(run_id, issue, WorkflowState.HUMAN_REVIEW)
            self.runs.finish(run_id, "failed")
        return run_id

    def _handle_escalation(self, run_id: str, issue: LinearIssue | None, raw: str) -> bool:
        """If the agent asked for human input, record it and stop. Returns True when escalated."""
        esc = parse_escalation(raw)
        if esc.get("status") != "needs_input":
            return False
        questions = esc.get("questions") or []
        self.runs.set_questions(run_id, questions)
        pretty = "\n".join(f"- {q}" for q in questions) or "- (no specific questions provided)"
        if issue:
            self._comment(run_id, issue, f"AI needs human input before continuing:\n\n{pretty}")
            self._set_state(run_id, issue, WorkflowState.HUMAN_REVIEW)
        self.runs.event(run_id, "needs_input", f"{len(questions)} question(s) awaiting the human")
        self.runs.finish(run_id, "needs_input")
        return True

    def _open_pr(self, run_id: str, issue: LinearIssue, decision, *, session_id: str = "", cli: str = ""):
        bb = self._make_bb(issue.repo)
        meta = PRMeta(
            linear_issue=issue.identifier,
            linear_issue_id=issue.id,
            ai_author=decision.agent,
            ai_reviewer=decision.reviewer,
            ai_complexity=str(issue.size or ""),
            ai_risk=str(issue.risk or ""),
            ai_session_id=session_id,
            ai_cli=cli,
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
            wt = gitops.ensure_worktree_for_branch(self.s.target_repo_path, pr.source_branch)
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
            wt = gitops.ensure_worktree_for_branch(self.s.target_repo_path, pr.source_branch)
            fallback = self.s.codex_fallback_to_claude
            cli = runner.resolve_cli(self._cli_family(meta.ai_author), allow_claude_fallback=fallback)
            # Resume the ORIGINAL session so the fix keeps full authoring context
            # (claude-only; codex meta or an absent id falls back to a cold prompt).
            resume = bool(meta.ai_session_id) and cli == "claude" and meta.ai_cli == "claude"
            how = f"resuming session {meta.ai_session_id[:8]}…" if resume else "cold prompt (no resumable session)"
            self.runs.event(run_id, "fix", f"running original author {meta.ai_author} ({cli}) — {how}")
            raw = runner.run_agent(
                cli=cli, prompt=prompts.build_fix_prompt(issue, unresolved), cwd=wt, read_only=False,
                timeout=self.s.agent_timeout_seconds,
                on_event=lambda p, m: self.runs.event(run_id, p, m),
                session_id=meta.ai_session_id or None, resume=resume,
            )
            if self._handle_escalation(run_id, issue, raw):
                return run_id
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

    # ── 4. answer (resume a paused session with the human's decision) ──
    def dispatch_answer(self, issue: LinearIssue, answer: str) -> str:
        """Resume the original Claude session, feeding the human's answer, then continue.

        Targets a run that previously finished `needs_input`. Resume is claude-only;
        if there is no resumable session we report that instead of silently guessing.
        """
        run_id = self._new_run_id()
        prior = self.runs.latest_for_issue(issue.identifier)
        session_id = prior.session_id if prior else ""
        cli = prior.cli if prior else ""
        self.runs.start(run_id, "resume", issue.identifier, agent=(prior.agent if prior else ""),
                        session_id=session_id, cli=cli)

        if not session_id or cli != "claude":
            self.runs.event(run_id, "resume", "no resumable Claude session for this issue (codex or unpinned)")
            self.runs.finish(run_id, "failed")
            return run_id

        if self.s.dry_run:
            self.runs.event(run_id, "resume", f"[dry-run] would resume session {session_id[:8]}… with the answer")
            self.runs.finish(run_id, "dry_run")
            return run_id

        try:
            decision = route(issue, self.s)
            wt = gitops.worktree_for(self.s.target_repo_path, decision.branch)
            self.runs.event(run_id, "resume", f"resuming session {session_id[:8]}… ({decision.agent})")
            raw = runner.run_agent(
                cli="claude", prompt=prompts.build_answer_prompt(answer), cwd=wt, read_only=False,
                timeout=self.s.agent_timeout_seconds,
                on_event=lambda p, m: self.runs.event(run_id, p, m),
                session_id=session_id, resume=True,
            )
            if self._handle_escalation(run_id, issue, raw):  # may pause again on a follow-up question
                return run_id
            if not gitops.has_commits(wt):
                raise RuntimeError("agent answered but produced no commits")
            gitops.push_branch(wt, decision.branch)
            pr = self._open_pr(run_id, issue, decision, session_id=session_id, cli="claude")
            self._set_state(run_id, issue, WorkflowState.PR_OPEN)
            self.runs.finish(run_id, "passed", pr_url=pr.url)
        except Exception as e:  # noqa: BLE001
            logger.exception("answer/resume failed for %s", issue.identifier)
            self._comment(run_id, issue, f"AI resume failed: {e}. Escalating to human review.")
            self._set_state(run_id, issue, WorkflowState.HUMAN_REVIEW)
            self.runs.finish(run_id, "failed")
        return run_id

    def resume_session(self, session_id: str, cwd: str, answer: str) -> str:
        """Resume ANY Claude session (radar action) with the human's answer, headless.

        Unlike dispatch_answer this is not tied to a Linear issue — it just continues
        the session in its own working directory. Claude-only.
        """
        run_id = self._new_run_id()
        self.runs.start(run_id, "resume", session_id[:12], session_id=session_id, cli="claude")
        if self.s.dry_run:
            self.runs.event(run_id, "resume", f"[dry-run] would resume session {session_id[:8]}… in {cwd}")
            self.runs.finish(run_id, "dry_run")
            return run_id
        try:
            self.runs.event(run_id, "resume", f"resuming session {session_id[:8]}… in {cwd}")
            raw = runner.run_agent(
                cli="claude", prompt=prompts.build_answer_prompt(answer), cwd=cwd, read_only=False,
                timeout=self.s.agent_timeout_seconds,
                on_event=lambda p, m: self.runs.event(run_id, p, m),
                session_id=session_id, resume=True,
            )
            if not self._handle_escalation(run_id, None, raw):
                self.runs.finish(run_id, "passed")
        except Exception as e:  # noqa: BLE001
            logger.exception("resume_session failed for %s", session_id)
            self.runs.event(run_id, "resume", f"failed: {e}")
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
