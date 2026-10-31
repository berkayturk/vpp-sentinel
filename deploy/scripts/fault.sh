#!/usr/bin/env bash
# Call the fleet simulator's control API through a short-lived port-forward.
#   fault.sh POST   /faults/load-surge '{"site":"lugano-south","multiplier":50,"duration_seconds":180}'
#   fault.sh DELETE /faults
#   fault.sh GET    /faults
set -euo pipefail

NAMESPACE="${NAMESPACE:-vpp}"
LOCAL_PORT="${LOCAL_PORT:-18080}"
METHOD="$1"
PATH_="$2"
BODY="${3:-}"

kubectl -n "$NAMESPACE" port-forward svc/fleet-simulator "${LOCAL_PORT}:8080" >/dev/null 2>&1 &
PF_PID=$!
trap 'kill "$PF_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${LOCAL_PORT}/healthz" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

if [ -n "$BODY" ]; then
  curl -fsS -X "$METHOD" "http://127.0.0.1:${LOCAL_PORT}${PATH_}" \
    -H 'Content-Type: application/json' -d "$BODY"
else
  curl -fsS -X "$METHOD" "http://127.0.0.1:${LOCAL_PORT}${PATH_}"
fi
echo
