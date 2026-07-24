from dev_orchestrator.pr_meta import PRMeta, build_pr_description, parse_meta


def test_meta_roundtrip():
    meta = PRMeta(linear_issue="DEV-123", linear_issue_id="uuid", ai_author="codex-5.4",
                  ai_reviewer="codex-5.5", ai_complexity="s", ai_risk="low")
    parsed = parse_meta(meta.to_block())
    assert parsed == meta


def test_parse_meta_from_full_description():
    body = build_pr_description(
        meta=PRMeta(linear_issue="DEV-9", ai_author="opus-4.8", ai_reviewer="codex-5.5"),
        summary="Add feature",
        acceptance_criteria=["does X", "does Y"],
        test_plan=["unit tests"],
    )
    parsed = parse_meta(body)
    assert parsed.linear_issue == "DEV-9"
    assert parsed.ai_author == "opus-4.8"
    assert "## Acceptance criteria" in body
    assert "- [ ] does X" in body


def test_parse_meta_absent_returns_none():
    assert parse_meta("just a normal PR body, no meta") is None


def test_meta_roundtrips_session_id_and_cli():
    meta = PRMeta(linear_issue="DEV-7", ai_author="opus-4.8", ai_reviewer="codex-5.5",
                  ai_session_id="9dd4b6bc-27b1-4621-8990-fa2831388323", ai_cli="claude")
    parsed = parse_meta(meta.to_block())
    assert parsed.ai_session_id == "9dd4b6bc-27b1-4621-8990-fa2831388323"
    assert parsed.ai_cli == "claude"
    assert parsed == meta
