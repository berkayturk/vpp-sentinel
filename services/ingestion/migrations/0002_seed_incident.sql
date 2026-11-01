-- One historical incident so the first demo run can show the agent's memory lookup.
-- The README says this row is seeded. Idempotent: inserted only if absent.

INSERT INTO incidents (fingerprint, alert_name, site, device_type, severity, started_at,
                       resolved_at, report_md, actions_taken, outcome)
SELECT
  'VppIngestionLagHigh|-|-',
  'VppIngestionLagHigh',
  NULL,
  NULL,
  'warning',
  '2026-10-18T09:12:00Z'::timestamptz,
  '2026-10-18T09:18:40Z'::timestamptz,
  $report$# Incident: VppIngestionLagHigh (2026-10-18 09:12 UTC)

**Summary.** Mean ingestion lag rose above 30 s for about six minutes after the
stored message rate at lugano-north climbed to roughly fifteen times its
baseline. No devices went dark; telemetry was stored late, not lost.

**Blast radius.** Fleet-wide staleness; the extra volume came from
lugano-north (60 devices). No other site changed its publish rate.

**Hypothesis.** A publish-rate increase at one site exceeded what the single
ingestion consumer can store under its CPU limit, so a backlog built up.

**Evidence.** Stored rate at lugano-north 12 → 180 msg/s at 09:09; mean lag
0.1 s → 48 s by 09:13; ingestion CPU throttled share 0.92; memory 41 → 77 MiB;
no container restarts; no reject rate change.

**Actions taken.** restart_deployment(ingestion) at 09:16:05 — allowed by the
allowlist; outcome ok. Lag fell to 0.3 s within 70 s and the alert resolved at
09:18:40.

**This was a mitigation, not a fix.** The consumer still processes one message
at a time under a 100m CPU limit; the next surge of the same size will
reproduce the alert.

**Recommended action.** Review the ingestion CPU limit and consumer
parallelism; find why lugano-north's publish cadence changed.
$report$,
  '[{"tool": "restart_deployment", "args": {"name": "ingestion"}, "outcome": "ok", "at": "2026-10-18T09:16:05Z"}]'::jsonb,
  'resolved: mitigation worked, lag drained in 70 s'
WHERE NOT EXISTS (
  SELECT 1 FROM incidents
  WHERE fingerprint = 'VppIngestionLagHigh|-|-' AND started_at = '2026-10-18T09:12:00Z'::timestamptz
);
