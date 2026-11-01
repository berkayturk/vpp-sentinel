"""One incident, one model run: inject a runner, always leave a record.

``run_incident`` never raises. It ends in one of three states — ``reported``
(the model filed a report), ``incomplete`` (it stopped without one; its last
words are stored) or ``error`` (the model API failed; recorded on the
incident) — and the poller decides what happens next.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import structlog

from agent import metrics
from agent.context import IncidentContext, render_context
from agent.memory import IncidentStore
from agent.report import IncidentReport
from agent.tools import ToolSet

log = structlog.get_logger("agent.loop")

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
DEFAULT_MAX_ITERATIONS = 40
MAX_TOKENS = 16000
INCOMPLETE_HEADER = "# Incident report (incomplete)\n\nThe agent stopped before filing a report."

Status = Literal["reported", "incomplete", "error"]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def plus(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
        )


@dataclass(frozen=True)
class RunResult:
    final_text: str
    usage: Usage
    iterations: int


class ModelRunner(Protocol):
    async def run(self, system: str, user: str, tools: list, max_iterations: int) -> RunResult: ...


@dataclass(frozen=True)
class IncidentOutcome:
    status: Status
    report: IncidentReport | None
    final_text: str
    usage: Usage


class AnthropicRunner:
    """The production runner: the SDK's tool runner over our ``@beta_async_tool`` functions."""

    def __init__(self, client, model: str, effort: str = "high") -> None:
        self._client = client
        self._model = model
        self._effort = effort

    async def run(self, system: str, user: str, tools: list, max_iterations: int) -> RunResult:
        runner = self._client.beta.messages.tool_runner(
            model=self._model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=tools,
            messages=[{"role": "user", "content": user}],
            max_iterations=max_iterations,
            output_config={"effort": self._effort},
        )
        usage = Usage()
        final_text = ""
        iterations = 0
        async for message in runner:
            iterations += 1
            usage = usage.plus(_usage_of(message))
            final_text = _text_of(message) or final_text
        return RunResult(final_text, usage, iterations)


def load_system_prompt() -> str:
    return PROMPT_PATH.read_text()


async def run_incident(
    ctx: IncidentContext,
    toolset: ToolSet,
    runner: ModelRunner,
    store: IncidentStore,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> IncidentOutcome:
    started = time.monotonic()
    incident = toolset.deps.incident
    bound = log.bind(incident_id=incident.id, alert=ctx.alert.name)
    try:
        result = await runner.run(
            load_system_prompt(), render_context(ctx), toolset.tools, max_iterations
        )
    except Exception as exc:
        outcome_text = f"error: {_describe(exc)}"
        bound.error("incident.model_failed", error=outcome_text)
        await _record_quietly(store, incident.id, outcome_text, toolset)
        metrics.record_incident(ctx.alert.name, "error", time.monotonic() - started)
        return IncidentOutcome("error", None, "", Usage())
    for call in toolset.log:
        metrics.record_tool_call(call.tool, call.outcome)
    metrics.record_tokens(
        result.usage.input_tokens, result.usage.output_tokens, result.usage.cache_read_tokens
    )
    status: Status = "reported" if toolset.report is not None else "incomplete"
    if status == "incomplete":
        await _record_quietly(store, incident.id, "incomplete", toolset, result.final_text)
    metrics.record_incident(ctx.alert.name, status, time.monotonic() - started)
    bound.info(
        "incident.run_finished",
        status=status,
        iterations=result.iterations,
        tool_calls=len(toolset.log),
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
    )
    return IncidentOutcome(status, toolset.report, result.final_text, result.usage)


async def _record_quietly(
    store: IncidentStore, incident_id: int, outcome: str, toolset: ToolSet, final_text: str = ""
) -> None:
    try:
        if final_text or outcome == "incomplete":
            await store.save_report(
                incident_id, f"{INCOMPLETE_HEADER}\n\n{final_text}".rstrip() + "\n"
            )
        await store.record_action(
            incident_id,
            {"tool": "agent", "args": {}, "outcome": outcome, "at": toolset.deps.now().isoformat()},
        )
    except Exception as exc:  # the store is a boundary; the outcome still returns
        log.error("incident.record_failed", error=str(exc))


def _describe(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def _usage_of(message) -> Usage:
    usage = getattr(message, "usage", None)
    if usage is None:
        return Usage()
    return Usage(
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
        int(getattr(usage, "cache_read_input_tokens", 0) or 0),
    )


def _text_of(message) -> str:
    blocks = getattr(message, "content", None) or []
    return "\n".join(b.text for b in blocks if getattr(b, "type", "") == "text").strip()
