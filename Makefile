SERVICES := services/fleet-simulator services/ingestion

.PHONY: test lint fmt demo down status incident-load-surge incident-firmware-drift incident-clear

test: ## Run every service's test suite with coverage
	@for s in $(SERVICES); do echo "==> $$s"; uv run --directory $$s pytest -q || exit 1; done

lint: ## Static checks: ruff lint and format check
	uv run --with ruff ruff check .
	uv run --with ruff ruff format --check .

fmt: ## Auto-format with ruff
	uv run --with ruff ruff format .
	uv run --with ruff ruff check --fix .

demo: ## Create the kind cluster and deploy the platform
	@echo "implemented in Task 9"; exit 1

down: ## Delete the kind cluster
	@echo "implemented in Task 9"; exit 1

status: ## Show pods and active faults
	@echo "implemented in Task 9"; exit 1

incident-load-surge: ## Inject a telemetry load surge at one site
	@echo "implemented in Task 9"; exit 1

incident-firmware-drift: ## Switch part of one site's chargers to drifted firmware
	@echo "implemented in Task 9"; exit 1

incident-clear: ## Switch every fault off
	@echo "implemented in Task 9"; exit 1
