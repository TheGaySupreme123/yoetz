# Launch record — Grok easy-linking dogfood

| Field | Value |
| --- | --- |
| Launched (UTC) | 2026-07-27T16:43 approx |
| Branch | `codex/grok-easy-linking-dogfood-20260727` |
| Baseline | `3da640a9d4999d38149b2e996dc84ae87edc0295` |
| Host run dir | `<host-run-dir>` |
| Codex PID | see `codex.pid` in host run dir |
| Mode | auto (`--dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust --json`) |
| Model | gpt-5.6-luna @ high (codex-testing config) |
| CODEX_HOME | `<redacted-home>/.codex-testing` |
| Yoetz MCP | enabled `yoetz mcp serve` |

## Observers

1. **Agent quality** — writes `agent-quality-audit.md` + `agent-quality-live.md`
2. **Yoetz health** — writes `yoetz-health-audit.md` + `yoetz-health-live.md`

## How to follow later

```bash
# Is Codex still running?
kill -0 $(cat <host-run-dir>/codex.pid) && echo running || echo done

# Tail events
tail -f <host-run-dir>/codex-events.jsonl

# Read live observer logs
less docs/dogfood/2026-07-27-grok-easy-linking/agent-quality-live.md
less docs/dogfood/2026-07-27-grok-easy-linking/yoetz-health-live.md
```

