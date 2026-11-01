"""The one Kubernetes call the agent can make: a rollout restart of a named deployment.

The RBAC Role in the chart allows ``patch`` on exactly the deployments the
allowlist names, so even a bug here could not reach anything else.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from agent.clients.errors import ClientError

SERVICE = "kubernetes"
PATCH_CONTENT_TYPE = "application/strategic-merge-patch+json"


async def restart_deployment(
    http: httpx.AsyncClient, namespace: str, name: str, at: datetime
) -> None:
    body = {
        "spec": {
            "template": {
                "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": at.isoformat()}}
            }
        }
    }
    try:
        response = await http.patch(
            f"/apis/apps/v1/namespaces/{namespace}/deployments/{name}",
            json=body,
            headers={"Content-Type": PATCH_CONTENT_TYPE},
        )
    except httpx.HTTPError as exc:
        raise ClientError(SERVICE, f"request failed: {exc}") from exc
    if response.status_code >= 400:
        raise ClientError(SERVICE, f"HTTP {response.status_code}: {response.text[:200]}")
