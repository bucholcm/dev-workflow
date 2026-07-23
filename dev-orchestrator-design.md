# dev-orchestrator — Design Spec

> A small **local** app that orchestrates an AI-assisted dev workflow across
> **Linear** (planning) and **Bitbucket Cloud** (code), routing implementation
> work to **Claude Code** (`claude -p`) or **Codex** (`codex exec`) and driving a
> review → fix → human-merge loop. You trigger everything by hand from a simple
> local status page.
>
> Standalone project — intended to be developed in its own repo, outside
> `bridge-ai-poc`. Derived from `ai-dev-workflow-bitbucket-linear-claude-code.md`
> but adapted to the decisions below.
>
> Date: 2026-07-09

---

## 1. Locked decisions (the brainstorm result)

These reshape the original brief. Where they conflict with the brief, **these win.**

| # | Decision | Consequence |
|---|---|---|
| D1 | **Linear is the planning source of truth** (live since 2026-07-08). | The app reads/writes Linear issue state, fields, comments. |
| D2 | **Execution = a small local app** that shells out to **`claude -p`** (complex/Opus) and **`codex exec`** (simple). NOT agents-inside-Bitbucket-Pipelines. | No CI secrets for agent keys; reuses existing Claude Code billing (same rate as interactive; only *volume* grows). Codex is a separate OpenAI bill. |
| D3 | **Trigger = manual**, from a **simple local HTML status page** showing live Codex/Claude work. Not a webhook daemon. | No public URL, no tunnel, **no webhook signature surface**. Human-in-the-loop by construction. |
| D4 | **Standalone repo, Python + FastAPI**, one static page + JSON endpoints. | Reuses a known stack; keeps dev-tooling out of product code; can target multiple repos later. |
| D5 | **State = in-memory live runs + per-run JSONL on disk.** Linear + the Bitbucket PR remain the durable source of truth. | No database server. History survives restart via JSONL. |
| D6 | Reviewer = `MODEL_REVIEW` (Codex 5.5). **Original author fixes** its own PR comments. **Human is the only merge gate** — no auto-merge in MVP. | Matches the brief's roles. |

**Model names are never hardcoded** — always env vars:

```env
MODEL_SIMPLE_IMPLEMENTATION=codex-5.4
MODEL_COMPLEX_IMPLEMENTATION=opus-4.8
MODEL_REVIEW=codex-5.5
```

---

## 2. Architecture

Thin choreographer, not a workflow DB. **Linear owns planning state, Bitbucket
owns code state, the app owns only routing + the live view of runs.**

```
┌─ browser: status page (one static HTML, polls /api/*) ─────────────┐
│  • lists Linear issues in actionable states                        │
│  • shows live Codex/Claude runs (phase + log tail)                 │
│  • buttons: Run · Review · Fix                                     │
└───────────────┬────────────────────────────────────────────────────┘
                │ JSON
        FastAPI app (local)
   ┌────────────┼───────────────┬──────────────┬───────────────┐
   │            │               │              │               │
 Linear      routing         agent runner     Bitbucket     run registry
 client    (pure fn)      claude -p / codex   client        in-mem + JSONL
 (GraphQL)                 exec, in a repo    (via bin/bb)   (history/status)
                            checkout
```

### Repo layout

```
dev-orchestrator/
  app.py                 # FastAPI: static page + JSON endpoints, DRY_RUN aware
  config.py              # pydantic-settings: Linear/BB creds, model env vars, paths
  linear_client.py       # GraphQL: list issues by state, read fields, set state, comment
  bitbucket_client.py    # wraps bin/bb: create PR, comment, get PR/comments/diff
  routing.py             # PURE fn: issue fields → {agent, reviewer, branch, human_gate}
  runner.py              # invoke claude -p / codex exec headless, stream events
  pr_meta.py             # parse/format PR meta-block + branch naming
  review_parser.py       # structured AI-review text → decision (fail → human_review)
  runs.py                # active runs (memory) + append per-run .jsonl to disk
  orchestrator.py        # the choreography (ties the above together per action)
  web/index.html         # single static status page (vanilla JS)
  runs/                  # *.jsonl run logs (gitignored)
  tests/
  README.md  AGENTS.md  CLAUDE.md  .env.example
```

### Component boundaries (each independently testable)

- `routing` and `review_parser` are **pure functions** (no I/O).
- `linear_client` / `bitbucket_client` are the **only** things that talk to the outside world.
- `orchestrator` is the **only** thing that sequences them.
- `runner` is the **only** thing that spawns agent subprocesses.

### Endpoints

- `GET  /` → status page
- `GET  /api/work` → Linear issues in `Ready for AI` / `PR Open` / `Needs Fixes` (+ open PRs)
- `GET  /api/runs`, `GET /api/runs/{id}` → live + recent runs, log tail
- `POST /api/dispatch/issue` → implement an issue (routes to an agent)
- `POST /api/dispatch/review` → review a PR (`MODEL_REVIEW`)
- `POST /api/dispatch/fix` → fix PR comments (original authoring agent)
- `GET  /health`
- No inbound webhooks → **no signature-validation surface**.

