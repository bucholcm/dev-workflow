# AI Development Workflow Orchestrator — Claude Code Implementation Brief

## Context

I want to automate an AI-assisted development workflow for my project.

Current desired workflow:

1. I specify or refine requirements with Claude Code.
2. Requirements are converted into structured Linear issues.
3. Linear remains the planning source of truth.
4. Bitbucket remains the mandatory SCM and PR system.
5. Simple implementation tasks are routed to Codex 5.4.
6. Complex implementation tasks are routed to Claude / Opus 4.8.
7. Codex 5.5 performs delivery review / PR review.
8. The original PR authoring agent fixes PR comments.
9. A human remains the final approval gate for merge and high-risk changes.

Important constraints:

- I must use Bitbucket, not GitHub.
- Linear is acceptable and should be the planning source of truth.
- I do not want to run n8n.
- The solution should be lightweight, preferably serverless or a small service.
- Avoid unnecessary custom UI or persistent database in the MVP.
- Keep the design practical and implementable.

## Goal

Build a small orchestration layer that connects:

- Linear
- Bitbucket Cloud
- Bitbucket Pipelines / Agentic Pipelines
- Claude Code / Opus
- Codex

The orchestrator should remove manual tool switching by reacting to Linear and Bitbucket events, routing tasks to the right agent, triggering Bitbucket pipelines, and syncing state back to Linear.

## Assumptions

Assume Bitbucket Cloud unless the repository indicates otherwise.

If Bitbucket Agentic Pipelines are available in this workspace, use them as the preferred execution mechanism.

If Agentic Pipelines are not available, implement the same behavior using standard Bitbucket custom pipelines that invoke agent CLIs or scripts non-interactively.

Do not hardcode model names. Use environment variables instead:

```env
MODEL_SIMPLE_IMPLEMENTATION=codex-5.4
MODEL_COMPLEX_IMPLEMENTATION=opus-4.8
MODEL_REVIEW=codex-5.5
```

## Target architecture

```text
Claude Code
  ↓
Requirement / PRD / acceptance criteria
  ↓
Linear issue / epic
  ↓ Linear webhook
AI workflow orchestrator
  ↓ triggers Bitbucket custom pipeline with variables
Bitbucket Pipelines / Agentic Pipelines
  ├─ simple task → Codex 5.4
  ├─ complex task → Claude / Opus 4.8
  └─ PR review → Codex 5.5
  ↓
Bitbucket Pull Request
  ↓ PR webhooks / comments / tasks
Original PR authoring agent fixes comments
  ↓
Codex 5.5 re-review
  ↓
Human approval / merge
```

## Source-of-truth model

### Linear owns planning state

Linear should own:

- requirement
- issue title and description
- acceptance criteria
- priority
- size / complexity
- risk
- target repository
- agent assignment
- workflow state
- PR URL

### Bitbucket owns code state

Bitbucket should own:

- branch
- commits
- pull request
- PR comments
- PR tasks
- CI status
- merge state

### Orchestrator owns only routing and event choreography

The orchestrator should not become a workflow database.

For MVP, store durable state in:

- Linear custom fields / labels
- Bitbucket PR description metadata
- Bitbucket branch naming convention

## Linear workflow

Create or support these Linear statuses:

```text
Triage
Ready for Specification
Specified
Ready for AI
AI In Progress
PR Open
AI Review
Needs Fixes
Human Review
Ready to Merge
Done
```

Suggested Linear labels or custom fields:

```text
size: xs | s | m | l | xl
risk: low | medium | high
agent: codex-5.4 | opus-4.8 | human
reviewer: codex-5.5 | human
repo: workspace/repo
branch: ai/DEV-123-short-slug
pr_url: https://bitbucket.org/...
```

## Routing rules

Implement deterministic routing first.

```text
If risk == high:
  route to human or require human approval before AI execution

If size in [xs, s]:
  implementation_agent = MODEL_SIMPLE_IMPLEMENTATION

If size in [m, l, xl]:
  implementation_agent = MODEL_COMPLEX_IMPLEMENTATION

Review agent:
  MODEL_REVIEW

Fix agent:
  same agent that authored the PR
```

More specific routing:

| Issue type | Agent |
|---|---|
| Small bug | Codex 5.4 |
| Simple UI tweak | Codex 5.4 |
| Test addition | Codex 5.4 |
| Isolated refactor | Codex 5.4 |
| Multi-file feature | Claude / Opus 4.8 |
| Architecture-sensitive change | Claude / Opus 4.8 |
| Data model change | Claude / Opus 4.8 |
| Auth / permissions / security | Human gate + AI support |
| Migration / destructive data change | Human gate + AI support |
| PR review / delivery check | Codex 5.5 |

## Required orchestrator endpoints

Implement a small service with these endpoints:

