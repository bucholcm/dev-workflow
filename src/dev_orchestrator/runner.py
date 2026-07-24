"""Agent runner: invoke `claude -p` / `codex exec` headless in a repo checkout.

The subprocess + process-group handling and the CLI invocation shapes are
adapted from simion/reviewd (MIT) — see vendor/README.md. Retargeted here to
also support implement/fix (write-enabled) runs, not just read-only review.

CLI shapes confirmed from reviewd:
  claude : claude --print [--disallowedTools Write,Edit] --model M -p <prompt>
  codex  : codex exec [--sandbox workspace-write] --model M -   (prompt via stdin)
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import signal
import subprocess

logger = logging.getLogger(__name__)

_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}


class AgentUnavailable(RuntimeError):
    """Raised when the required CLI is not installed."""


def cli_available(cli: str) -> bool:
    return shutil.which(cli) is not None


def resolve_cli(family: str, *, allow_claude_fallback: bool) -> str:
    """Return the CLI binary to run for a logical family ('claude' | 'codex').

    Falls back to claude when codex is requested but not installed (config-gated).
    """
    if family == "codex":
        if cli_available("codex"):
            return "codex"
        if allow_claude_fallback and cli_available("claude"):
            logger.warning("codex not installed → falling back to claude")
            return "claude"
        raise AgentUnavailable("codex CLI not found (install: npm install -g @openai/codex)")
    if family == "claude":
        if cli_available("claude"):
            return "claude"
        raise AgentUnavailable("claude CLI not found (https://github.com/anthropics/claude-code)")
    raise ValueError(f"unknown CLI family: {family}")


def _build_command(
    cli: str,
    prompt: str,
    *,
    model: str | None,
    read_only: bool,
    session_id: str | None = None,
    resume: bool = False,
) -> tuple[list[str], str | None]:
    """Return (argv, stdin_input). stdin_input is None when the prompt goes via a flag.

    Session handling is claude-only. On a fresh run pass `--session-id <uuid>` to
    pin a known session; on a later turn pass `--resume <uuid>` to continue that
    exact session (design: session management). Codex has a separate resume model,
    so the session args are ignored for the codex CLI.
    """
    model_args = ["--model", model] if model else []
    if cli == "claude":
        base = ["claude", "--print"]
        if session_id:
            base += ["--resume", session_id] if resume else ["--session-id", session_id]
        if read_only:
            base += ["--disallowedTools", "Write,Edit"]
        return [*base, *model_args, "-p", prompt], None
    if cli == "codex":
        base = ["codex", "exec"]
        base += ["--sandbox", "read-only" if read_only else "workspace-write"]
        return [*base, *model_args, "-"], prompt  # prompt via stdin
    raise ValueError(f"unknown CLI: {cli}")


def run_agent(
    *,
    cli: str,
    prompt: str,
    cwd: str,
    model: str | None = None,
    read_only: bool = False,
    timeout: int = 900,
    on_event=None,
    session_id: str | None = None,
    resume: bool = False,
) -> str:
    """Run the agent CLI headless in `cwd`, streaming stderr to `on_event`. Returns stdout.

    Raises AgentUnavailable / RuntimeError on failure so the orchestrator can
    move the Linear issue to Human Review.

    `session_id` pins a claude session (or resumes it when `resume=True`) so a
    later fix/answer turn continues the original conversation with full context.
    """
    argv, stdin_input = _build_command(
        cli, prompt, model=model, read_only=read_only, session_id=session_id, resume=resume
    )
    if on_event:
        on_event("spawn", f"{cli} {'(read-only)' if read_only else ''} in {cwd}")

    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.PIPE if stdin_input is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_GIT_ENV,
        start_new_session=True,  # own process group → clean kill on timeout
    )
    try:
        stdout, stderr = proc.communicate(input=stdin_input, timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
        raise RuntimeError(f"{cli} timed out after {timeout}s") from None

    if stderr and on_event:
        for line in stderr.splitlines()[-20:]:
            on_event("stderr", line)
    if proc.returncode != 0:
        raise RuntimeError(f"{cli} exited {proc.returncode}: {stderr[:500]}")
    return stdout
