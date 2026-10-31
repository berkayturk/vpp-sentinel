SERVICES := services/fleet-simulator services/ingestion
CLUSTER  := vpp-sentinel
NAMESPACE := vpp
RELEASE  := vpp-sentinel
FAULT    := NAMESPACE=$(NAMESPACE) deploy/scripts/fault.sh

.PHONY: help test lint fmt images demo down status incident-load-surge incident-firmware-drift incident-clear

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

demo: images ## Create the kind cluster (if needed) and deploy the platform
	@kind get clusters 2>/dev/null | grep -qx $(CLUSTER) || kind create cluster --config deploy/kind/cluster.yaml
	kind load docker-image vpp-fleet-simulator:dev vpp-ingestion:dev --name $(CLUSTER)
	helm upgrade --install $(RELEASE) deploy/chart -n $(NAMESPACE) --create-namespace --wait --timeout 5m
	@echo
	@echo "Platform is up. Inspect it with:"
	@echo "  make status"
	@echo "  kubectl -n $(NAMESPACE) port-forward svc/ingestion 8000:8000   # then curl localhost:8000/metrics"

down: ## Delete the kind cluster
	kind delete cluster --name $(CLUSTER)

status: ## Show pods and active faults
	kubectl -n $(NAMESPACE) get pods
	@$(FAULT) GET /faults

incident-load-surge: ## Inject a 10x telemetry surge at lugano-south for 180 s
	@$(FAULT) POST /faults/load-surge '{"site":"lugano-south","multiplier":10,"duration_seconds":180}'

incident-firmware-drift: ## Switch 40 % of como's chargers to firmware 2.4.0
	@$(FAULT) POST /faults/firmware-drift '{"site":"como","device_type":"charger","fraction":0.4}'

incident-clear: ## Switch every fault off
	@$(FAULT) DELETE /faults
