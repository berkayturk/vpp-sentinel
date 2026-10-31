"""Validate the provisioned Grafana dashboards without Grafana.

Every ``*.json`` next to this file must be a dashboard with a ``uid``, a ``title``,
at least one panel, and every panel must target one of the provisioned datasources.
Run with ``make dashboards-check``; exits 1 on the first problem. Runs on any
Python ≥ 3.8 so a stock system interpreter is enough.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ALLOWED_DATASOURCE_UIDS = frozenset({"prometheus", "loki"})
REQUIRED_KEYS = ("uid", "title", "panels")


def check_dashboard(path: Path) -> str | None:
    """Return a problem description, or ``None`` when the dashboard is acceptable."""
    try:
        dashboard = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return f"invalid JSON: {exc}"
    missing = [key for key in REQUIRED_KEYS if key not in dashboard]
    if missing:
        return f"missing keys: {', '.join(missing)}"
    if not dashboard["panels"]:
        return "no panels"
    for panel in dashboard["panels"]:
        uid = (panel.get("datasource") or {}).get("uid")
        if uid not in ALLOWED_DATASOURCE_UIDS:
            return f"panel {panel.get('title')!r} uses datasource uid {uid!r}"
    return None


def main() -> int:
    dashboards = sorted(Path(__file__).resolve().parent.glob("*.json"))
    if not dashboards:
        print("no dashboards found", file=sys.stderr)
        return 1
    for path in dashboards:
        problem = check_dashboard(path)
        if problem:
            print(f"{path.name}: {problem}", file=sys.stderr)
            return 1
        print(f"ok: {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
