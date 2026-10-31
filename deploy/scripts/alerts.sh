#!/usr/bin/env bash
# List active Alertmanager alerts through a short-lived port-forward.
# Output: one line per alert — ALERTNAME SITE DEVICE_TYPE STATE — nothing when quiet.
set -euo pipefail

NAMESPACE="${NAMESPACE:-monitoring}"
SERVICE="${SERVICE:-obs-alertmanager}"
LOCAL_PORT="${LOCAL_PORT:-19093}"

kubectl -n "$NAMESPACE" port-forward "svc/${SERVICE}" "${LOCAL_PORT}:9093" >/dev/null 2>&1 &
PF_PID=$!
trap 'kill "$PF_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${LOCAL_PORT}/-/ready" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

curl -fsS "http://127.0.0.1:${LOCAL_PORT}/api/v2/alerts?active=true" | python3 -c '
import json, sys
for alert in json.load(sys.stdin):
    labels = alert.get("labels", {})
    print(labels.get("alertname", "?"), labels.get("site", "-"), labels.get("device_type", "-"),
          alert.get("status", {}).get("state", "?"))
'
