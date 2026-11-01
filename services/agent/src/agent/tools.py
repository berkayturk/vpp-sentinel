"""The ten tools the model can call, each enforcing its own boundary in code.

There is no shell, no kubectl and no merge tool: a tool that was never
defined needs no guard. Every tool returns a string; refusals start with
``REFUSED``, failures with ``ERROR``, and nothing raises into the model loop.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx
from anthropic import beta_async_tool

from agent.clients import github, kube, loki, prometheus
from agent.clients.errors import ClientError
from agent.fingerprint import slug
from agent.memory import Incident, IncidentStore
from agent.policy import Allowlist, PathGuard, PathRefused
from agent.report import ActionRecord, IncidentReport, render_markdown
from agent.sanitize import sanitize_text
from agent.workspace import Workspace, WorkspaceError

TOOL_NAMES = (
    "query_prometheus",
    "query_logs",
    "lookup_incident_memory",
    "list_source_files",
    "read_source_file",
    "restart_deployment",
    "write_source_file",
    "run_tests",
    "open_pull_request",
    "file_report",
)
RECORDED_TOOLS = frozenset(
    {"restart_deployment", "write_source_file", "run_tests", "open_pull_request", "file_report"}
)
MAX_LOG_LINES = 200
MAX_MINUTES_BACK = 24 * 60
ATTRIBUTION = re.compile(r"^.*(co-authored-by:|generated with).*$", re.IGNORECASE | re.MULTILINE)
BRANCH_PREFIX = "agent/"


@dataclass(frozen=True)
class ToolDeps:
    prometheus: httpx.AsyncClient
    loki: httpx.AsyncClient
    kube: httpx.AsyncClient
    github: httpx.AsyncClient
    store: IncidentStore
    workspace: Workspace
    policy: Allowlist
    guard: PathGuard
    incident: Incident
    now: Callable[[], datetime]
    repo: str
    base_branch: str = "main"
    salt: bytes | None = None


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict[str, Any]
    outcome: str
    at: datetime


@dataclass
class ToolSet:
    """The runner-facing tools plus the immutable record of what they did."""

    deps: ToolDeps
    log: tuple[ToolCall, ...] = ()
    report: IncidentReport | None = None
    tests_passed: bool = False
    _tools: list = field(default_factory=list, repr=False)

    @property
    def tools(self) -> list:
        return self._tools

    def names(self) -> tuple[str, ...]:
        return tuple(t.name for t in self._tools)

    # --- read tools -------------------------------------------------------------

    async def query_prometheus(self, expr: str, minutes_back: int = 10) -> str:
        """Run a PromQL instant query now and at ``minutes_back`` minutes ago.

        Args:
            expr: The PromQL expression.
            minutes_back: How far back the second sample is taken (1–1440).
        """
        minutes = min(max(int(minutes_back), 1), MAX_MINUTES_BACK)
        now = self.deps.now()
        try:
            current = await prometheus.instant(self.deps.prometheus, expr, at=now)
            earlier = await prometheus.instant(
                self.deps.prometheus, expr, at=now - timedelta(minutes=minutes)
            )
        except ClientError as exc:
            return self._done("query_prometheus", {"expr": expr}, f"ERROR: {exc}")
        text = f"now: {_samples(current)}\n{minutes} min ago: {_samples(earlier)}"
        return self._done("query_prometheus", {"expr": expr}, text)

    async def query_logs(self, logql: str, minutes_back: int = 10, limit: int = 100) -> str:
        """Read log lines from Loki for the last ``minutes_back`` minutes (sanitised).

        Args:
            logql: The LogQL selector and pipeline.
            minutes_back: Window length in minutes (1–1440).
            limit: Maximum lines (1–200).
        """
        minutes = min(max(int(minutes_back), 1), MAX_MINUTES_BACK)
        end = self.deps.now()
        try:
            lines = await loki.query_range(
                self.deps.loki,
                logql,
                end - timedelta(minutes=minutes),
                end,
                min(max(int(limit), 1), MAX_LOG_LINES),
            )
        except ClientError as exc:
            return self._done("query_logs", {"logql": logql}, f"ERROR: {exc}")
        text = "\n".join(ln.line for ln in lines) or "(no lines)"
        return self._done("query_logs", {"logql": logql}, text)

    async def lookup_incident_memory(self, alertname: str) -> str:
        """Earlier incidents with this alert name, newest first, with their outcomes.

        Args:
            alertname: The Prometheus alert name.
        """
        try:
            incidents = await self.deps.store.recent_like(alertname)
        except Exception as exc:  # the store is a boundary; report, never raise
            return self._done("lookup_incident_memory", {"alertname": alertname}, f"ERROR: {exc}")
        if not incidents:
            text = f"No earlier incident with alert name {alertname}."
        else:
            text = "\n".join(
                f"#{i.id} started {i.started_at.isoformat()} outcome={i.outcome or 'unknown'}"
                f" actions={[a.get('tool') for a in i.actions_taken]}\n{i.report_md}"
                for i in incidents
            )
        return self._done("lookup_incident_memory", {"alertname": alertname}, text)

    async def list_source_files(self, directory: str) -> str:
        """List files in a repository directory (only under services/ingestion).

        Args:
            directory: Repository-relative directory path.
        """
        try:
            path = self.deps.guard.resolve(directory)
            entries = await self.deps.workspace.list(path)
        except PathRefused as exc:
            return self._done("list_source_files", {"directory": directory}, f"REFUSED: {exc}")
        except WorkspaceError as exc:
            return self._done("list_source_files", {"directory": directory}, f"ERROR: {exc}")
        return self._done(
            "list_source_files", {"directory": directory}, "\n".join(entries) or "(empty)"
        )

    async def read_source_file(self, path: str) -> str:
        """Read a repository file (only under services/ingestion).

        Args:
            path: Repository-relative file path.
        """
        try:
            content = await self.deps.workspace.read(self.deps.guard.resolve(path))
        except PathRefused as exc:
            return self._done("read_source_file", {"path": path}, f"REFUSED: {exc}")
        except WorkspaceError as exc:
            return self._done("read_source_file", {"path": path}, f"ERROR: {exc}")
        return self._done("read_source_file", {"path": path}, content)

    # --- action tools (Tier 2) ---------------------------------------------------

    async def restart_deployment(self, name: str) -> str:
        """Rollout-restart a deployment listed in allowlist.yaml, at most once per cooldown.

        Args:
            name: Deployment name in the platform namespace.
        """
        policy = self.deps.policy
        decision = policy.restart_allowed(policy.restart_namespace, name)
        if not decision.allowed:
            return await self._record(
                "restart_deployment", {"name": name}, f"REFUSED: {decision.reason}"
            )
        last = await self.deps.store.last_restart_at(self.deps.incident.fingerprint)
        now = self.deps.now()
        if last is not None and now - last < policy.cooldown:
            remaining = policy.cooldown - (now - last)
            reason = (
                f"REFUSED: cooldown — {name} was restarted at {last.isoformat()};"
                f" {int(remaining.total_seconds() // 60)} min remain."
                " Do not retry; report and escalate."
            )
            return await self._record("restart_deployment", {"name": name}, reason)
        try:
            await kube.restart_deployment(self.deps.kube, policy.restart_namespace, name, now)
        except ClientError as exc:
            return await self._record("restart_deployment", {"name": name}, f"ERROR: {exc}")
        return await self._record("restart_deployment", {"name": name}, "ok", stored_outcome="ok")

    # --- code tools (Tier 3) -----------------------------------------------------

    async def write_source_file(self, path: str, content: str) -> str:
        """Write a repository file (only under services/ingestion). Returns ok or REFUSED.

        Args:
            path: Repository-relative file path.
            content: The complete new file content.
        """
        try:
            await self.deps.workspace.write(self.deps.guard.resolve(path), content)
        except PathRefused as exc:
            return await self._record("write_source_file", {"path": path}, f"REFUSED: {exc}")
        except WorkspaceError as exc:
            return await self._record("write_source_file", {"path": path}, f"ERROR: {exc}")
        self.tests_passed = False
        return await self._record("write_source_file", {"path": path}, f"ok: wrote {path}")

    async def run_tests(self) -> str:
        """Run the ingestion service's test suite in the workspace; returns the output tail."""
        result = await self.deps.workspace.run_tests()
        self.tests_passed = result.passed
        verdict = "passed" if result.passed else "failed"
        return await self._record(
            "run_tests", {}, f"{verdict}\n{result.output}", stored_outcome=verdict
        )

    async def open_pull_request(self, title: str, body: str) -> str:
        """Push the workspace changes to a branch and open (or update) a pull request. Never merges.

        Args:
            title: Pull request title (imperative, one line).
            body: Pull request description; the incident report is appended by the tool.
        """
        args = {"title": title}
        if not self.deps.policy.pull_requests_enabled:
            return await self._record(
                "open_pull_request", args, "REFUSED: pull requests are disabled"
            )
        if not self.tests_passed:
            return await self._record(
                "open_pull_request", args, "REFUSED: run_tests must pass after the last write"
            )
        changed = await self.deps.workspace.changed_paths()
        if not changed:
            return await self._record("open_pull_request", args, "REFUSED: no changes to push")
        decision = self.deps.guard.check_diff(changed)
        if not decision.allowed:
            return await self._record("open_pull_request", args, f"REFUSED: {decision.reason}")
        branch = BRANCH_PREFIX + slug(self.deps.incident.fingerprint)
        clean_title = _strip_attribution(title).strip()
        clean_body = _strip_attribution(body).strip()
        try:
            await self.deps.workspace.commit_and_push(
                branch, f"fix(ingestion): {clean_title}", changed
            )
            existing = await github.find_open_pr(self.deps.github, self.deps.repo, branch)
            if existing is None:
                pr = await github.create_pr(
                    self.deps.github,
                    self.deps.repo,
                    clean_title,
                    clean_body,
                    branch,
                    self.deps.base_branch,
                )
            else:
                pr = await github.update_pr(
                    self.deps.github, self.deps.repo, existing.number, clean_title, clean_body
                )
        except (WorkspaceError, ClientError) as exc:
            return await self._record("open_pull_request", args, f"ERROR: {exc}")
        self._pull_request_url = pr.url
        return await self._record(
            "open_pull_request", args, f"ok: {pr.url}", stored_outcome=f"ok: {pr.url}"
        )

    # --- report (Tier 1) ---------------------------------------------------------

    async def file_report(
        self,
        summary: str,
        blast_radius: str,
        hypothesis: str,
        evidence: list[str],
        verification: list[str],
        recommended_action: str,
        seen_before: bool,
        mitigation_not_fix: bool,
        page_human: bool,
        seen_before_reference: str = "",
    ) -> str:
        """File the incident report. Call this exactly once, as the last step.

        Args:
            summary: What happened, in two or three sentences.
            blast_radius: Which sites, device groups and counts were affected.
            hypothesis: The most likely cause, stated as a hypothesis.
            evidence: Metric values and log lines that support the hypothesis.
            verification: What was checked to confirm or rule out causes.
            recommended_action: What a human should do next.
            seen_before: Whether incident memory holds a similar incident.
            mitigation_not_fix: True when an action taken only mitigates.
            page_human: True when a human must look now.
            seen_before_reference: The earlier incident's id and date, if any.
        """
        try:
            report = IncidentReport(
                summary=summary,
                started_at=self.deps.incident.started_at,
                blast_radius=blast_radius,
                hypothesis=hypothesis,
                evidence=tuple(evidence),
                verification=tuple(verification),
                actions_taken=tuple(
                    ActionRecord(tool=c.tool, args=c.args, outcome=c.outcome, at=c.at)
                    for c in self.log
                    if c.tool in RECORDED_TOOLS
                ),
                recommended_action=recommended_action,
                seen_before=seen_before,
                seen_before_reference=seen_before_reference,
                mitigation_not_fix=mitigation_not_fix,
                page_human=page_human,
                pull_request_url=getattr(self, "_pull_request_url", None),
            )
        except ValueError as exc:
            return await self._record("file_report", {}, f"ERROR: invalid report: {exc}")
        self.report = report
        try:
            await self.deps.store.save_report(self.deps.incident.id, render_markdown(report))
        except Exception as exc:
            return await self._record(
                "file_report", {}, f"ERROR: could not store the report: {exc}"
            )
        return await self._record("file_report", {}, "ok: report filed", stored_outcome="ok")

    # --- plumbing ----------------------------------------------------------------

    def _done(self, tool: str, args: dict[str, Any], outcome: str) -> str:
        clean = sanitize_text(outcome, self.deps.salt)
        self.log = (*self.log, ToolCall(tool, args, _short(clean), self.deps.now()))
        return clean

    async def _record(
        self, tool: str, args: dict[str, Any], outcome: str, stored_outcome: str | None = None
    ) -> str:
        clean = self._done(tool, args, outcome)
        action = {
            "tool": tool,
            "args": args,
            "outcome": stored_outcome or _short(clean),
            "at": self.deps.now().isoformat(),
        }
        try:
            await self.deps.store.record_action(self.deps.incident.id, action)
        except Exception:  # memory is best effort; the tool result still returns
            pass
        return clean


def make_tools(deps: ToolDeps) -> ToolSet:
    """Build the tool set for one incident; the model sees exactly ``TOOL_NAMES``."""
    toolset = ToolSet(deps)
    toolset._tools = [beta_async_tool(getattr(toolset, name)) for name in TOOL_NAMES]
    return toolset


def _samples(samples: tuple) -> str:
    if not samples:
        return "no data"
    return "; ".join(
        f"{{{', '.join(f'{k}={v}' for k, v in sorted(s.labels.items()))}}} {s.value:g}"
        for s in samples
    )


def _strip_attribution(text: str) -> str:
    return ATTRIBUTION.sub("", text)


def _short(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
