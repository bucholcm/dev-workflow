"""Parse the implement/fix/answer agent's escalation block (prompts._ESCALATION_CONTRACT).

The agent ends its output with a fenced json block declaring either
`{"status": "done"}` or `{"status": "needs_input", "questions": [...]}`. We reuse
the robust LLM-JSON extractor from review_parser. Fail-safe rule: anything we
cannot confidently read as `needs_input` is treated as `done`, so a garbled or
missing block never blocks work spuriously (the has-commits check still guards
the real completion path).
"""

from __future__ import annotations

import logging

from .review_parser import extract_json

logger = logging.getLogger(__name__)


def parse_escalation(raw_output: str) -> dict:
    """Return {"status": "done"|"needs_input", "questions": [...], "context": str}.

    Only an explicit, parseable needs_input block yields needs_input; everything
    else (done, missing block, parse error) yields done.
    """
    try:
        data = extract_json(raw_output or "")
    except Exception as e:  # noqa: BLE001 — never let parsing block the pipeline
        logger.debug("no escalation block found (%s) → done", e)
        return {"status": "done", "questions": [], "context": ""}
    if not isinstance(data, dict):
        return {"status": "done", "questions": [], "context": ""}

    status = str(data.get("status", "")).strip().lower()
    if status == "needs_input":
        raw_qs = data.get("questions") or []
        questions = [str(q).strip() for q in raw_qs if str(q).strip()] if isinstance(raw_qs, list) else []
        return {"status": "needs_input", "questions": questions, "context": str(data.get("context", ""))}
    return {"status": "done", "questions": [], "context": ""}
