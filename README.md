# vpp-sentinel

Platform health and incident agent for distributed energy fleets.

_Work in progress — see `docs/superpowers/specs/` for the design and
`docs/architecture.md` for what is built so far._

## Prerequisites

- Docker (Desktop or Engine)
- [kind](https://kind.sigs.k8s.io/), `kubectl`, [Helm 3](https://helm.sh/)
- [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv on demand)

## Run

| Command | What it does |
|---|---|
| `make demo` | Builds the images, creates the `vpp-sentinel` kind cluster if needed and deploys the platform |
| `make status` | Lists pods and shows the active faults |
| `make incident-load-surge` | One site publishes telemetry 10× faster for 180 s |
| `make incident-firmware-drift` | 40 % of one site's chargers switch to a firmware whose payload the ingestion parser rejects |
| `make incident-clear` | Switches every fault off |
| `make down` | Deletes the kind cluster |

Developer loop: `make test` and `make lint`.
