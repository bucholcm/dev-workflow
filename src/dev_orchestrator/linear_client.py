"""Linear client (GraphQL). The only thing that reads/writes Linear planning state.

Schema assumption (design §12, decided): size / risk / agent are **labels**
formatted `key:value` (e.g. `size:s`, `risk:low`, `agent:opus-4.8`, `repo:ws/repo`).
Swap `_parse_labels` if your team uses custom fields instead.
"""

from __future__ import annotations

import logging

import httpx

from .models import LinearIssue, Risk, Size

logger = logging.getLogger(__name__)

LINEAR_API = "https://api.linear.app/graphql"

_ISSUES_QUERY = """
query Issues($filter: IssueFilter) {
  issues(filter: $filter, first: 50) {
    nodes {
      id identifier title description url
      state { name }
      labels { nodes { name } }
    }
  }
}
"""

_UPDATE_STATE = """
mutation Update($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) { success }
}
"""

_COMMENT = """
mutation Comment($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) { success }
}
"""

_ISSUE_QUERY = """
query Issue($id: String!) {
  issue(id: $id) {
    id identifier title description url
    state { name }
    labels { nodes { name } }
  }
}
"""

_STATES_QUERY = """
query States($teamId: ID!) {
  workflowStates(filter: { team: { id: { eq: $teamId } } }, first: 100) {
    nodes { id name }
  }
}
"""


def _parse_labels(labels: list[str]) -> dict:
    """Extract size / risk / agent / repo / branch / pr_url from `key:value` labels."""
    out: dict = {"size": None, "risk": None, "agent_hint": None, "repo": None, "branch": None, "pr_url": None}
    for raw in labels:
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "size":
            try:
                out["size"] = Size(val.lower())
            except ValueError:
                pass
        elif key == "risk":
            try:
                out["risk"] = Risk(val.lower())
            except ValueError:
                pass
        elif key == "agent":
            out["agent_hint"] = val
        elif key in ("repo", "branch", "pr_url"):
            out[key] = val
    return out


def _to_issue(node: dict) -> LinearIssue:
    labels = [n["name"] for n in node.get("labels", {}).get("nodes", [])]
    parsed = _parse_labels(labels)
    return LinearIssue(
        id=node["id"],
        identifier=node["identifier"],
        title=node.get("title", ""),
        description=node.get("description") or "",
        state_name=(node.get("state") or {}).get("name", ""),
        url=node.get("url", ""),
        labels=labels,
        **parsed,
    )


class LinearClient:
    def __init__(self, api_key: str, team_id: str = ""):
        self.team_id = team_id
        self.client = httpx.Client(
            base_url=LINEAR_API,
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=30,
        )

    def _gql(self, query: str, variables: dict) -> dict:
        resp = self.client.post("", json={"query": query, "variables": variables})
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"Linear GraphQL error: {data['errors']}")
        return data["data"]

    def list_issues_in_states(self, state_names: list[str]) -> list[LinearIssue]:
        """Fetch issues whose workflow state name is in `state_names` (for this team)."""
        filt: dict = {"state": {"name": {"in": state_names}}}
        if self.team_id:
            filt["team"] = {"id": {"eq": self.team_id}}
        nodes = self._gql(_ISSUES_QUERY, {"filter": filt})["issues"]["nodes"]
        return [_to_issue(n) for n in nodes]

    def get_issue(self, key: str) -> LinearIssue | None:
        """Fetch a single issue by identifier (e.g. 'BRI-61') or UUID — even outside actionable states."""
        node = self._gql(_ISSUE_QUERY, {"id": key}).get("issue")
        return _to_issue(node) if node else None

    def workspace_url_key(self) -> str:
        """Org slug used in issue URLs, e.g. 'bridge-soi' → https://linear.app/bridge-soi/issue/BRI-1."""
        return self._gql("{ organization { urlKey } }", {})["organization"]["urlKey"]

    def get_state_ids(self) -> dict[str, str]:
        """Map workflow-state name → id for this team (used to set state)."""
        if not self.team_id:
            raise RuntimeError("LINEAR_TEAM_ID required to resolve workflow state IDs")
        nodes = self._gql(_STATES_QUERY, {"teamId": self.team_id})["workflowStates"]["nodes"]
        return {n["name"]: n["id"] for n in nodes}

    def set_state(self, issue_id: str, state_id: str) -> bool:
        return self._gql(_UPDATE_STATE, {"id": issue_id, "stateId": state_id})["issueUpdate"]["success"]

    def comment(self, issue_id: str, body: str) -> bool:
        return self._gql(_COMMENT, {"issueId": issue_id, "body": body})["commentCreate"]["success"]

    def close(self) -> None:
        self.client.close()
