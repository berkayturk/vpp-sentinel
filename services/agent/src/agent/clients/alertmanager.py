"""Alertmanager v2 API: the active alerts the agent wakes on."""

from __future__ import annotations

from datetime import datetime

import httpx

from agent.clients.errors import ClientError, get_json
from agent.fingerprint import Alert

SERVICE = "alertmanager"


async def active_alerts(http: httpx.AsyncClient) -> tuple[Alert, ...]:
    """Firing alerts that are neither silenced nor inhibited."""
    raw = await get_json(
        http, SERVICE, "/api/v2/alerts", active="true", silenced="false", inhibited="false"
    )
    if not isinstance(raw, list):
        raise ClientError(SERVICE, "alerts response is not a list")
    return tuple(_parse(item) for item in raw if _is_active(item))


def _is_active(item: object) -> bool:
    return isinstance(item, dict) and item.get("status", {}).get("state") == "active"


def _parse(item: dict) -> Alert:
    labels = dict(item.get("labels", {}))
    try:
        starts_at = datetime.fromisoformat(item["startsAt"].replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise ClientError(SERVICE, f"alert without a valid startsAt: {exc}") from exc
    return Alert(
        name=labels.get("alertname", "unknown"),
        labels=labels,
        annotations=dict(item.get("annotations", {})),
        starts_at=starts_at,
    )
