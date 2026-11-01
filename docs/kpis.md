# Measured KPIs

Measured on 2026-11-01 with `make capture` on a fresh kind cluster (`make down`, `make demo`, credentials, then this protocol), model `claude-opus-5-5`, agent image `vpp-agent:dev`. Sources for this table: fault injection times from the simulator's `fault.applied` log lines; alert firing times from Alertmanager's `startsAt`, stored on the incident row; incident opening from the agent's `incident.opened` log line; actions, reports and resolution from the `incidents` table; tokens and tool calls from the agent pod's Prometheus counters. `make capture` produced the run and the report file, but its own output for this run undercounted tokens and tool calls (it snapshotted the counters before the last scrape) and measured detection from the alert's pending time; the numbers here were recomputed from the sources above with the script's own `compute_kpis`, and the script has since been corrected.

| KPI | Load surge (restart) | Firmware drift (pull request) |
|---|---|---|
| Detection: fault injected → alert firing | 172.2 s | 194.8 s |
| Triage: alert firing → incident opened | 5.5 s | 11 s |
| Action: alert firing → allowlisted restart | 73 s | — |
| Pull request: alert firing → PR opened or updated | — | 137.9 s |
| Report: alert firing → report filed | 123.8 s | 170.7 s |
| Resolution: alert firing → alert resolved | 149.9 s | — (resolves when the fix is deployed or the fault is cleared) |

Token and tool-call counts are the agent pod's own totals: the pod was started fresh for this protocol and handled nothing else.

| Whole protocol | |
|---|---|
| Alerts fired → actions taken | 2/2 |
| Tool calls / refusals | 47 / 1 |
| Model tokens (input / output / cache read) | 588,816 / 22,324 / 0 |
| Cost at list prices (2026-11-01: input 4 USD, output 20 USD, cache read 0.2 USD per million tokens) | 2.80 USD |
