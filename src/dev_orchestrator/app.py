"""FastAPI app: static status page + JSON endpoints (design §2). DRY_RUN aware.

No inbound webhooks → no signature-validation surface. Everything is triggered
by hand from the status page.
"""

from __future__ import annotations

import datetime
import logging
import re
import time
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import sessions as session_radar
from .bitbucket_client import BitbucketClient
from .config import get_settings
from .linear_client import LinearClient
from .models import LinearIssue, WorkflowState
from .orchestrator import Orchestrator
from .runs import RunRegistry

logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parents[2] / "web"

ACTIONABLE_STATES = [
    str(WorkflowState.READY_FOR_AI),
    str(WorkflowState.PR_OPEN),
    str(WorkflowState.NEEDS_FIXES),
    str(WorkflowState.AI_REVIEW),
]


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


_AI_BRANCH_KEY_RE = re.compile(r"ai/([A-Za-z]+-\d+)-")
_PR_TITLE_KEY_RE = re.compile(r"\b([A-Z]{2,}-\d+)\b")


def _pr_linear_key(pr) -> str | None:
    """Best-effort Linear key for a PR: from an ai/<KEY>- branch, else the title."""
    m = _AI_BRANCH_KEY_RE.search(pr.source_branch or "")
    if m:
        return m.group(1).upper()
    m = _PR_TITLE_KEY_RE.search(pr.title or "")
    return m.group(1) if m else None


class DispatchIssue(BaseModel):
    issue_key: str


class DispatchPR(BaseModel):
    issue_key: str | None = None
    pr_id: int
    repo: str | None = None


class DispatchAnswer(BaseModel):
    issue_key: str
    answer: str


class SessionResume(BaseModel):
    session_id: str
    answer: str | None = None
    cwd: str | None = None


