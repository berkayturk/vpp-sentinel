"""Grafana annotations: how "page a human" is represented in the demo."""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from agent.clients.errors import ClientError, post_json

SERVICE = "grafana"


async def annotate(http: httpx.AsyncClient, text: str, tags: Sequence[str]) -> int:
    raw = await post_json(http, SERVICE, "/api/annotations", {"text": text, "tags": list(tags)})
    try:
        return int(raw["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ClientError(SERVICE, f"annotation response without id: {exc}") from exc
