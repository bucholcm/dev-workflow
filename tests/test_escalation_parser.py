"""The escalation contract: only an explicit needs_input block blocks; else done."""

from dev_orchestrator.escalation_parser import parse_escalation


def test_done_block():
    out = parse_escalation('Implemented it.\n\n```json\n{"status": "done"}\n```')
    assert out["status"] == "done"


def test_needs_input_block_extracts_questions():
    raw = (
        "I can't proceed safely.\n\n"
        '```json\n{"status": "needs_input", '
        '"questions": ["Which table owns the FK?", "Soft or hard delete?"], '
        '"context": "migration is destructive"}\n```'
    )
    out = parse_escalation(raw)
    assert out["status"] == "needs_input"
    assert out["questions"] == ["Which table owns the FK?", "Soft or hard delete?"]
    assert "destructive" in out["context"]


def test_missing_block_is_done():
    assert parse_escalation("just prose, no json at all")["status"] == "done"


def test_garbled_json_is_done():
    # Unparseable / not our schema → fail safe to done (never block spuriously).
    assert parse_escalation('```json\n{status: needs_input,,,}\n```')["status"] == "done"


def test_needs_input_without_questions_still_flagged():
    out = parse_escalation('```json\n{"status": "needs_input"}\n```')
    assert out["status"] == "needs_input"
    assert out["questions"] == []


def test_empty_input_is_done():
    assert parse_escalation("")["status"] == "done"
