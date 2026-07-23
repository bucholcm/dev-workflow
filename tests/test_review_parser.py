from dev_orchestrator.models import Decision, Severity
from dev_orchestrator.review_parser import extract_json, parse_review_output

FENCED = """
Here is my review.

```json
{
  "overview": "ok",
  "findings": [
    {"severity": "critical", "category": "Correctness", "title": "npe", "file": "a.py", "line": 3, "issue": "boom"}
  ],
  "acceptance_criteria": ["[x] one"],
  "tests_passed": true,
  "summary": "one bug",
  "decision": "request_changes"
}
```
"""


def test_extract_fenced_json():
    data = extract_json(FENCED)
    assert data["decision"] == "request_changes"


def test_raw_json_fallback():
    raw = 'blah blah {"overview":"x","findings":[],"summary":"s","decision":"pass"} trailing'
    data = extract_json(raw)
    assert data["decision"] == "pass"


def test_trailing_comma_repaired():
    raw = '```json\n{"findings": [], "decision": "pass",}\n```'
    assert extract_json(raw)["decision"] == "pass"


def test_parse_output_maps_decision_and_findings():
    r = parse_review_output(FENCED)
    assert r.decision == Decision.REQUEST_CHANGES
    assert len(r.blocking_findings) == 1
    assert r.blocking_findings[0].severity == Severity.CRITICAL


def test_pass_with_critical_finding_is_downgraded():
    raw = '```json\n{"findings":[{"severity":"critical","title":"x","file":"f","line":1,"issue":"i","category":"C"}],"decision":"pass","summary":"","overview":""}\n```'
    assert parse_review_output(raw).decision == Decision.REQUEST_CHANGES


def test_unparseable_is_human_review():
    r = parse_review_output("the model forgot to emit json")
    assert r.decision == Decision.REQUIRE_HUMAN_REVIEW
