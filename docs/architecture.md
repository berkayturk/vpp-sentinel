# Architecture

This page describes what `make demo` brings up: the platform (a simulated
DER fleet, an MQTT broker, the ingestion service and PostgreSQL) and the
observability stack that watches it. The incident agent is layered on top.

```
                 control API (:8080)                    /metrics  /healthz (:8000)
                        ▲                                        ▲
                        │                                        │
 ┌──────────────────┐   │   ┌───────────┐   ┌──────────────┐     │     ┌────────────┐
 │ fleet-simulator  │───┘   │ mosquitto │   │  ingestion   │─────┘     │ postgresql │
 │ 300 virtual DERs │──────▶│  (MQTT)   │──▶│ parse→store  │──────────▶│  telemetry │
 │ 5 sites          │ pub   │           │sub│ →metrics→log │ asyncpg   │  incidents │
 └──────────────────┘       └───────────┘   └──────────────┘           └────────────┘
          ▲                                                                   ▲
          │ make incident-*  (fault switches)                                 │ seed: sites, devices
```

Everything runs in one `kind` cluster, namespace `vpp`, from the Helm chart in
`deploy/chart`.

## Fleet

| Site | Region | Chargers | Batteries | Inverters |
|---|---|---|---|---|
| lugano-south | Ticino | 30 | 15 | 15 |
| lugano-north | Ticino | 30 | 15 | 15 |
| bellinzona | Ticino | 30 | 15 | 15 |
| como | Lombardy | 30 | 15 | 15 |
| varese | Lombardy | 30 | 15 | 15 |

Device ids follow `{site}-{device_type}-{nnn}`, e.g. `lugano-south-charger-001`.
Every device publishes every 5 s by default (`FLEET_CADENCE_SECONDS`).

The simulator's physics are simple on purpose: inverters follow a half-sine
solar curve and produce nothing at night, batteries random-walk between
charging and discharging with state of charge clamped to 20–100 %, and
chargers run sessions that cluster around commute hours.

## Topic and payload

Topic: `vpp/{site}/{device_type}/{device_id}/telemetry`

```json
{
  "device_id": "lugano-south-charger-001",
  "site": "lugano-south",
  "device_type": "charger",
  "ts": "2026-10-31T14:05:00.000Z",
  "firmware": "2.3.1",
  "status": "charging",
  "power_kw": 7.4,
  "energy_kwh": 1234.5,
  "soc_pct": null,
  "voltage_v": 231.2
}
```

`soc_pct` is `null` for chargers and inverters. `status` is one of
`charging`, `idle`, `discharging`, `producing`, `fault`.

## Ingestion service

`services/ingestion`, Python 3.12, asyncio.

1. Subscribes to `vpp/#`.
2. `parse_telemetry` validates each payload with pydantic and returns either a
   `Telemetry` or a `ParseError` with a bounded `reason`.
3. `PostgresStore.insert` writes the row with `ON CONFLICT (device_id, ts) DO
   NOTHING`; a foreign-key violation means an unknown device.
4. Metrics and a structured JSON log line are recorded for every outcome.
5. Every 15 s the per-group gauges are recomputed from an immutable
   last-seen registry (freshness window 60 s).

Migrations in `services/ingestion/migrations/*.sql` are applied idempotently at
startup; applied filenames are tracked in `schema_migrations`.

### Metrics

| Metric | Type | Labels |
|---|---|---|
| `vpp_ingest_messages_total` | counter | `site`, `device_type`, `result` ∈ stored, duplicate, rejected |
| `vpp_ingest_rejects_total` | counter | `reason`, `device_type`, `firmware` |
| `vpp_ingest_lag_seconds` | histogram | `site` (device timestamp → stored) |
| `vpp_db_write_seconds` | histogram | — |
| `vpp_devices_reporting` | gauge | `site`, `device_type` (seen within 60 s) |
| `vpp_devices_expected` | gauge | `site`, `device_type` |

`reason` ∈ `invalid_json`, `invalid_timestamp`, `missing_field`,
`invalid_value`, `unknown_device`. Missing context is labelled `unknown`.
Device ids are never used as label values.

### Tables

| Table | Key columns |
|---|---|
| `sites` | `id`, `region` |
| `devices` | `id`, `site_id`, `device_type`, `firmware` |
| `telemetry` | `(device_id, ts)` primary key; `firmware`, `status`, `power_kw`, `energy_kwh`, `soc_pct`, `voltage_v` |
| `incidents` | `fingerprint`, `alert_name`, `site`, `device_type`, `severity`, `started_at`, `resolved_at`, `report_md`, `actions_taken`, `outcome` |

`incidents` is empty in this layer; the agent writes to it.

## Fault switches

The simulator exposes `POST /faults/{name}`, `GET /faults`, `DELETE /faults`
on port 8080. The Makefile wraps them through a short-lived port-forward.

| Fault | Parameters | Effect |
|---|---|---|
| `load-surge` | `site`, `multiplier`, `duration_seconds` | Devices at `site` publish `multiplier`× faster until the deadline, then revert |
| `firmware-drift` | `site`, `device_type`, `fraction` | The first `fraction` of those devices (sorted by id) report firmware 2.4.0 until cleared |

