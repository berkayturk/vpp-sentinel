# Build from the repository root:
#   docker build -f services/agent/Dockerfile -t vpp-agent:dev .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
COPY services/fleet-simulator/pyproject.toml services/fleet-simulator/
COPY services/ingestion/pyproject.toml services/ingestion/
COPY services/agent/ services/agent/
RUN mkdir -p services/fleet-simulator/src/fleet_simulator services/ingestion/src/ingestion \
 && touch services/fleet-simulator/src/fleet_simulator/__init__.py \
          services/ingestion/src/ingestion/__init__.py \
 && uv sync --frozen --no-dev --package vpp-agent

FROM python:3.12-slim
WORKDIR /app
# git for the Tier 3 workspace clone; uv to run the ingestion test suite inside it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --create-home app \
 && mkdir -p /workspace && chown app:app /workspace
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/services/agent /app/services/agent
COPY --chown=app:app allowlist.yaml /app/allowlist.yaml
COPY --chown=app:app docs/runbooks /app/docs/runbooks
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HTTP_PORT=8001 \
    WORKSPACE_DIR=/workspace \
    RUNBOOKS_DIR=/app/docs/runbooks \
    ALLOWLIST_PATH=/app/allowlist.yaml
USER app
EXPOSE 8001
CMD ["python", "-m", "agent.main"]
