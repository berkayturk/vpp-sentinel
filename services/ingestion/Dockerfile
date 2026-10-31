# Build from the repository root:
#   docker build -f services/ingestion/Dockerfile -t vpp-ingestion:dev .
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
COPY services/fleet-simulator/pyproject.toml services/fleet-simulator/
COPY services/ingestion/ services/ingestion/
RUN mkdir -p services/fleet-simulator/src/fleet_simulator \
 && touch services/fleet-simulator/src/fleet_simulator/__init__.py \
 && uv sync --frozen --no-dev --package vpp-ingestion

FROM python:3.12-slim
WORKDIR /app
RUN useradd --system --uid 10001 --create-home app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/services/ingestion /app/services/ingestion
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MIGRATIONS_DIR=/app/services/ingestion/migrations \
    HTTP_PORT=8000
USER app
EXPOSE 8000
CMD ["python", "-m", "ingestion.main"]