```text
POST /webhooks/linear
POST /webhooks/bitbucket
POST /dispatch/issue
POST /dispatch/pr-review
POST /health
```

Optional:

```text
POST /sync/linear
POST /sync/bitbucket
```

## Suggested implementation stack

Choose the stack that best fits the existing project.

If there is no existing backend stack, prefer one of:

### Option A — TypeScript serverless

- TypeScript
- Hono or Fastify
- Cloudflare Workers / Vercel / AWS Lambda / Azure Functions
- zod for payload validation

### Option B — Python service

- Python
- FastAPI
- Pydantic
- deployable as container or serverless function

Use whichever is most consistent with the repository.

## Required environment variables

```env
# Linear
LINEAR_API_KEY=
LINEAR_WEBHOOK_SECRET=
LINEAR_TEAM_ID=
LINEAR_WORKFLOW_READY_FOR_AI=
LINEAR_WORKFLOW_AI_IN_PROGRESS=
LINEAR_WORKFLOW_PR_OPEN=
LINEAR_WORKFLOW_AI_REVIEW=
LINEAR_WORKFLOW_NEEDS_FIXES=
LINEAR_WORKFLOW_HUMAN_REVIEW=
LINEAR_WORKFLOW_READY_TO_MERGE=
LINEAR_WORKFLOW_DONE=

# Bitbucket
BITBUCKET_WORKSPACE=
BITBUCKET_USERNAME=
BITBUCKET_APP_PASSWORD=
BITBUCKET_WEBHOOK_SECRET=

# Agent models
MODEL_SIMPLE_IMPLEMENTATION=codex-5.4
MODEL_COMPLEX_IMPLEMENTATION=opus-4.8
MODEL_REVIEW=codex-5.5

# Pipeline names
PIPELINE_IMPLEMENT_SIMPLE=ai-implement-simple
PIPELINE_IMPLEMENT_COMPLEX=ai-implement-complex
PIPELINE_REVIEW=ai-review
PIPELINE_FIX_COMMENTS=ai-fix-pr-comments

# Runtime
LOG_LEVEL=info
DRY_RUN=false
```

## Security requirements

Do not log secrets.

Validate webhook signatures where supported.

Reject unsigned or invalid webhook requests.

Make all write operations idempotent.

Use least-privilege credentials:

- Linear token should only access required workspace/team data.
- Bitbucket app password / OAuth token should only have required repo and PR permissions.
- Agent provider credentials should be scoped to pipeline environment.

Add `DRY_RUN=true` support so the workflow can be tested without creating branches, PRs, or state transitions.

## Bitbucket branch and PR conventions

Branch name:

```text
ai/<LINEAR_KEY>-<short-slug>
```

Example:

```text
ai/DEV-123-payment-validation
```

Every AI-created PR description must include machine-readable metadata:

```markdown
<!-- ai-workflow
linear_issue: DEV-123
linear_issue_id: <linear-id>
ai_author: codex-5.4
ai_reviewer: codex-5.5
ai_complexity: s
ai_risk: low
source: ai-workflow-orchestrator
-->
```

Also include human-readable summary:

```markdown
## Summary

...

## Linear issue

DEV-123

## Acceptance criteria

- [ ] ...
- [ ] ...

## Test plan

- [ ] ...

## AI metadata

Authoring agent: codex-5.4  
Review agent: codex-5.5  
Risk: low  
Complexity: s
```

## Bitbucket custom pipelines

Add or update `bitbucket-pipelines.yml` with custom pipelines.

Use this as a conceptual skeleton and adapt it to the project:

```yaml
pipelines:
  custom:
    ai-implement-simple:
      - variables:
          - name: LINEAR_ISSUE_KEY
          - name: LINEAR_ISSUE_ID
          - name: TARGET_BRANCH
          - name: SOURCE_BRANCH
          - name: AI_AGENT
      - step:
          name: Implement simple issue with AI
          script:
            - ./scripts/ai/implement-simple.sh

    ai-implement-complex:
      - variables:
          - name: LINEAR_ISSUE_KEY
          - name: LINEAR_ISSUE_ID
          - name: TARGET_BRANCH
          - name: SOURCE_BRANCH
          - name: AI_AGENT
      - step:
          name: Implement complex issue with AI
          script:
            - ./scripts/ai/implement-complex.sh

    ai-review:
      - variables:
          - name: LINEAR_ISSUE_KEY
          - name: PULL_REQUEST_ID
          - name: AI_REVIEWER
      - step:
          name: Review PR with AI
          script:
            - ./scripts/ai/review-pr.sh

    ai-fix-pr-comments:
      - variables:
          - name: LINEAR_ISSUE_KEY
          - name: PULL_REQUEST_ID
          - name: AI_AUTHOR
          - name: COMMENT_CONTEXT
      - step:
          name: Fix PR comments with original authoring agent
          script:
            - ./scripts/ai/fix-pr-comments.sh
```

