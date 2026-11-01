from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agent.report import ActionRecord, IncidentReport, render_markdown

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


def _report(**overrides) -> IncidentReport:
    fields = dict(
        summary="como chargers went dark after a firmware change",
        started_at=NOW,
        blast_radius="como/charger: 12 of 30 missing",
        hypothesis="messages from firmware 2.4.0 are rejected by the parser",
        evidence=("rejects_by_reason_firmware rose to 2 msg/s for firmware 2.4.0",),
        verification=("regression test added", "ingestion test suite passed"),
        actions_taken=(ActionRecord(tool="run_tests", args={}, outcome="passed", at=NOW),),
        recommended_action="merge the pull request after review",
        seen_before=False,
        mitigation_not_fix=False,
        page_human=True,
    )
    fields.update(overrides)
    return IncidentReport(**fields)


def test_report_requires_a_hypothesis():
    with pytest.raises(ValidationError):
        _report(hypothesis="")


def test_report_is_frozen():
    r = _report()
    with pytest.raises(ValidationError):
        r.summary = "x"  # type: ignore[misc]


def test_render_markdown_has_every_section():
    text = render_markdown(_report(pull_request_url="https://github.com/x/y/pull/7"))
    for heading in (
        "## Summary",
        "## Blast radius",
        "## Hypothesis",
        "## Evidence",
        "## Verification",
        "## Actions taken",
        "## Recommended action",
        "## Seen before",
        "## Pull request",
    ):
        assert heading in text
    assert "run_tests" in text and "passed" in text
    assert "This is a mitigation, not a fix" not in text
    assert "A human was paged" in text


def test_render_marks_mitigations_and_unknown_history():
    text = render_markdown(
        _report(mitigation_not_fix=True, seen_before=True, seen_before_reference="#1 on 2026-10-18")
    )
    assert "This is a mitigation, not a fix" in text
    assert "#1 on 2026-10-18" in text
