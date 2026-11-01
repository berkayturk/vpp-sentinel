# vpp-sentinel

Platform health and incident agent for distributed energy fleets.

_Work in progress — see `docs/architecture.md` for what is built so far._

## Prerequisites

- Docker (Desktop or Engine)
- [kind](https://kind.sigs.k8s.io/), `kubectl`, [Helm 3](https://helm.sh/)
- [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv on demand)
- For the incident agent (optional — without it the platform and dashboards still run):
  - `ANTHROPIC_API_KEY` in your environment (an Anthropic API key; one incident run
    costs roughly 1–3 USD on Claude Opus)
  - `GITHUB_TOKEN`: a fine-grained personal access token for the repository the
    agent should open pull requests on, with *Contents: write* and
    *Pull requests: write*; set `agent.repo` in `deploy/chart/values.yaml` to your
    fork. The agent never merges; enable branch protection with Code Owner review
    on `main` so `CODEOWNERS` is enforced.

`make demo` stores both values in a Kubernetes Secret in the cluster; they are never
written to the repository.

## Run

| Command | What it does |
|---|---|
| `make demo` | Builds the images, creates the `vpp-sentinel` kind cluster if needed and deploys the observability stack and the platform |
| `make status` | Lists pods, active faults and active alerts |
| `make grafana` | Opens Grafana on `localhost:3000` (admin / `vpp-demo-password`) |
| `make alerts` | Lists active alerts |
| `make agent-logs` | Follows the incident agent's log: incidents, tool calls, reports |
| `make incident-load-surge` | One site publishes telemetry 50× faster for 180 s |
| `make incident-firmware-drift` | 40 % of one site's chargers switch to a newer firmware revision |
| `make incident-clear` | Switches every fault off |
| `make down` | Deletes the kind cluster |

Developer loop: `make test`, `make lint` and `make alerts-test` (alert rules
are unit-tested with promtool).

## The incident agent

When an alert fires, the agent assembles a sanitised, read-only context
(timing, blast radius, severity, incident memory, logs, runbook), investigates
with ten purpose-built tools, and always files a report. It may restart the
one deployment named in `allowlist.yaml`, and for a code-level cause it opens
a pull request on `agent/<fingerprint>` that it can never merge. Every
boundary is enforced in the tools and covered by a test; there is no shell or
kubectl tool, and the tests the model writes run in a separate container
with no secrets. `docs/architecture.md` has the details.

The `incidents` table ships with one seeded historical incident so a first run
can show the memory lookup; it is marked as seeded in the migration.

## A note on the planted fault

The platform ships with one deliberate code-level defect. The incident
scenarios above expose it, and the incident agent is expected to find it
from the observability signals alone and propose the fix as a pull request.
This README does not say what the defect is; the agent's report will.
