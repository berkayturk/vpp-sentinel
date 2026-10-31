SERVICES := services/fleet-simulator services/ingestion
CLUSTER  := vpp-sentinel
NAMESPACE := vpp
RELEASE  := vpp-sentinel
OBS_NAMESPACE := monitoring
OBS_RELEASE := obs
FAULT    := NAMESPACE=$(NAMESPACE) deploy/scripts/fault.sh
# Same Prometheus version kube-prometheus-stack installs; promtool comes from the same image.
PROMTOOL := docker run --rm -v $(PWD)/deploy/observability/rules:/rules --entrypoint promtool prom/prometheus:v3.15.1

.PHONY: help test lint fmt images demo down status incident-load-surge incident-firmware-drift incident-clear \
        grafana prometheus alertmanager alerts alerts-test

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-24s %s\n", $$1, $$2}'

test: ## Run every service's test suite with coverage
	@for s in $(SERVICES); do echo "==> $$s"; uv run --directory $$s pytest -q || exit 1; done

lint: ## Static checks: ruff lint and format check
	uv run --with ruff ruff check .
	uv run --with ruff ruff format --check .

fmt: ## Auto-format with ruff
	uv run --with ruff ruff format .
	uv run --with ruff ruff check --fix .

images: ## Build both service images with the :dev tag
	docker build -f services/fleet-simulator/Dockerfile -t vpp-fleet-simulator:dev .
	docker build -f services/ingestion/Dockerfile -t vpp-ingestion:dev .

demo: images ## Create the kind cluster (if needed), deploy observability and the platform
	@kind get clusters 2>/dev/null | grep -qx $(CLUSTER) || kind create cluster --config deploy/kind/cluster.yaml
	kind load docker-image vpp-fleet-simulator:dev vpp-ingestion:dev --name $(CLUSTER)
	helm dependency build deploy/observability
	helm upgrade --install $(OBS_RELEASE) deploy/observability -n $(OBS_NAMESPACE) --create-namespace --wait --timeout 10m
	helm upgrade --install $(RELEASE) deploy/chart -n $(NAMESPACE) --create-namespace --wait --timeout 5m
	@# The :dev tag never changes, so a re-run must restart the pods to pick up freshly loaded images.
	kubectl -n $(NAMESPACE) rollout restart deploy/fleet-simulator deploy/ingestion
	kubectl -n $(NAMESPACE) rollout status deploy/fleet-simulator deploy/ingestion --timeout 3m
	@echo
	@echo "Platform is up. Inspect it with:"
	@echo "  make status     # pods, active faults, active alerts"
	@echo "  make grafana    # http://localhost:3000, admin / vpp-demo-password"

down: ## Delete the kind cluster
	kind delete cluster --name $(CLUSTER)

status: ## Show pods, active faults and active alerts
	kubectl -n $(NAMESPACE) get pods
	@echo "Active faults:"
	@$(FAULT) GET /faults
	@echo "Active alerts:"
	@NAMESPACE=$(OBS_NAMESPACE) deploy/scripts/alerts.sh

grafana: ## Port-forward Grafana to http://localhost:3000 (admin / vpp-demo-password)
	@echo "Grafana: http://localhost:3000  (admin / vpp-demo-password)"
	kubectl -n $(OBS_NAMESPACE) port-forward svc/grafana 3000:80

prometheus: ## Port-forward Prometheus to http://localhost:9090
	kubectl -n $(OBS_NAMESPACE) port-forward svc/$(OBS_RELEASE)-prometheus 9090:9090

alertmanager: ## Port-forward Alertmanager to http://localhost:9093
	kubectl -n $(OBS_NAMESPACE) port-forward svc/$(OBS_RELEASE)-alertmanager 9093:9093

alerts: ## List active alerts (one line each; empty when quiet)
	@NAMESPACE=$(OBS_NAMESPACE) deploy/scripts/alerts.sh

alerts-test: ## Check the alert rules and run their promtool unit tests
	$(PROMTOOL) check rules /rules/vpp.rules.yaml
	$(PROMTOOL) test rules /rules/vpp.tests.yaml

incident-load-surge: ## Inject a 10x telemetry surge at lugano-south for 180 s
	@$(FAULT) POST /faults/load-surge '{"site":"lugano-south","multiplier":10,"duration_seconds":180}'

incident-firmware-drift: ## Switch 40 % of como's chargers to firmware 2.4.0
	@$(FAULT) POST /faults/firmware-drift '{"site":"como","device_type":"charger","fraction":0.4}'

incident-clear: ## Switch every fault off
	@$(FAULT) DELETE /faults
