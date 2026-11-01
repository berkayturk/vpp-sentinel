"""One error type for every client, so callers can catch it and keep going."""

from __future__ import annotations

from typing import Any

import httpx


class ClientError(Exception):
    def __init__(self, service: str, detail: str) -> None:
        super().__init__(f"{service}: {detail}")
        self.service = service
        self.detail = detail


async def get_json(http: httpx.AsyncClient, service: str, path: str, **params: Any) -> Any:
    return await _request(http, service, "GET", path, params=params)


async def post_json(http: httpx.AsyncClient, service: str, path: str, body: Any) -> Any:
    return await _request(http, service, "POST", path, json=body)


async def _request(http: httpx.AsyncClient, service: str, method: str, path: str, **kw: Any) -> Any:
    try:
        response = await http.request(method, path, **kw)
    except httpx.HTTPError as exc:
        raise ClientError(service, f"request failed: {exc}") from exc
    if response.status_code >= 400:
        raise ClientError(service, f"HTTP {response.status_code}: {response.text[:200]}")
    try:
        return response.json()
    except ValueError as exc:
        raise ClientError(service, "response is not JSON") from exc
