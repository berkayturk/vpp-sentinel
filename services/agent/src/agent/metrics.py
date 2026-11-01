"""Prometheus metrics the agent exposes about itself."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

INCIDENTS_TOTAL = Counter(
    "vpp_agent_incidents_total",
    "Incidents the agent handled, by alert name and outcome.",
    ["alertname", "outcome"],
)
TOOL_CALLS_TOTAL = Counter(
    "vpp_agent_tool_calls_total",
    "Tool calls the model made, by tool and outcome class.",
    ["tool", "outcome"],
)
MODEL_TOKENS_TOTAL = Counter(
    "vpp_agent_model_tokens_total",
    "Tokens exchanged with the model.",
    ["kind"],
)
INCIDENT_SECONDS = Histogram(
    "vpp_agent_incident_seconds",
    "Wall time from the start of the agent run to the filed report.",
    buckets=(10, 30, 60, 120, 300, 600, 1200),
)

OUTCOME_CLASSES = ("ok", "refused", "error")


def record_incident(alertname: str, outcome: str, seconds: float) -> None:
    INCIDENTS_TOTAL.labels(alertname, outcome).inc()
    INCIDENT_SECONDS.observe(max(0.0, seconds))


def record_tokens(input_tokens: int, output_tokens: int, cache_read_tokens: int) -> None:
    MODEL_TOKENS_TOTAL.labels("input").inc(input_tokens)
    MODEL_TOKENS_TOTAL.labels("output").inc(output_tokens)
    MODEL_TOKENS_TOTAL.labels("cache_read").inc(cache_read_tokens)


def record_tool_call(tool: str, outcome_text: str) -> None:
    TOOL_CALLS_TOTAL.labels(tool, outcome_class(outcome_text)).inc()


def outcome_class(outcome_text: str) -> str:
    """Collapse a tool's free-text outcome to a bounded label value."""
    if outcome_text.startswith("REFUSED"):
        return "refused"
    if outcome_text.startswith("ERROR"):
        return "error"
    return "ok"
