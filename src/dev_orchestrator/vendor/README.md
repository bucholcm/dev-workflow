# Vendored / adapted code

Portions of this project are adapted from **[simion/reviewd](https://github.com/simion/reviewd)**
(MIT License), specifically:

- `bitbucket_client.py` — the `httpx`-based Bitbucket Cloud client (auth handling,
  429 retry/backoff, pagination, comment/task endpoints) is adapted from
  `reviewd/src/reviewd/providers/bitbucket.py`.
- `review_parser.py` — the robust LLM-JSON extraction (`extract_json`,
  `_find_last_json_object`, trailing-comma repair) is adapted from
  `reviewd/src/reviewd/reviewer.py`.
- `runner.py` — the subprocess/process-group handling and per-run git-worktree
  isolation pattern are adapted from `reviewd/src/reviewd/reviewer.py`.
- `models.py` — the `Finding` / `Severity` / `ReviewResult` dataclass shapes.

reviewd is MIT-licensed; the original copyright and license are reproduced in
`LICENSE-reviewd` in this directory. Adaptations: retargeted to this project's
Linear-driven, manual-trigger orchestration model and its JSON review contract.
