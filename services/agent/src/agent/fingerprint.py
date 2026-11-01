"""Alerts as the agent sees them, and the fingerprint that dedupes incidents."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

DEFAULT_SEVERITY = "warning"
MISSING = "-"


@dataclass(frozen=True)
class Alert:
    name: str
    labels: Mapping[str, str]
    annotations: Mapping[str, str]
    starts_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))
        object.__setattr__(self, "annotations", MappingProxyType(dict(self.annotations)))

    @property
    def severity(self) -> str:
        return self.labels.get("severity", DEFAULT_SEVERITY)


def fingerprint(alert: Alert) -> str:
    """``alertname|site|device_type`` with ``-`` for a missing label."""
    site = alert.labels.get("site", MISSING)
    device_type = alert.labels.get("device_type", MISSING)
    return f"{alert.name}|{site}|{device_type}"


def slug(fp: str) -> str:
    """A branch-safe form: lowercase, runs of non-alphanumerics collapse to one dash."""
    return re.sub(r"[^a-z0-9]+", "-", fp.lower()).strip("-")
