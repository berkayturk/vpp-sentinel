"""Prometheus metrics exposed by the ingestion service.

Label sets are kept bounded. ``site`` and ``device_type`` come from the
registered fleet (callers pass the ``(site, device_type)`` group they looked
up), ``result`` and ``reason`` are closed enumerations, and ``firmware`` must
look like a semantic version or is collapsed to ``other``. Anything a
publisher could invent is mapped to ``unknown``/``other``. Device ids are
never labels.
"""

import re
from datetime import datetime

from prometheus_client import Counter, Gauge, Histogram

from ingestion.schema import ParseError, Telemetry

Group = tuple[str, str]

UNKNOWN_LABEL = "unknown"
OTHER_LABEL = "other"
KNOWN_DEVICE_TYPES = frozenset({"charger", "battery", "inverter"})
FIRMWARE_PATTERN = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")

MESSAGES_TOTAL = Counter(
    "vpp_ingest_messages_total",
    "Telemetry messages consumed, by outcome.",
    ["site", "device_type", "result"],
)
REJECTS_TOTAL = Counter(
    "vpp_ingest_rejects_total",
    "Telemetry messages rejected by the parser or the store, by reason.",
    ["reason", "device_type", "firmware"],
)
LAG_SECONDS = Histogram(
    "vpp_ingest_lag_seconds",
    "Age of a telemetry sample when it was stored (device timestamp to ingestion).",
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300),
)
DB_WRITE_SECONDS = Histogram(
    "vpp_db_write_seconds",
    "Wall time of one telemetry insert.",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2),
)
DEVICES_REPORTING = Gauge(
    "vpp_devices_reporting",
    "Devices that reported within the freshness window.",
    ["site", "device_type"],
)
DEVICES_EXPECTED = Gauge(
    "vpp_devices_expected",
    "Devices registered for the site and type.",
    ["site", "device_type"],
)


def _device_type_label(value: str | None) -> str:
    return value if value in KNOWN_DEVICE_TYPES else UNKNOWN_LABEL


def _firmware_label(value: str | None) -> str:
    if value is None:
        return UNKNOWN_LABEL
    return value if FIRMWARE_PATTERN.match(value) else OTHER_LABEL


def record_stored(t: Telemetry, group: Group, now: datetime) -> None:
    MESSAGES_TOTAL.labels(*group, "stored").inc()
    LAG_SECONDS.observe(max(0.0, (now - t.ts).total_seconds()))


def record_duplicate(group: Group) -> None:
    MESSAGES_TOTAL.labels(*group, "duplicate").inc()


def record_store_error(group: Group) -> None:
    MESSAGES_TOTAL.labels(*group, "error").inc()


def record_rejected(error: ParseError, site: str | None) -> None:
    """``site`` must already be validated by the caller (a known site or ``None``)."""
    device_type = _device_type_label(error.device_type)
    REJECTS_TOTAL.labels(error.reason, device_type, _firmware_label(error.firmware)).inc()
    MESSAGES_TOTAL.labels(site or UNKNOWN_LABEL, device_type, "rejected").inc()


def observe_db_write(seconds: float) -> None:
    DB_WRITE_SECONDS.observe(seconds)


def publish_gauges(counts: dict[Group, int], expected: dict[Group, int]) -> None:
    for (site, device_type), value in counts.items():
        DEVICES_REPORTING.labels(site, device_type).set(value)
    for (site, device_type), value in expected.items():
        DEVICES_EXPECTED.labels(site, device_type).set(value)
