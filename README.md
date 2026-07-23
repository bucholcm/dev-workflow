# dev-orchestrator

A small **local** app that orchestrates an AI-assisted dev workflow across
**Linear** (planning source of truth) and **Bitbucket Cloud** (code), routing
implementation to **Claude Code** (`claude -p`) or **Codex** (`codex exec`) and
driving a **review → fix → human-merge** loop. You trigger every step by hand
from a simple local status page. No webhooks, no database, no CI secrets for
agent keys.

Built from [`dev-orchestrator-design.md`](./dev-orchestrator-design.md). Bitbucket
client, LLM-JSON parsing, and the subprocess/worktree runner are adapted from
[simion/reviewd](https://github.com/simion/reviewd) (MIT) — see
[`src/dev_orchestrator/vendor/README.md`](./src/dev_orchestrator/vendor/README.md).

## How the workflow works

```
Linear "Ready for AI"  ──Run──▶  route (size/risk) ──▶  claude -p / codex exec
        │                                                        │
        │                                                  commit + push
        ▼                                                        ▼
   status page  ◀── live runs ──                        Bitbucket PR ([review])
        │                                                        │
        └──Review──▶ codex exec (MODEL_REVIEW) ──▶ JSON verdict ──┤
                          pass → Human Review                     │
                          needs_fixes → Needs Fixes ──Fix──▶ original author
                          unparseable → Human Review              │
                                                          human merges (no auto-merge)
```

- **Routing** (`routing.py`, pure): `xs/s` → simple model, `m/l/xl` → complex model,
  `risk=high` → human gate. An `agent:<model>` label overrides.
- **Review** emits a **JSON contract** parsed by `review_parser.py`; any parse
  failure fails safe to `require_human_review`.
- **Fix** is always done by the **original authoring agent** (recovered from the
  PR meta-block), never the reviewer.

## Quick start (DRY_RUN — no credentials needed)

```bash
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env            # DRY_RUN=true by default
python -m dev_orchestrator      # → http://127.0.0.1:8787
pytest                          # run the test suite
```

In **DRY_RUN** every intended Linear/Bitbucket/git action is logged to the run
(visible on the page and in `runs/<id>.jsonl`) but nothing external is mutated.

## Going live

1. **Linear setup**
   - Create the workflow states (names matched case-sensitively):
     `Triage · Ready for Specification · Specified · Ready for AI · AI In Progress ·
     PR Open · AI Review · Needs Fixes · Human Review · Ready to Merge · Done`.
   - Add **labels** (this build reads labels, not custom fields — swap
     `linear_client._parse_labels` if you use custom fields):
     `size:xs|s|m|l|xl`, `risk:low|medium|high`, optional `agent:<model>`,
     `repo:workspace/repo`.
   - Set `LINEAR_API_KEY` and `LINEAR_TEAM_ID` in `.env`.
2. **Bitbucket setup** — App Password with **Pull requests: Write**. Either set
   `BITBUCKET_USERNAME`/`BITBUCKET_TOKEN` in `.env`, or rely on the existing
   `~/.zshrc` / `~/.config/bridge-ai/bitbucket.env` (same source as `bin/bb`).
   Set `BITBUCKET_WORKSPACE` (and `BITBUCKET_REPO` as a default).
3. **Agents** — `claude` on PATH; install `codex`
   (`npm install -g @openai/codex`) or set `CODEX_FALLBACK_TO_CLAUDE=true` to run
   codex-routed work through Claude until Codex is installed.
4. **Target checkout** — `TARGET_REPO_PATH=/path/to/your/repo` (a git checkout of
   the Bitbucket repo; runs happen in per-run worktrees under it).
5. Set `DRY_RUN=false` and restart.

## Environment variables

See [`.env.example`](./.env.example). Model names are always env vars, never
hardcoded.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Status page |
| GET | `/health` | mode + connectivity |
| GET | `/api/work` | Linear issues in actionable states |
| GET | `/api/runs`, `/api/runs/{id}` | live + recent runs, log tail |
| POST | `/api/dispatch/issue` | implement an issue |
| POST | `/api/dispatch/review` | review a PR (`MODEL_REVIEW`) |
| POST | `/api/dispatch/fix` | fix PR comments (original author) |

No inbound webhooks → no signature-validation surface.

## Testing with DRY_RUN

`pytest` covers routing, PR-meta round-trip, review-JSON parsing (incl.
parse-fail → human review), Linear label parsing, and the DRY_RUN contract
(no external writes; JSONL history written).

## Troubleshooting

- **No issues on the page** — `LINEAR_API_KEY` unset, or no issues in the
  actionable states. `/health` shows `linear: false` when the key is missing.
- **`codex CLI not found`** — install Codex or set `CODEX_FALLBACK_TO_CLAUDE=true`.
- **PR create fails** — check the App Password scope and that
  `BITBUCKET_WORKSPACE`/repo resolve (the `repo:` label is `workspace/repo`).
- **Review always says human_review** — the reviewer didn't emit valid JSON;
  the raw output is logged. This is the intended fail-safe.

## Manual agent-routing override

Add an `agent:<model>` label to the Linear issue (e.g. `agent:opus-4.8` to force
the complex model, or `agent:human` to force a human gate). It overrides the
size-based rule.

## Non-goals (MVP)

n8n · webhooks/tunnel · auto-merge · persistent DB · long-term agent memory ·
multi-agent debate · Slack · full dashboard.
