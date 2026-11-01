> This is the agent's own report for the firmware-drift incident, copied verbatim from the `incidents` table (incident #3, VppSiteDevicesDark).

# Incident report (2026-11-01T13:49:24.038000+00:00)

## Summary
At 2026-11-01T13:49:24Z, 12 of the 30 chargers at como stopped storing telemetry, and the critical VppSiteDevicesDark alert fired. The devices are still sending, but they run firmware 2.4.0, which sends `ts` as an integer in epoch milliseconds. The ingestion parser only accepted ISO 8601 strings, so every one of their messages is rejected as `invalid_timestamp`. A parser fix with tests is open as PR #1 (not merged). The devices stay dark until a human merges and deploys it or rolls back the firmware.

## Blast radius
One site, one device type: como/charger, 12 of 30 missing (40%). All other site/type groups have 0 missing: lugano-north, lugano-south, bellinzona and varese (chargers, batteries, inverters), plus como batteries (15/15) and como inverters (15/15). Affected firmware: 2.4.0 only. Logs show 12 distinct como charger pseudonyms being rejected, matching the 12 missing. Duration: the 1m reject rate was empty ~5 min before 13:49:35 and 2.40/s by 13:49:35, so rejects started after about 13:44Z and were still going at 13:49:35Z. Impact per runbook: dispatch decisions for como are unsafe while these chargers' power and state are unknown.

## Hypothesis
Charger firmware 2.4.0 changed the telemetry timestamp format from an ISO 8601 string to an integer epoch-millisecond value. The `ts` before-validator in services/ingestion/src/ingestion/schema.py rejected any non-string, non-datetime value, so the platform rejects every message from these devices. The cause is a payload-format mismatch in our parser, not connectivity, the broker or ingestion capacity.

## Evidence
- sum by (site, device_type) (vpp_devices_expected - vpp_devices_reporting): charger/como = 12 now, 0 ten minutes earlier; every other group = 0 at both times
- vpp_devices_reporting{site=como, device_type=charger} = 18 vs vpp_devices_expected = 30
- sum by (reason, firmware) (rate(vpp_ingest_rejects_total[5m])): now {firmware=2.4.0, reason=invalid_timestamp} 1.66339; 10 min earlier: no data. No other reason or firmware present
- sum by (site, firmware) (rate(vpp_ingest_rejects_total[1m])): now {firmware=2.4.0} 2.39957; 5 min earlier: no data
- Stored rate for como (1m): 12.2222/s five minutes earlier -> 9.59827/s now, a drop of ~2.4/s, matching the 2.40/s reject rate (12 devices x one message per 5 s)
- Log line 2026-11-01T13:49:35.670858Z event=telemetry.rejected topic=vpp/como/charger/como-charger-#01549c/telemetry reason=invalid_timestamp firmware=2.4.0 detail='ts: Value error, timestamp must be an ISO 8601 string (input_type=int)' payload ts=1793540975005
- 1793540975005 ms since the epoch = 2026-11-01T13:49:35.005Z, matching the log time, so the value is a valid epoch-ms timestamp, not garbage
- Rejected pseudonyms in logs (12): como-charger-#cbedc4, #1e108e, #adbfec, #b172ab, #a65b5b, #01549c, #5742ef, #6dda40, #346e16, #607bd9, #ca055d, #45a194
- schema.py before this change: `if not isinstance(value, str | datetime): raise ValueError("timestamp must be an ISO 8601 string")`, which is the exact message in the reject log
- container_restarts = 0; memory working set 57.2 MB; mean ingest lag fell from 6.96 s to 0.92 s over the window, so there is no sign of an ingestion capacity problem

