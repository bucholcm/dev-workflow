# Linear issue template

Paste this into a Linear issue description; set the matching labels
(`size:*`, `risk:*`, optional `agent:*`, `repo:workspace/repo`).

```markdown
## Problem

...

## Desired behavior

...

## Acceptance criteria

- [ ] ...
- [ ] ...

## Technical notes

...
```

## Labels to set

| Label | Values | Meaning |
|---|---|---|
| `size:` | `xs \| s \| m \| l \| xl` | `xs/s` → simple model, `m/l/xl` → complex model |
| `risk:` | `low \| medium \| high` | `high` → human gate before any AI run |
| `agent:` | `<model>` or `human` | optional override of the size-based routing |
| `repo:` | `workspace/repo` | target Bitbucket repository |

Move the issue to **Ready for AI** to make it actionable on the status page.