If Agentic Pipelines are supported in this repository, replace the script steps with the appropriate Bitbucket Agentic Pipeline agent steps.

## Required scripts

Create these scripts if the project does not already have equivalents:

```text
scripts/ai/implement-simple.sh
scripts/ai/implement-complex.sh
scripts/ai/review-pr.sh
scripts/ai/fix-pr-comments.sh
scripts/ai/shared/fetch-linear-issue.sh
scripts/ai/shared/update-linear-issue.sh
scripts/ai/shared/create-bitbucket-pr.sh
scripts/ai/shared/post-bitbucket-pr-comment.sh
```

The scripts should be thin wrappers. Business logic should live in reusable code where possible.

## Orchestrator behavior

### 1. Linear webhook

When a Linear issue is updated:

```pseudo
if issue.status == "Ready for AI":
  read issue fields
  validate repo exists
  determine size and risk
  choose implementation agent
  compute branch name
  update Linear:
    agent = selected agent
    reviewer = MODEL_REVIEW
    branch = computed branch
    status = "AI In Progress"
  trigger Bitbucket custom pipeline:
    selector = ai-implement-simple OR ai-implement-complex
    variables:
      LINEAR_ISSUE_KEY
      LINEAR_ISSUE_ID
      SOURCE_BRANCH
      TARGET_BRANCH
      AI_AGENT
```

### 2. Bitbucket PR created

When Bitbucket emits a PR-created event:

```pseudo
extract PR metadata
find Linear issue
update Linear:
  pr_url = PR URL
  status = "PR Open"
trigger ai-review pipeline:
  LINEAR_ISSUE_KEY
  PULL_REQUEST_ID
  AI_REVIEWER = MODEL_REVIEW
```

### 3. Bitbucket PR updated

When the source branch changes:

```pseudo
if PR is AI-authored:
  trigger ai-review pipeline
  update Linear status = "AI Review"
```

Add a debounce or idempotency guard to avoid infinite review loops.

### 4. PR comments / requested changes

When a PR comment or PR task indicates requested changes:

```pseudo
read PR description metadata
authoring_agent = ai_author

if authoring_agent is empty:
  do not auto-fix
  add comment requiring manual triage

if comment/task is from review agent or human reviewer:
  update Linear status = "Needs Fixes"
  trigger ai-fix-pr-comments pipeline:
    PULL_REQUEST_ID
    LINEAR_ISSUE_KEY
    AI_AUTHOR = authoring_agent
    COMMENT_CONTEXT = summarized unresolved comments/tasks
```

### 5. Fix completed

When the fix pipeline pushes commits:

```pseudo
update Linear status = "AI Review"
trigger ai-review pipeline again
```

### 6. Review passes

When Codex 5.5 review reports no blocking findings:

```pseudo
post Bitbucket PR comment:
  AI review passed
update Linear status = "Human Review"
```

Do not auto-merge in MVP.

## AI review output format

The review agent should always produce this structure:

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

The review script should parse the final decision.

If parsing fails, treat it as:

```text
require_human_review
```

## Fix comments behavior

The fix agent must:

```text
1. Read the PR diff.
2. Read unresolved PR comments and tasks.
3. Read the Linear issue and acceptance criteria.
4. Make the smallest possible fix.
5. Avoid unrelated cleanup.
6. Push to the same PR branch.
7. Add a PR comment summarizing what was fixed.
```

Important rule:

```text
The original PR authoring agent fixes comments.
Codex 5.5 reviews but does not normally fix its own review findings.
```

Exception:

```text
If the original authoring agent is unavailable or misconfigured, move Linear issue to Human Review and add a Bitbucket PR comment explaining the failure.
```

## Repository instruction files

Create or update `AGENTS.md`:

```markdown
# Agent instructions

## Scope

Work only on the Linear issue referenced in pipeline variables or the PR description.

## Implementation rules

- Keep changes minimal and scoped.
- Do not introduce dependencies without clear justification.
- Do not reformat unrelated files.
- Follow existing project patterns.
- Add or update tests when behavior changes.
- Update documentation when public behavior changes.

## Review rules

- Treat correctness, regressions, missing tests, security, data loss, and breaking API changes as blocking.
- Treat auth, permissions, billing, migrations, and destructive data operations as high risk.
- Prefer actionable comments tied to specific files, tests, or acceptance criteria.
```

Create or update `CLAUDE.md`:

