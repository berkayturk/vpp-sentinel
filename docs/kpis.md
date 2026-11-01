# Measured KPIs

Measured on 2026-11-01 with `make capture` on a fresh kind cluster (`make down`, `make demo`, credentials, then this protocol), model `claude-opus-5-5`, agent image `vpp-agent:dev`. Timestamps come from Alertmanager (the moment an alert began firing, stored on the incident), the `incidents` table (actions, reports, resolution) and the capture script's own clock (fault injection, incident opening — from the agent's own `incident.opened` log line for this run).

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
