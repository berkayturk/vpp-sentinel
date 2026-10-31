"""Prometheus metrics exposed by the ingestion service.

Label sets are kept bounded: ``site`` and ``device_type`` come from a fixed
fleet, ``result`` and ``reason`` from closed enumerations, and ``firmware``
from the handful of versions a fleet runs. Device ids are never labels.
"""

from datetime import datetime

from prometheus_client import Counter, Gauge, Histogram

from ingestion.schema import ParseError, Telemetry

UNKNOWN_LABEL = "unknown"

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


def record_stored(t: Telemetry, now: datetime) -> None:
    MESSAGES_TOTAL.labels(t.site, t.device_type, "stored").inc()
    LAG_SECONDS.observe(max(0.0, (now - t.ts).total_seconds()))


def record_duplicate(t: Telemetry) -> None:
    MESSAGES_TOTAL.labels(t.site, t.device_type, "duplicate").inc()


def record_rejected(error: ParseError, site: str | None) -> None:
    device_type = error.device_type or UNKNOWN_LABEL
    firmware = error.firmware or UNKNOWN_LABEL
    REJECTS_TOTAL.labels(error.reason, device_type, firmware).inc()
    MESSAGES_TOTAL.labels(site or UNKNOWN_LABEL, device_type, "rejected").inc()


def observe_db_write(seconds: float) -> None:
    DB_WRITE_SECONDS.observe(seconds)


def publish_gauges(
    counts: dict[tuple[str, str], int], expected: dict[tuple[str, str], int]
) -> None:
    for (site, device_type), value in counts.items():
        DEVICES_REPORTING.labels(site, device_type).set(value)
    for (site, device_type), value in expected.items():
        DEVICES_EXPECTED.labels(site, device_type).set(value)