## Verification
- Check 1 (one site or all?): only como/charger is missing devices; the other 14 site/type groups report fully, which rules out a fleet-wide ingestion or broker outage
- Check 2 (rejects by reason and firmware): one series, firmware=2.4.0 / reason=invalid_timestamp, absent before the alert window and present with it
- Check 3 (rejected samples): about 50 recent telemetry.rejected lines read; all are como chargers on firmware 2.4.0 with an integer `ts`; no other payload fields are malformed
- Check 4 (broker and simulator pod health via kubectl): NOT performed, because there is no kubectl or shell tool. Indirect evidence: messages from the affected devices reach ingestion (they show up as rejects), and all other sites store normally
- Ruled out connectivity: reject rate (2.40/s) equals the missing stored rate for como
- Ruled out a pod crash or resource problem: 0 container restarts, normal memory, lag improving
- Reproduced in code: a new unit test with the logged payload shape failed with the same detail string as production before the fix
- Fix checked: run_tests 54 passed, 5 skipped. New tests confirm epoch seconds, negative, huge, float and boolean `ts` values are still rejected as invalid_timestamp
- Incident memory: no earlier VppSiteDevicesDark incident
- No restart_deployment was attempted: the runbook's Act step for firmware-correlated rejects is a code change, and a restart would not change parser behaviour
- Not investigated: the ingestion pod changed from ingestion-76dcc87b49-gwhmj to ingestion-5fc7d76bbb-rptg7 during the window (a new ReplicaSet). The reject pattern is fully explained by the payload format, but I did not check what that rollout changed
- Not investigated: the 1m stored rate for lugano-south (12.00 -> 10.80/s) and varese (12.00 -> 11.40/s) dipped in the latest sample, with no rejects and 0 missing devices at those sites

## Actions taken
- 2026-11-01T13:50:21.182967+00:00 `write_source_file` {'path': 'services/ingestion/tests/test_schema.py'} → REFUSED: the content carries sanitised placeholders (a pseudonymised device id, <ip> or <token>); write only what belongs in source code
- 2026-11-01T13:50:40.982387+00:00 `run_tests`  → passed
Using CPython 3.12.16 interpreter at: /usr/local/bin/python3.12
Creating virtual environment at: /tmp/venv
   Building vpp-ingestion @ file:///workspace/vppsitedevicesdark-como-charger/services/ingestion
Downloading pygments (1.2MiB)
Downloading pydantic-core (2.0MiB)
Downloading asyncpg (3.5…
- 2026-11-01T13:50:57.647828+00:00 `write_source_file` {'path': 'services/ingestion/tests/test_schema.py'} → ok: wrote services/ingestion/tests/test_schema.py
- 2026-11-01T13:51:05.266342+00:00 `run_tests`  → failed
uv exited 1: .....................................F
=================================== FAILURES ===================================
_____________ test_parses_firmware_240_epoch_millisecond_timestamp _____________

    def test_parses_firmware_240_epoch_millisecond_timestamp():
        t = pa…
- 2026-11-01T13:51:19.527430+00:00 `write_source_file` {'path': 'services/ingestion/src/ingestion/schema.py'} → ok: wrote services/ingestion/src/ingestion/schema.py
- 2026-11-01T13:51:28.049312+00:00 `run_tests`  → passed
......................................................sssss              [100%]
54 passed, 5 skipped in 3.43s
- 2026-11-01T13:51:41.895612+00:00 `open_pull_request` {'title': 'Accept integer epoch-millisecond timestamps from charger firmware 2.4.0'} → ok: https://github.com/berkayturk/vpp-sentinel/pull/1

## Recommended action
1) Review and merge PR #1 (https://github.com/berkayturk/vpp-sentinel/pull/1, "Accept integer epoch-millisecond timestamps from charger firmware 2.4.0"), then deploy ingestion. The alert should clear once como/charger reporting returns to 30/30 and the firmware=2.4.0 reject rate drops to 0. 2) If the fix cannot ship quickly, work with the device or fleet owner to pause or roll back the 2.4.0 rollout on the 12 como chargers. Until one of these happens, treat como charger capacity as unknown for dispatch. 3) Run runbook check 4, which I could not (kubectl -n vpp get pods; mosquitto logs). Also confirm the ingestion rollout to ReplicaSet 5fc7d76bbb during the window is unrelated. 4) Find out whether firmware 2.4.0 is planned for other sites, and ask the firmware team to document the timestamp format change. 5) Incident record: firmware 2.4.0, 12 chargers at como, rejects from about 13:44Z, still ongoing at 13:49:35Z.

## Seen before
no

**A human was paged** (severity gate).

## Pull request
https://github.com/berkayturk/vpp-sentinel/pull/1
