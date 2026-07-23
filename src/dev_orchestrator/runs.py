"""Run registry: live runs in memory + per-run JSONL on disk (design §5/§7).

No database. Linear state + the Bitbucket PR remain the durable source of truth;
this only powers the live status page and a restart-surviving history.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

RunKind = Literal["implement", "review", "fix"]
RunStatus = Literal["running", "passed", "needs_fixes", "human_review", "failed", "dry_run"]


@dataclass
class RunEvent:
    ts: str
    phase: str
    message: str


@dataclass
class Run:
    id: str
    kind: RunKind
    issue_key: str
    status: RunStatus = "running"
    agent: str = ""
    pr_url: str = ""
    events: list[RunEvent] = field(default_factory=list)

    def log_tail(self, n: int = 40) -> list[dict]:
        return [asdict(e) for e in self.events[-n:]]


class RunRegistry:
    """Thread-safe in-memory registry that also appends events to runs/<id>.jsonl."""

    def __init__(self, runs_dir: str, clock):
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self._dir = Path(runs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock  # injected callable → ISO timestamp string (keeps this testable)

    def _append_jsonl(self, run_id: str, record: dict) -> None:
        with (self._dir / f"{run_id}.jsonl").open("a") as fh:
            fh.write(json.dumps(record) + "\n")

    def start(self, run_id: str, kind: RunKind, issue_key: str, agent: str = "", pr_url: str = "") -> Run:
        run = Run(id=run_id, kind=kind, issue_key=issue_key, agent=agent, pr_url=pr_url)
        with self._lock:
            self._runs[run_id] = run
        self._append_jsonl(run_id, {"event": "start", "kind": kind, "issue": issue_key, "agent": agent})
        return run

    def event(self, run_id: str, phase: str, message: str) -> None:
        ev = RunEvent(ts=self._clock(), phase=phase, message=message)
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                run.events.append(ev)
        self._append_jsonl(run_id, {"event": "log", **asdict(ev)})

    def finish(self, run_id: str, status: RunStatus, pr_url: str = "") -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                run.status = status
                if pr_url:
                    run.pr_url = pr_url
        self._append_jsonl(run_id, {"event": "finish", "status": status, "pr_url": pr_url})

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def list(self) -> list[Run]:
        with self._lock:
            return list(self._runs.values())

    def active_for_issue(self, issue_key: str) -> Run | None:
        """Idempotency guard: is a run already active for this issue?"""
        with self._lock:
            for run in self._runs.values():
                if run.issue_key == issue_key and run.status == "running":
                    return run
        return None
