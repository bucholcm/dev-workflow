# Agent instructions (copy into your TARGET repo as AGENTS.md)

## Scope

Work only on the Linear issue referenced in the prompt or the PR description.

## Implementation rules

- Keep changes minimal and scoped to the issue.
- Do not introduce dependencies without clear justification.
- Do not reformat unrelated files.
- Follow existing project patterns.
- Add or update tests when behavior changes.
- Update documentation when public behavior changes.
- Commit on the current branch with a message referencing the Linear key.

## Review rules

- Treat correctness, regressions, missing tests, security, data loss, and
  breaking API changes as **blocking** (severity `critical`).
- Treat auth, permissions, billing, migrations, and destructive data operations
  as **high risk** → set `decision: require_human_review`.
- Prefer actionable findings tied to a specific file, line, test, or acceptance
  criterion.
- Emit ONLY the JSON review contract requested in the prompt — no prose outside
  the fenced ```json block.
