# Build from the repository root:
#   docker build -f services/fleet-simulator/Dockerfile -t vpp-fleet-simulator:dev .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
COPY services/ingestion/pyproject.toml services/ingestion/
COPY services/fleet-simulator/ services/fleet-simulator/
RUN mkdir -p services/ingestion/src/ingestion \
 && touch services/ingestion/src/ingestion/__init__.py \
 && uv sync --frozen --no-dev --package vpp-fleet-simulator

FROM python:3.12-slim
WORKDIR /app
RUN useradd --system --uid 10001 --create-home app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/services/fleet-simulator /app/services/fleet-simulator
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CONTROL_PORT=8080
USER app
EXPOSE 8080
CMD ["python", "-m", "fleet_simulator.main"]