---

## 3. Workflow loop (manual-triggered)

1. **See work.** Page lists Linear issues in `Ready for AI`. You click **Run**.
2. **Validate.** App checks required fields (size, risk, target repo, acceptance
   criteria). Missing → do **not** run; comment on Linear listing what's missing;
   leave issue in `Ready for Specification`.
3. **Route** (deterministic, pure fn — see §4). Decide agent (codex/opus) or
   **human gate** (risk == high).
4. **Prepare.** Set Linear → `AI In Progress`; write `agent` / `reviewer` /
   `branch` fields; create branch `ai/<LINEAR_KEY>-<slug>` in the target repo checkout.
5. **Implement.** `runner` invokes the chosen CLI headless (`claude -p` or
   `codex exec`) with a prompt built from the Linear issue + repo `AGENTS.md` /
   `CLAUDE.md`. Events stream to the run's JSONL + the page.
6. **Open PR.** Agent commits + pushes; app creates the PR via `bin/bb` with
   title prefix `[review]` and a **meta-block** (both the bridge-ai `pr-meta`
   convention *and* the brief's `ai-workflow` machine block — see §6). Set Linear
   → `PR Open`, store `pr_url`.
7. **Review.** You click **Review**. `runner` invokes `MODEL_REVIEW`
   (`codex exec`), which emits the structured review format (§5). `review_parser`
   extracts the decision:
   - `pass` → post PR comment "AI review passed"; Linear → `Human Review`.
   - `needs_fixes` → Linear → `Needs Fixes`; page shows a **Fix** button.
   - unparseable → treat as `require_human_review`.
8. **Fix.** You click **Fix**. `runner` invokes the **original author** agent
   (from PR meta `ai_author`) with the unresolved comments/tasks + the smallest-fix
   instruction; it pushes to the same branch; Linear → `AI Review`; re-review.
9. **Merge.** A **human** merges in the Bitbucket UI. No auto-merge.

### Linear statuses used

`Triage · Ready for Specification · Specified · Ready for AI · AI In Progress ·
PR Open · AI Review · Needs Fixes · Human Review · Ready to Merge · Done`

### Linear fields / labels

```
size: xs | s | m | l | xl
risk: low | medium | high
agent: codex-5.4 | opus-4.8 | human
reviewer: codex-5.5 | human
repo: workspace/repo
branch: ai/DEV-123-short-slug
pr_url: https://bitbucket.org/...
```

---

## 4. Routing (deterministic, pure function)

```
if risk == high:               → human gate (require explicit human approval/override before AI run)
if size in [xs, s]:            implementation_agent = MODEL_SIMPLE_IMPLEMENTATION   # codex
if size in [m, l, xl]:         implementation_agent = MODEL_COMPLEX_IMPLEMENTATION  # opus
reviewer:                      MODEL_REVIEW                                         # codex-5.5
fix agent:                     same agent that authored the PR (from PR meta ai_author)
```

| Issue type | Agent |
|---|---|
| Small bug / simple UI tweak / test addition / isolated refactor | Codex (simple) |
| Multi-file feature / architecture-sensitive / data model change | Opus (complex) |
| Auth / permissions / security | Human gate + AI support |
| Migration / destructive data change | Human gate + AI support |
| PR review / delivery check | Codex 5.5 (`MODEL_REVIEW`) |

`routing.py` takes issue fields, returns `{agent, reviewer, branch, human_gate: bool, reason}`. No I/O — fully unit-testable.

---

## 5. AI review output contract

The reviewer must always emit this structure; `review_parser` reads the **Final decision** line.

```markdown
## AI Review Result
Status: pass | needs_fixes | human_review_required

## Blocking findings
- ...

## Non-blocking findings
- ...

## Acceptance criteria check
- [x] Criterion 1
- [ ] Criterion 2

## Test assessment
- Tests run:
- Missing tests:
- Risk areas:

## Final decision
pass | request_changes | require_human_review
```

**Parse failure → `require_human_review`** (fail safe, never fail open).

---

## 6. Bitbucket conventions

**Branch:** `ai/<LINEAR_KEY>-<short-slug>` (e.g. `ai/DEV-123-payment-validation`).

**PR title** carries pipeline state as a prefix (bridge-ai convention):
`[review]` → `[changes-requested]` / `[qa]` → `[merge-queue]` etc. Initial =
`[review]`.

**PR description** includes a machine-readable block…

```markdown
<!-- ai-workflow
linear_issue: DEV-123
linear_issue_id: <linear-id>
ai_author: codex-5.4
ai_reviewer: codex-5.5
ai_complexity: s
ai_risk: low
source: dev-orchestrator
-->
```

…plus the human-readable Summary / Linear issue / Acceptance criteria / Test plan
/ AI metadata sections. All Bitbucket I/O goes through **`bin/bb`** (App Password,
Basic auth, `Pull requests: Write`; loads creds fresh from disk). Never raw curl,
never a hardcoded token; manual fallback = surface the PR-create link + title +
meta text for a human to paste.

---

## 7. State, idempotency, error handling, security

**State (D5):** active runs in memory (for the live page); each run appends
events to `runs/<run-id>.jsonl`. Linear state + PR meta are the durable truth.

**Idempotency:** before acting, check current Linear state / PR meta ("already
handled?"); an in-memory guard prevents double-dispatching the same issue while a
run is active. Event keys: `linear:<event>:<issue>:<state>`,
`bitbucket:<pr-id>:<updated-on>`, `run:<issue>:<action>:<sha>`.

**Error handling:**
- Missing required Linear fields → don't run; comment the missing fields; keep/return to `Ready for Specification`.
- Agent/branch/PR trigger fails → write error note to Linear; move to `Needs Fixes` or `Human Review`.
- AI implementation fails → post failure summary; move to `Human Review`.
- Review decision unparseable → `require_human_review`.
- Original author agent unavailable/misconfigured → `Human Review` + a PR comment explaining.

**Security:** never log secrets/tokens/keys; least-privilege Linear + Bitbucket
credentials; `DRY_RUN=true` logs intended actions without mutating Linear/Bitbucket
or creating branches/PRs. No inbound webhooks → nothing unauthenticated to accept.

---

## 8. MVP build order (smallest reliable first)

Each slice is independently useful and testable.

1. **Walking skeleton.** `config` + `linear_client` (list `Ready for AI`, set
   state, comment) + status page that lists work + `DRY_RUN`. *No agent runs yet —
   just see Linear work on the page and prove Linear I/O.*
2. **Implement path (one agent).** `routing` + `pr_meta` + branch gen + `runner`
   for `claude -p` + create PR via `bin/bb` + Linear state sync. End-to-end for a
   simple issue → open PR.
3. **Review.** `runner` for `codex exec` (`MODEL_REVIEW`) + `review_parser` +
   Linear `Needs Fixes` / `Human Review` transitions + posting the result to the PR.
4. **Fix loop.** Original-author fix from unresolved comments + re-review.
5. **Hardening.** All error paths, `runs/*.jsonl` history view on the page, target
   repo `AGENTS.md` / `CLAUDE.md`, README + setup/deploy/troubleshoot/override docs.

---

## 9. Tests

- Linear payload/field validation → routing decision
- branch-name generation
- PR meta-block parse/format (round-trip)
- review-result parser, incl. **parse-fail → require_human_review**
- PR-comment event → original-author routing
- idempotency (already-handled state is a no-op)
- `DRY_RUN` (no external mutations)
- `linear_client` / `bitbucket_client` mocked at the boundary

---

## 10. Environment

```env
# Linear
LINEAR_API_KEY=
LINEAR_TEAM_ID=
# (status IDs discovered via API, or pinned as LINEAR_WORKFLOW_* if preferred)

# Bitbucket (reuse bin/bb; App Password, Pull requests: Write)
BITBUCKET_WORKSPACE=
BITBUCKET_REPO=
BITBUCKET_USERNAME=
BITBUCKET_APP_PASSWORD=      # or via ~/.config/bridge-ai/bitbucket.env

# Agent models (never hardcoded)
MODEL_SIMPLE_IMPLEMENTATION=codex-5.4
MODEL_COMPLEX_IMPLEMENTATION=opus-4.8
MODEL_REVIEW=codex-5.5

# Runtime
TARGET_REPO_PATH=/path/to/checkout   # where claude/codex run
LOG_LEVEL=info
DRY_RUN=false
```

---

## 11. Non-goals (MVP) & future

**Non-goals:** n8n · webhooks/tunnel · auto-merge · persistent DB · long-term
agent memory · multi-agent debate · Slack · full dashboard (the status page is the
only UI).

**Future extension points:** Slack/email notifications · metrics & cost tracking ·
agent success rate · automatic issue splitting · multiple Bitbucket workspaces ·
optional auto-merge after human approval · switch the manual trigger for webhooks
if you later host it.

---

## 12. Open questions to resolve in the new repo

- Exact Linear workflow **state IDs** and whether `size`/`risk`/`agent` are
  **labels** vs **custom fields** in your team (affects `linear_client` reads).
- How `bin/bb` (currently in `bridge-ai-poc`) is shared — copy it in, or make the
  Bitbucket client self-contained in this repo.
- Prompt templates for implement / review / fix (what context to inject from the
  Linear issue + repo instruction files).
- Whether the status page auto-advances (create → auto-review) or every step is a
  manual click.
