"""Bitbucket Cloud client (httpx). Adapted from simion/reviewd (MIT) — see
vendor/README.md. Retargeted with PR-create + get-description used by the
orchestrator, and DRY_RUN awareness delegated to the caller (orchestrator).

Auth: `email:token` → HTTP Basic (App Password, "Pull requests: Write"), or a
plain token → Bearer. Same credential the bridge-ai-poc `bin/bb` uses.
"""

from __future__ import annotations

import logging
import re
import time

import httpx

from .models import PRInfo

logger = logging.getLogger(__name__)

BB_API_BASE = "https://api.bitbucket.org/2.0"
BOT_MARKER = "[](dev-orchestrator)"
_BASIC_AUTH_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+:.+$")


class BitbucketClient:
    def __init__(self, workspace: str, repo: str, auth_token: str):
        self.workspace = workspace
        self.repo = repo
        if _BASIC_AUTH_RE.match(auth_token):
            email, token = auth_token.split(":", 1)
            self._auth: tuple[str, str] | None = (email, token)
            headers = {"Content-Type": "application/json"}
        else:
            self._auth = None
            headers = {"Authorization": f"Bearer {auth_token}", "Content-Type": "application/json"}
        self.client = httpx.Client(base_url=BB_API_BASE, auth=self._auth, headers=headers, timeout=30)

    # ── low-level ──
    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        max_retries = 3
        resp = None
        for attempt in range(max_retries + 1):
            resp = self.client.request(method, url, **kwargs)
            if resp.status_code != 429 or attempt == max_retries:
                resp.raise_for_status()
                return resp
            retry_after = int(resp.headers.get("Retry-After", 2**attempt))
            logger.warning("Bitbucket 429, retrying in %ds (%d/%d)", retry_after, attempt + 1, max_retries)
            time.sleep(retry_after)
        return resp  # unreachable

    def _paginate(self, url: str, params: dict | None = None) -> list[dict]:
        results: list[dict] = []
        seen: set[int] = set()
        params = params or {}
        while True:
            data = self._request("GET", url, params=params).json()
            values = [v for v in data.get("values", []) if v.get("id") not in seen]
            if not values:
                break
            for v in values:
                if "id" in v:
                    seen.add(v["id"])
            results.extend(values)
            nxt = data.get("next")
            if not nxt:
                break
            url, params = nxt, {}
        return results

    def _base(self) -> str:
        return f"/repositories/{self.workspace}/{self.repo}"

    def _pr_from_data(self, data: dict) -> PRInfo:
        return PRInfo(
            repo_slug=self.repo,
            pr_id=data["id"],
            title=data["title"],
            author=data.get("author", {}).get("display_name", ""),
            source_branch=data["source"]["branch"]["name"],
            destination_branch=data["destination"]["branch"]["name"],
            source_commit=data["source"]["commit"]["hash"] if data["source"].get("commit") else "",
            url=data["links"]["html"]["href"],
            draft=data.get("draft", False),
        )

    # ── PR ops ──
    def list_open_prs(self) -> list[PRInfo]:
        items = self._paginate(f"{self._base()}/pullrequests", {"state": "OPEN"})
        return [self._pr_from_data(i) for i in items]

    def get_pr(self, pr_id: int) -> PRInfo:
        return self._pr_from_data(self._request("GET", f"{self._base()}/pullrequests/{pr_id}").json())

    def get_pr_description(self, pr_id: int) -> str:
        data = self._request("GET", f"{self._base()}/pullrequests/{pr_id}").json()
        return data.get("description", "") or ""

    def create_pr(self, *, title: str, source_branch: str, dest_branch: str, description: str) -> PRInfo:
        payload = {
            "title": title,
            "source": {"branch": {"name": source_branch}},
            "destination": {"branch": {"name": dest_branch}},
            "description": description,
        }
        data = self._request("POST", f"{self._base()}/pullrequests", json=payload).json()
        pr = self._pr_from_data(data)
        logger.info("Created PR #%d: %s", pr.pr_id, pr.url)
        return pr

    def update_pr_title(self, pr_id: int, title: str) -> None:
        self._request("PUT", f"{self._base()}/pullrequests/{pr_id}", json={"title": title})

    def post_comment(self, pr_id: int, body: str, *, file_path: str | None = None, line: int | None = None) -> int:
        payload: dict = {"content": {"raw": f"{body}\n\n{BOT_MARKER}"}}
        if file_path is not None:
            inline: dict = {"path": file_path}
            if line is not None:
                inline["to"] = line
            payload["inline"] = inline
        rid = self._request("POST", f"{self._base()}/pullrequests/{pr_id}/comments", json=payload).json()["id"]
        logger.info("Posted comment %d on PR #%d", rid, pr_id)
        return rid

    def list_comments(self, pr_id: int) -> list[dict]:
        return self._paginate(f"{self._base()}/pullrequests/{pr_id}/comments")

    def list_tasks(self, pr_id: int) -> list[dict]:
        return self._paginate(f"{self._base()}/pullrequests/{pr_id}/tasks")

    def close(self) -> None:
        self.client.close()
