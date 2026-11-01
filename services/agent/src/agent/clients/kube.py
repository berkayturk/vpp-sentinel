"""The one Kubernetes call the agent can make: a rollout restart of a named deployment.

The RBAC Role in the chart allows ``get`` and ``patch`` on exactly the
deployments the allowlist names, so even a bug here could not reach anything
else. After the patch the call waits for the rollout so the model can tell a
completed restart from a requested one.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from agent.clients.errors import ClientError, get_json

SERVICE = "kubernetes"
PATCH_CONTENT_TYPE = "application/strategic-merge-patch+json"
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 90.0


@dataclass(frozen=True)
class RolloutResult:
    rolled_out: bool
    seconds: float


async def restart_deployment(
    http: httpx.AsyncClient,
    namespace: str,
    name: str,
    at: datetime,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> RolloutResult:
    path = f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}"
    body = {
        "spec": {
            "template": {
                "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": at.isoformat()}}
            }
        }
    }
    try:
        response = await http.patch(path, json=body, headers={"Content-Type": PATCH_CONTENT_TYPE})
    except httpx.HTTPError as exc:
        raise ClientError(SERVICE, f"request failed: {exc}") from exc
    if response.status_code >= 400:
        raise ClientError(SERVICE, f"HTTP {response.status_code}: {response.text[:200]}")
    generation = int((response.json().get("metadata") or {}).get("generation", 0))
    started = time.monotonic()
    while True:
        if _rolled_out(await get_json(http, SERVICE, path), generation):
            return RolloutResult(True, time.monotonic() - started)
        if time.monotonic() - started >= timeout_seconds:
            return RolloutResult(False, time.monotonic() - started)
        await asyncio.sleep(poll_seconds)


def _rolled_out(deployment: dict, generation: int) -> bool:
    status = deployment.get("status", {})
    wanted = int(deployment.get("spec", {}).get("replicas", 1))
    return (
        int(status.get("observedGeneration", 0)) >= generation
        and int(status.get("updatedReplicas", 0)) == wanted
        and int(status.get("availableReplicas", 0)) == wanted
        and int(status.get("unavailableReplicas", 0)) == 0
    )
