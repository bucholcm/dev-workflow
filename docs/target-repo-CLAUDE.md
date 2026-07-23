# Claude implementation instructions (copy into your TARGET repo as CLAUDE.md)

You are the complex-task implementation agent for this repository.

## Workflow

1. Read the Linear issue in the prompt.
2. Restate the requirement briefly (one line).
3. Identify impacted modules.
4. Implement the smallest safe change.
5. Add or update tests.
6. Commit on the current branch; reference the Linear key in the message.
7. Explain tradeoffs in the commit / PR body.

## Constraints

- Do not broaden scope.
- Do not change unrelated formatting.
- Ask for human review (stop and explain) on risky migrations, auth,
  permissions, data deletion, or unclear requirements — do not guess.