class SessionDismiss(BaseModel):
    session_id: str
    last_active: str | None = None


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    app = FastAPI(title="dev-orchestrator", version="0.1.0")

    runs = RunRegistry(s.runs_dir, clock=_now_iso)
    _hydrated = runs.hydrate()  # restore prior runs (+ pinned session ids) across restarts
    if _hydrated:
        logger.info("hydrated %d prior runs from %s", _hydrated, s.runs_dir)
    _dismiss_path = str(Path(s.runs_dir) / "dismissed_sessions.json")

    linear = LinearClient(s.linear_api_key, s.linear_team_id) if s.linear_api_key else None
    state_ids: dict[str, str] = {}
    if linear and s.linear_team_id and not s.dry_run:
        try:
            state_ids = linear.get_state_ids()
        except Exception as e:  # noqa: BLE001
            logger.warning("could not resolve Linear state ids: %s", e)

    # Workspace slug for building issue URLs from a bare key (e.g. a branch's BRI-59).
    linear_url_key = ""
    if linear:
        try:
            linear_url_key = linear.workspace_url_key()
        except Exception as e:  # noqa: BLE001
            logger.warning("could not resolve Linear workspace slug: %s", e)

    def _linear_url(key: str | None) -> str:
        return f"https://linear.app/{linear_url_key}/issue/{key}" if key and linear_url_key else ""

    def make_bb(repo_slug: str | None) -> BitbucketClient:
        workspace = s.bitbucket_workspace
        repo = repo_slug.split("/")[-1] if repo_slug and "/" in repo_slug else (repo_slug or s.bitbucket_repo)
        if repo_slug and "/" in repo_slug:
            workspace = repo_slug.split("/")[0]
        return BitbucketClient(workspace, repo, s.basic_auth_token)

    orch = Orchestrator(s, runs, linear=linear, make_bitbucket=make_bb, state_ids=state_ids)

    # Cache the issues last shown so dispatch endpoints can resolve a key → issue
    # without a second Linear round-trip. Refreshed by GET /api/work.
    issue_cache: dict[str, LinearIssue] = {}

    # ── routes ──
    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/health")
    def health():
        return {"ok": True, "dry_run": s.dry_run, "linear": bool(linear), "workspace": s.bitbucket_workspace}

    @app.get("/api/work")
    def work():
        if not linear:
            return {"dry_run": s.dry_run, "issues": [], "note": "LINEAR_API_KEY not set — no live issues."}
        issues = linear.list_issues_in_states(ACTIONABLE_STATES)
        issue_cache.clear()
        for i in issues:
            issue_cache[i.identifier] = i
        return {"dry_run": s.dry_run, "issues": [asdict(i) for i in issues]}

    @app.get("/api/prs")
    def open_prs():
        """Pull-based review queue: open Bitbucket PRs, matched to Linear where possible.

        Surfaces PRs the orchestrator did NOT open (human- or agent-created), which
        the Linear-state poll alone would miss. No inbound webhook / signature surface.
        """
        try:
            prs = make_bb(None).list_open_prs()
        except Exception as e:  # noqa: BLE001 — no creds / repo unreachable → empty queue
            return {"prs": [], "note": f"could not list PRs: {e}"}
        repo = f"{s.bitbucket_workspace}/{s.bitbucket_repo}"
        out = []
        for p in prs:
            key = _pr_linear_key(p)
            out.append({
                "pr_id": p.pr_id, "title": p.title, "author": p.author,
                "source_branch": p.source_branch, "destination_branch": p.destination_branch,
                "url": p.url, "draft": p.draft, "repo": repo,
                "linear_key": key, "linear_url": _linear_url(key),
                "is_review": p.title.strip().lower().startswith("[review]"),
            })
        # AI review-tagged PRs first, then newest PR id.
        out.sort(key=lambda d: (not d["is_review"], -d["pr_id"]))
        return {"prs": out}

    @app.get("/api/runs")
    def list_runs():
        return {"runs": [_run_summary(r) for r in runs.list()]}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        r = runs.get(run_id)
        if not r:
            raise HTTPException(404, "run not found")
        return {**_run_summary(r), "log": r.log_tail(80)}

    @app.post("/api/dispatch/issue")
    def dispatch_issue(body: DispatchIssue):
        issue = issue_cache.get(body.issue_key)
        if not issue:
            raise HTTPException(404, f"unknown issue {body.issue_key}; refresh /api/work first")
        return {"run_id": orch.dispatch_issue(issue)}

    @app.post("/api/dispatch/review")
    def dispatch_review(body: DispatchPR):
        issue = issue_cache.get(body.issue_key) if body.issue_key else None
        repo = body.repo or (issue.repo if issue else s.bitbucket_repo)
        return {"run_id": orch.dispatch_review(issue, body.pr_id, repo)}

    @app.post("/api/dispatch/fix")
    def dispatch_fix(body: DispatchPR):
        issue = issue_cache.get(body.issue_key) if body.issue_key else None
        repo = body.repo or (issue.repo if issue else s.bitbucket_repo)
        return {"run_id": orch.dispatch_fix(issue, body.pr_id, repo)}

    @app.post("/api/dispatch/answer")
    def dispatch_answer(body: DispatchAnswer):
        issue = issue_cache.get(body.issue_key)
        if not issue:
            raise HTTPException(404, f"unknown issue {body.issue_key}; refresh /api/work first")
        return {"run_id": orch.dispatch_answer(issue, body.answer)}

    def _app_session_ids() -> set[str]:
        return {r.session_id for r in runs.list() if r.session_id}

    @app.get("/api/sessions")
    def list_sessions():
        found = session_radar.scan_sessions(
            s.claude_projects_path,
            idle_seconds=s.session_idle_seconds,
            waiting_max_age_seconds=s.session_waiting_max_age_seconds,
            now=time.time(),
            target_repo_path=s.target_repo_path,
            app_session_ids=_app_session_ids(),
        )
        # Enrich app-managed sessions with the run's parsed questions (needs_input).
        q_by_sid = {r.session_id: r.questions for r in runs.list() if r.session_id and r.questions}
        dismissed = session_radar.load_dismissed(_dismiss_path)
        out = []
        for si in found:
            d = asdict(si)
            d["questions"] = q_by_sid.get(si.session_id, [])
            d["linear_url"] = _linear_url(si.linear_key)
            d["dismissed"] = session_radar.is_dismissed(dismissed, si.session_id, si.last_active)
            out.append(d)
        return {"sessions": out}

    @app.post("/api/sessions/dismiss")
    def dismiss_session(body: SessionDismiss):
        session_radar.dismiss(_dismiss_path, body.session_id, body.last_active or "")
        return {"ok": True}

    @app.post("/api/sessions/resume")
    def resume_session(body: SessionResume):
        # No answer supplied → hand back the command for the user to run interactively.
        if not body.answer:
            return {"command": f"claude --resume {body.session_id}"}
        cwd = body.cwd
        if not cwd:  # recover cwd from the transcript scan
            for si in session_radar.scan_sessions(
                s.claude_projects_path, idle_seconds=s.session_idle_seconds, now=time.time(),
            ):
                if si.session_id == body.session_id:
                    cwd = si.cwd
                    break
        return {"run_id": orch.resume_session(body.session_id, cwd or s.target_repo_path, body.answer)}

    def _run_summary(r) -> dict:
        return {"id": r.id, "kind": r.kind, "issue_key": r.issue_key, "status": r.status,
                "agent": r.agent, "pr_url": r.pr_url,
                "session_id": r.session_id, "questions": r.questions}

    return app


app = create_app()
