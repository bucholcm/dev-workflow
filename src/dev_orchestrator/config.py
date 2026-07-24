"""Runtime configuration (pydantic-settings). Loaded from environment / .env.

Model names are NEVER hardcoded (design §1) — always env vars. Bitbucket creds
are resolved with the same precedence as bridge-ai-poc's bin/bb so the two share
one credential source:
  1. ~/.config/bridge-ai/bitbucket.env   (preferred, chmod 600)
  2. `export BITBUCKET_(USERNAME|TOKEN)=` lines in ~/.zshrc  (fallback)
  3. ambient env / .env
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_bitbucket_creds_from_disk() -> dict[str, str]:
    """Best-effort load of BITBUCKET_USERNAME / BITBUCKET_TOKEN from bin/bb's sources.

    Only fills values not already present in the ambient environment.
    """
    out: dict[str, str] = {}
    envfile = Path.home() / ".config" / "bridge-ai" / "bitbucket.env"
    zshrc = Path.home() / ".zshrc"
    pat = re.compile(r'^\s*export\s+(BITBUCKET_(?:USERNAME|TOKEN))=(.+)$')

    def parse(text: str) -> None:
        for line in text.splitlines():
            m = pat.match(line) or re.match(r'^\s*(BITBUCKET_(?:USERNAME|TOKEN))=(.+)$', line)
            if m:
                key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
                out.setdefault(key, val)

    if envfile.is_file():
        try:
            parse(envfile.read_text())
        except OSError:
            pass
    if zshrc.is_file():
        try:
            parse(zshrc.read_text())
        except OSError:
            pass
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Linear ──
    linear_api_key: str = ""
    linear_team_id: str = ""

    # ── Bitbucket ──
    bitbucket_workspace: str = ""
    bitbucket_repo: str = ""
    bitbucket_username: str = ""
    bitbucket_token: str = ""

    # ── Agent models (never hardcoded) ──
    model_simple_implementation: str = "codex-5.4"
    model_complex_implementation: str = "opus-4.8"
    model_review: str = "codex-5.5"

    # Which CLI drives each logical model. Maps a model env value to a CLI family.
    # Values: "claude" | "codex". Codex-family models that aren't installed fall
    # back to claude when `codex_fallback_to_claude` is true.
    cli_for_simple: str = "codex"
    cli_for_complex: str = "claude"
    cli_for_review: str = "codex"
    codex_fallback_to_claude: bool = True

    # ── Runtime ──
    target_repo_path: str = ""     # working checkout where claude/codex run
    runs_dir: str = "runs"
    log_level: str = "info"
    dry_run: bool = True           # default ON so it runs with zero live creds
    agent_timeout_seconds: int = 900

    # ── Session radar (scans Claude Code transcripts for "needs your attention") ──
    claude_projects_dir: str = ""  # default ~/.claude/projects (resolved below)
    session_idle_seconds: int = 45  # newer than this = "running"; older + pending = "waiting"
    session_waiting_max_age_seconds: int = 172800  # 2d — older pending turns aren't "waiting on you"

    @property
    def claude_projects_path(self) -> str:
        return self.claude_projects_dir or str(Path.home() / ".claude" / "projects")

    def resolve_bitbucket(self) -> Settings:
        """Fill missing Bitbucket creds from bin/bb's on-disk sources."""
        if not (self.bitbucket_username and self.bitbucket_token):
            disk = _load_bitbucket_creds_from_disk()
            if not self.bitbucket_username:
                self.bitbucket_username = os.environ.get("BITBUCKET_USERNAME", "") or disk.get("BITBUCKET_USERNAME", "")
            if not self.bitbucket_token:
                self.bitbucket_token = os.environ.get("BITBUCKET_TOKEN", "") or disk.get("BITBUCKET_TOKEN", "")
        return self

    @property
    def basic_auth_token(self) -> str:
        """`email:token` form consumed by the vendored Bitbucket client."""
        return f"{self.bitbucket_username}:{self.bitbucket_token}"


@lru_cache
def get_settings() -> Settings:
    return Settings().resolve_bitbucket()
