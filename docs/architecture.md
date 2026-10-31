# Architecture — platform foundation

This page describes what `make demo` brings up. Observability, alerting and
the incident agent are layered on top of this foundation; see the design
document in `docs/superpowers/specs/`.

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
| `vpp_ingest_lag_seconds` | histogram | — (device timestamp → stored) |
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
`ts`, `service` and event-specific fields (`device_id`, `site`, `reason`,
`firmware`, …). Device ids appear in logs here; masking before anything
reaches the agent is the agent layer's responsibility.
