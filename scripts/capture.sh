#!/usr/bin/env bash
# Run both incident scenarios on the current cluster and write docs/kpis.md and
# docs/sample-report.md. Prerequisites are checked by the Python driver before any fault
# is injected. Port-forwards are opened here and closed on exit.
set -euo pipefail

NAMESPACE="${NAMESPACE:-vpp}"
OBS_NAMESPACE="${OBS_NAMESPACE:-monitoring}"
OUT_DIR="${OUT_DIR:-$(cd "$(dirname "$0")/.." && pwd)/docs}"

cleanup() { kill "${PIDS[@]}" 2>/dev/null || true; }
PIDS=()
trap cleanup EXIT

kubectl -n "$OBS_NAMESPACE" port-forward svc/obs-prometheus 29090:9090 >/dev/null 2>&1 & PIDS+=($!)
kubectl -n "$OBS_NAMESPACE" port-forward svc/obs-alertmanager 29093:9093 >/dev/null 2>&1 & PIDS+=($!)
kubectl -n "$NAMESPACE" port-forward svc/postgres 25432:5432 >/dev/null 2>&1 & PIDS+=($!)
sleep 3

# Facts only the cluster knows, handed to the driver as plain values (never the secret itself).
SECRET_KEYS=$(kubectl -n "$NAMESPACE" get secret agent-credentials -o json 2>/dev/null \
  | python3 -c 'import base64,json,sys; d=json.load(sys.stdin).get("data", {}); print(",".join(sorted(k for k,v in d.items() if base64.b64decode(v).strip())))' || true)
AGENT_READY=$(kubectl -n "$NAMESPACE" logs deploy/agent -c agent --tail=200 2>/dev/null \
  | grep -c '"event": "agent.ready"' || true)
AGENT_MODEL=$(kubectl -n "$NAMESPACE" get deploy agent -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="AGENT_MODEL")].value}')
DB_PASSWORD=$(kubectl -n "$NAMESPACE" get secret postgres-credentials -o jsonpath='{.data.DATABASE_URL}' | base64 -d | sed -E 's#.*://[^:]+:([^@]+)@.*#\1#')

export DATABASE_URL="postgresql://vpp:${DB_PASSWORD}@127.0.0.1:25432/vpp"
export PROMETHEUS_URL="http://127.0.0.1:29090"
export ALERTMANAGER_URL="http://127.0.0.1:29093"
export CAPTURE_SECRET_KEYS="$SECRET_KEYS"
export CAPTURE_AGENT_READY="$AGENT_READY"
export CAPTURE_AGENT_MODEL="$AGENT_MODEL"
export CAPTURE_FAULT_SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/deploy/scripts/fault.sh"

uv run --directory services/agent python -m agent.capture --out "$OUT_DIR"
