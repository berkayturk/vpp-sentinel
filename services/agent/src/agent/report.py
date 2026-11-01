"""The incident report: the one output the agent always produces (spec §7.3, Tier 1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool: str
    args: dict[str, Any]
    outcome: str
    at: datetime


class IncidentReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str = Field(min_length=1)
    started_at: datetime
    blast_radius: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    evidence: tuple[str, ...]
    verification: tuple[str, ...]
    actions_taken: tuple[ActionRecord, ...] = ()
    recommended_action: str = Field(min_length=1)
    seen_before: bool
    seen_before_reference: str = ""
    mitigation_not_fix: bool
    page_human: bool
    pull_request_url: str | None = None


def render_markdown(report: IncidentReport) -> str:
    """The stored ``report_md`` and the pull-request body."""
    seen = report.seen_before_reference or "yes" if report.seen_before else "no"
    lines = [
        f"# Incident report ({report.started_at.isoformat()})",
        "",
        "## Summary",
        report.summary,
        "",
        "## Blast radius",
        report.blast_radius,
        "",
        "## Hypothesis",
        report.hypothesis,
        "",
        "## Evidence",
        *(_bullets(report.evidence) or ["- none recorded"]),
        "",
        "## Verification",
        *(_bullets(report.verification) or ["- none recorded"]),
        "",
        "## Actions taken",
        *(_actions(report.actions_taken) or ["- none"]),
        "",
        "## Recommended action",
        report.recommended_action,
        "",
        "## Seen before",
        seen,
    ]
    if report.mitigation_not_fix:
        lines += ["", "**This is a mitigation, not a fix.**"]
    if report.page_human:
        lines += ["", "**A human was paged** (severity gate)."]
    if report.pull_request_url:
        lines += ["", "## Pull request", report.pull_request_url]
    return "\n".join(lines) + "\n"


def _bullets(items: tuple[str, ...]) -> list[str]:
    return [f"- {item}" for item in items]


def _actions(actions: tuple[ActionRecord, ...]) -> list[str]:
    return [f"- {a.at.isoformat()} `{a.tool}` {a.args or ''} → {a.outcome}" for a in actions]
