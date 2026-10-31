# Runbook: VppSiteDevicesDark

**Severity:** critical · **Owner:** platform on-call · **Service:** ingestion

## Symptom

More than a quarter of one site's devices of one type
(`vpp_devices_reporting` vs `vpp_devices_expected`) have not stored
telemetry within the 60 s freshness window, for at least two minutes.

## Impact

That part of the fleet is invisible to the VPP. Dispatch decisions for
the site are unsafe until the devices report again, because their power
and state of charge are unknown.

## Check

1. **One site or all of them?** The *VPP fleet health* dashboard, panel
   *Devices reporting / expected by site*. Every site dark at once points
   at the ingestion service or the broker, not at the devices.

2. **Are the messages still arriving but being rejected?** Rejections
   over the alert window, by reason and firmware version:

   ```promql
   sum by (reason, firmware) (rate(vpp_ingest_rejects_total[5m]))
   ```

   A reject rate that appeared together with the alert, concentrated on
   one firmware version, means the devices are talking but the platform
   does not understand them.

3. **What do the rejected samples look like?**

   ```logql
   {namespace="vpp", app="ingestion"} | json | event="telemetry.rejected"
   ```

   The log line carries the topic, the reject reason and the firmware.

4. **Are the broker and the simulator healthy?**

   ```bash
   kubectl -n vpp get pods
   kubectl -n vpp logs deploy/mosquitto --tail=50
   ```

## Act

- **Rejects correlate with one firmware version:** the ingestion parser
  needs a code change. Reproduce a rejected sample as a unit test in
  `services/ingestion/tests`, make the parser accept the new payload
  shape while still rejecting malformed input, and open a pull request
  that carries the incident report. A human reviews and merges; the
  alert resolves once the fix is deployed or the devices are rolled
  back.
- **No messages arrive at all from the site:** connectivity, not
  parsing. Check the broker, the network path and the devices
  themselves.

## Escalate

Paging is not wired in this demo. The `critical` severity is what a real
notification channel would key on; the incident agent records that a page
would have been sent.

## Afterwards

Record the firmware version, the number of devices affected and the
duration in the incident record.
