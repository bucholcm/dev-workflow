from dev_orchestrator.linear_client import _parse_labels, _to_issue
from dev_orchestrator.models import Risk, Size


def test_parse_labels():
    out = _parse_labels(["size:s", "risk:high", "agent:opus-4.8", "repo:ws/r", "other"])
    assert out["size"] == Size.S
    assert out["risk"] == Risk.HIGH
    assert out["agent_hint"] == "opus-4.8"
    assert out["repo"] == "ws/r"


def test_bad_label_values_ignored():
    out = _parse_labels(["size:huge", "risk:spicy"])
    assert out["size"] is None and out["risk"] is None


def test_to_issue_projects_node():
    node = {
        "id": "u", "identifier": "DEV-7", "title": "t", "description": "d",
        "url": "http://x", "state": {"name": "Ready for AI"},
        "labels": {"nodes": [{"name": "size:m"}, {"name": "risk:low"}]},
    }
    issue = _to_issue(node)
    assert issue.identifier == "DEV-7"
    assert issue.size == Size.M and issue.risk == Risk.LOW
    assert issue.state_name == "Ready for AI"