```markdown
# Claude implementation instructions

You are the complex-task implementation agent for this repository.

## Workflow

1. Read the Linear issue.
2. Restate the requirement briefly.
3. Identify impacted modules.
4. Implement the smallest safe change.
5. Add or update tests.
6. Open or update the Bitbucket PR.
7. Explain tradeoffs in the PR description.

## Constraints

- Do not broaden scope.
- Do not change unrelated formatting.
- Ask for human review on risky migrations, auth, permissions, data deletion, or unclear requirements.
```

## Linear issue template

Create or support this issue template:

```markdown
## Problem

...

## Desired behavior

...

## Acceptance criteria

- [ ] ...

## Technical notes

...

## Risk

low | medium | high

## Size

xs | s | m | l | xl

## Target repo

workspace/repo

## Suggested agent

codex-5.4 | opus-4.8 | human
```

## Idempotency requirements

Use idempotency keys for events:

```text
linear:<event-id>
bitbucket:<event-key>:<repo>:<pr-id>:<updated-on>
pipeline:<issue-key>:<pipeline-name>:<branch>:<sha>
```

For MVP, idempotency may be implemented by checking existing Linear state and PR metadata instead of a database.

If a database/cache is already present in the project, use it.

If not, avoid adding one unless required.

## Error handling

If Linear issue is missing required fields:

```text
- do not trigger AI implementation
- comment/update Linear issue with missing fields
- keep or move issue to Ready for Specification
```

If Bitbucket pipeline trigger fails:

```text
- update Linear issue with error note
- move issue to Needs Fixes or Human Review
```

If AI implementation fails:

```text
- post pipeline failure summary
- move Linear issue to Human Review
```

If AI review cannot parse decision:

```text
- require human review
```

## Logging

Log structured events:

```json
{
  "event": "linear.issue.ready_for_ai",
  "issueKey": "DEV-123",
  "repo": "workspace/repo",
  "agent": "codex-5.4",
  "risk": "low",
  "size": "s"
}
```

Do not log:

- API keys
- OAuth tokens
- full secrets
- sensitive source code beyond normal CI logs

## Tests to implement

Add tests for:

```text
- Linear webhook validation
- Bitbucket webhook validation
- Linear issue → routing decision
- branch name generation
- PR metadata parsing
- PR comment event → original authoring agent routing
- review result parser
- idempotency behavior
- dry-run behavior
```

## MVP acceptance criteria

The implementation is complete when:

```text
1. Moving a Linear issue to Ready for AI triggers the correct Bitbucket pipeline.
2. Simple issues route to MODEL_SIMPLE_IMPLEMENTATION.
3. Complex issues route to MODEL_COMPLEX_IMPLEMENTATION.
4. High-risk issues require human review or explicit override.
5. AI-created PRs include Linear issue metadata.
6. Creating/updating a PR triggers MODEL_REVIEW.
7. Review results are posted back to the Bitbucket PR.
8. Blocking review comments move the Linear issue to Needs Fixes.
9. Fixes are routed back to the original PR authoring agent.
10. A passing review moves the Linear issue to Human Review.
11. No automatic merge happens in MVP.
12. DRY_RUN mode logs intended actions without changing Linear or Bitbucket.
```

## Implementation order

Use this order:

```text
1. Inspect repository structure and existing stack.
2. Choose minimal implementation stack.
3. Add config and environment schema.
4. Implement Linear client.
5. Implement Bitbucket client.
6. Implement routing logic.
7. Implement webhook handlers.
8. Implement Bitbucket pipeline trigger.
9. Add PR metadata parser.
10. Add review result parser.
11. Add scripts under scripts/ai.
12. Add AGENTS.md and CLAUDE.md.
13. Add tests.
14. Add README documentation.
15. Provide setup instructions.
```

## Deliverables

Produce:

```text
- orchestrator source code
- Bitbucket pipeline definitions
- scripts/ai/* wrappers
- AGENTS.md
- CLAUDE.md
- Linear issue template
- README with setup and deployment instructions
- tests
```

## README requirements

The README should explain:

```text
- how the workflow works
- required Linear setup
- required Bitbucket setup
- required environment variables
- how to run locally
- how to deploy
- how to test with DRY_RUN
- how to troubleshoot failed pipeline triggers
- how to manually override agent routing
```

## Non-goals for MVP

Do not implement:

```text
- n8n
- custom dashboard
- automatic merge
- persistent long-term agent memory
- multi-agent debate
- Slack notifications
- full UI
```

## Future enhancements

Leave extension points for:

```text
- Slack / email notifications
- dashboard
- metrics
- cost tracking
- agent success rate
- automatic issue splitting
- support for multiple Bitbucket workspaces
- optional auto-merge after human approval
```

## Final instruction

Implement the smallest reliable version first.

Prioritize:

```text
1. deterministic routing
2. safe webhook handling
3. correct Bitbucket pipeline triggering
4. clean Linear state synchronization
5. original-author fix loop
6. clear failure modes
```

Do not over-engineer the workflow database or UI.
