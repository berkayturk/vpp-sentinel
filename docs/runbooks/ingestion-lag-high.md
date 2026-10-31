# Runbook: VppIngestionLagHigh

**Severity:** warning · **Owner:** platform on-call · **Service:** ingestion

## Symptom

Stored telemetry is on average more than 30 s old (mean of
`vpp_ingest_lag_seconds` over the last minute, across all sites) for at
least one minute.

## Impact

Dashboards and any downstream decision run on stale fleet state. No data
is lost while the pod stays up: messages queue inside the consumer and
are stored late. If the backlog grows until the pod is OOM-killed, the
queued messages are lost (MQTT QoS 0).

## Check

1. **Where is the extra volume coming from?** Compare the per-site stored
   rate with the previous hour:

   ```promql
   sum by (site) (rate(vpp_ingest_messages_total{result="stored"}[1m]))
   ```

   One site standing far above the others points at a publish-rate change
   on that site (firmware roll-out, cadence misconfiguration, replayed
   backlog from devices that were offline).

2. **Is the pod under memory pressure or restarting?** The *VPP ingestion
   service* dashboard shows working-set memory against the limit and the
   container restart count. A working set approaching the limit means the
   in-memory queue is the backlog.

3. **Anything else wrong?** Warnings from the broker or the database:

   ```logql
   {namespace="vpp", app="ingestion"} | json | level=~"warning|error"
   ```

## Act

Restart the ingestion deployment:

```bash
kubectl -n vpp rollout restart deploy/ingestion
```

This drops the in-memory backlog and reconnects to the broker. If the
surge has ended or is ending, the mean lag falls below 30 s within about
two minutes and the alert resolves.

**This is a mitigation, not a fix.** The consumer still processes
messages one at a time; the next surge of the same size will reproduce
the alert.

## Escalate

If the alert fires again within 30 minutes of a restart, do **not**
restart again. Escalate to the platform team to review the memory limit
and the consumer's parallelism (several consumer tasks, or batched
inserts).

## Afterwards

Record the site the surge came from, its duration and the peak lag in the
incident record, so the next occurrence can be compared with this one.
