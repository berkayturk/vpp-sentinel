"""GitHub REST: find, create and update a pull request. There is no merge call."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from agent.clients.errors import ClientError, get_json, post_json

SERVICE = "github"


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str


async def find_open_pr(http: httpx.AsyncClient, repo: str, head_branch: str) -> PullRequest | None:
    owner = repo.split("/")[0]
    raw = await get_json(
        http, SERVICE, f"/repos/{repo}/pulls", state="open", head=f"{owner}:{head_branch}"
    )
    if not isinstance(raw, list):
        raise ClientError(SERVICE, "pulls response is not a list")
    return _pull(raw[0]) if raw else None


async def create_pr(
    http: httpx.AsyncClient, repo: str, title: str, body: str, head: str, base: str
) -> PullRequest:
    raw = await post_json(
        http,
        SERVICE,
        f"/repos/{repo}/pulls",
        {"title": title, "body": body, "head": head, "base": base},
    )
    return _pull(raw)


async def update_pr(
    http: httpx.AsyncClient, repo: str, number: int, title: str, body: str
) -> PullRequest:
    try:
        response = await http.patch(
            f"/repos/{repo}/pulls/{number}", json={"title": title, "body": body}
        )
    except httpx.HTTPError as exc:
        raise ClientError(SERVICE, f"request failed: {exc}") from exc
    if response.status_code >= 400:
        raise ClientError(SERVICE, f"HTTP {response.status_code}: {response.text[:200]}")
    return _pull(response.json())


def _pull(raw: object) -> PullRequest:
    try:
        return PullRequest(int(raw["number"]), str(raw["html_url"]))  # type: ignore[index]
    except (KeyError, TypeError, ValueError) as exc:
        raise ClientError(SERVICE, f"malformed pull request: {exc}") from exc