A fault only ever touches the subset it names; the rest of the fleet keeps
reporting normally. Fault state is an immutable value replaced on every
change, never mutated.

## Logs

Both services emit one JSON object per line to stdout with `event`, `level`,
`ts`, `service`, `logger` and event-specific fields (`device_id`, `site`,
`reason`, `firmware`, …). Lines written by libraries (aiomqtt, uvicorn,
asyncpg) go through the same formatter, so `| json` in Loki parses every
line. Device ids appear in logs here; masking before anything reaches the
agent is the agent layer's responsibility.

## Observability

The second Helm chart, `deploy/observability` (release `obs`, namespace
`monitoring`), wraps three upstream charts and adds the repository's own
rules and dashboards:

| Component | Chart | Role |
|---|---|---|
| Prometheus, Alertmanager, Grafana, kube-state-metrics | `kube-prometheus-stack` | Metrics, alert evaluation, dashboards. The upstream rule pack is disabled; only the repository's rule group exists. |
| Loki (single binary) | `loki` | Log store, filesystem storage on a 2 Gi volume that lives with the cluster. |
| Alloy | `alloy` | Log shipper. Tails the `vpp` namespace through the Kubernetes API and labels lines with `namespace`, `pod`, `container`, `app`. |

Prometheus scrapes the platform through the `vpp-pods` job: pods in the `vpp`
namespace annotated `prometheus.io/scrape: "true"`, port and path from the
annotations, every 10 s. Rules are evaluated every 10 s.

### Dashboards

Provisioned from `deploy/observability/dashboards/*.json` (one ConfigMap per
file, loaded by Grafana's sidecar). `make grafana` opens them.

| Dashboard | uid | Answers |
|---|---|---|
| VPP fleet health | `vpp-fleet-health` | Which site has devices missing, how stale each site's telemetry is, what is being rejected and why, where a surge comes from, and the rejected log lines. |
| VPP ingestion service | `vpp-ingestion-service` | Mean lag against its alert threshold, throughput by outcome, DB write p95, memory against the limit, container restarts, firing alerts. |

### Alerts

Two alerts, one per incident scenario, in `deploy/observability/rules/vpp.rules.yaml`
(unit-tested with `promtool`, `make alerts-test`):

| Alert | Condition | `for` | Severity | Labels | Runbook |
|---|---|---|---|---|---|
| `VppIngestionLagHigh` | mean age of stored telemetry over 1 m > 30 s, fleet-wide | 1 m | warning | `service` | [ingestion-lag-high](runbooks/ingestion-lag-high.md) |
| `VppSiteDevicesDark` | > 25 % of a `(site, device_type)` group not seen for 60 s | 2 m | critical | `service`, `site`, `device_type` | [site-devices-dark](runbooks/site-devices-dark.md) |

The lag alert is fleet-wide on purpose: there is one consumer, so a backlog
delays every site at once and a per-site alert would fire five times for one
fault. Per-site lag stays on the metric for investigation. The dark alert's
two-minute hold outlasts the short dip the reporting gauges show after an
ingestion restart, when the last-seen registry starts empty.

Alertmanager has a single route with a null receiver, grouped by alert name
and site; the incident agent polls its API instead of receiving
notifications. `make alerts` lists what is active.

**Known gap.** A fleet-wide outage is not this alert set's job. If the
ingestion pod disappears entirely, its gauges disappear with it and neither
alert can fire. If the pod is up but nothing is stored (broker or database
down), every group goes dark at once; the dark alert is guarded so that it
stays silent when more than half the fleet is missing, because one fault
must not fan out into fifteen critical alerts. Both cases belong to the
Kubernetes layer (readiness probes, the deployment controller) and to a
fleet-level check that a later layer may add.

### Scenario behaviour (measured on a laptop kind cluster)

- `make incident-load-surge` makes the 60 devices at lugano-south publish
  50× faster (every 100 ms) for 180 s. The ingestion pod's 100m CPU limit
  caps the single consumer near 400 messages per second, so a backlog
  builds, mean lag crosses 30 s after about 95 s and `VppIngestionLagHigh`
  is active about 2.5 minutes after injection. Memory rises with the backlog
  but stays well below the limit. `kubectl -n vpp rollout restart
  deploy/ingestion` drops the backlog; once the surge has ended the lag
  stays low and the alert resolves.
- `make incident-firmware-drift` switches 12 of como's 30 chargers to a
  firmware revision whose payload the parser rejects. Those devices stop
  being stored, `vpp_devices_reporting{site="como",device_type="charger"}`
  falls to 18 (40 % dark) and `VppSiteDevicesDark` fires; rejects by
  firmware rise on the fleet dashboard at the same moment. `make
  incident-clear` restores the devices and the alert resolves.

### Makefile targets added by this layer

`make grafana`, `make prometheus`, `make alertmanager` (port-forwards),
`make alerts` (active alerts), `make alerts-test` (promtool), `make
dashboards-check` (dashboard JSON validation).
