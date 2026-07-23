"""Thin git helpers for preparing an isolated checkout per run.

Per-run worktree isolation (adapted idea from simion/reviewd + OpenAI Symphony):
each dispatch runs in its own `.dev-orchestrator-worktrees/<branch-slug>` so
parallel agents never clobber each other's working tree.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

WORKTREE_DIR = ".dev-orchestrator-worktrees"


def _run(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def prepare_worktree(repo_path: str, branch: str, base_branch: str = "main") -> str:
    """Create (or reuse) a worktree on `branch` off `base_branch`. Returns its path."""
    slug = branch.replace("/", "-")
    wt = Path(repo_path) / WORKTREE_DIR / slug
    wt.parent.mkdir(parents=True, exist_ok=True)
    if wt.exists():
        return str(wt)

    _run(["git", "fetch", "origin", base_branch], repo_path)
    # New branch off origin/base; fall back to local base if origin ref missing.
    res = _run(["git", "worktree", "add", "-b", branch, str(wt), f"origin/{base_branch}"], repo_path)
    if res.returncode != 0:
        res = _run(["git", "worktree", "add", "-b", branch, str(wt), base_branch], repo_path)
    if res.returncode != 0:
        raise RuntimeError(f"git worktree add failed for {branch}: {res.stderr.strip()}")
    logger.info("Prepared worktree %s on %s", wt, branch)
    return str(wt)


def worktree_for(repo_path: str, branch: str) -> str:
    """Path of an existing worktree for `branch` (for review/fix on an open PR)."""
    return str(Path(repo_path) / WORKTREE_DIR / branch.replace("/", "-"))


def push_branch(worktree_path: str, branch: str) -> None:
    res = _run(["git", "push", "-u", "origin", branch], worktree_path)
    if res.returncode != 0:
        raise RuntimeError(f"git push failed for {branch}: {res.stderr.strip()}")


def has_commits(worktree_path: str, base_branch: str = "main") -> bool:
    """True if the worktree branch has commits ahead of base (something to PR)."""
    res = _run(["git", "rev-list", "--count", f"origin/{base_branch}..HEAD"], worktree_path)
    return res.returncode == 0 and res.stdout.strip() not in ("", "0")
