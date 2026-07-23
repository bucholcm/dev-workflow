"""FastAPI app: static status page + JSON endpoints (design §2). DRY_RUN aware.

No inbound webhooks → no signature-validation surface. Everything is triggered
by hand from the status page.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

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


class DispatchIssue(BaseModel):
    issue_key: str


class DispatchPR(BaseModel):
    issue_key: str | None = None
    pr_id: int
    repo: str | None = None


def create_app() -> FastAPI:
    s = get_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    app = FastAPI(title="dev-orchestrator", version="0.1.0")

    runs = RunRegistry(s.runs_dir, clock=_now_iso)

    linear = LinearClient(s.linear_api_key, s.linear_team_id) if s.linear_api_key else None
    state_ids: dict[str, str] = {}
    if linear and s.linear_team_id and not s.dry_run:
        try:
            state_ids = linear.get_state_ids()
        except Exception as e:  # noqa: BLE001
            logger.warning("could not resolve Linear state ids: %s", e)

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

    def _run_summary(r) -> dict:
        return {"id": r.id, "kind": r.kind, "issue_key": r.issue_key, "status": r.status,
                "agent": r.agent, "pr_url": r.pr_url}

    return app


app = create_app()
