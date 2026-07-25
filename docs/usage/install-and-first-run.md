# Install and first run

## Install

The supported install path is Python via [`uv`](https://docs.astral.sh/uv/):

```text
uv tool install --managed-python --python 3.14.6 "yoetz==0.1.0"
yoetz
```

`uvx yoetz` works for a one-off run. `npx yoetz` will delegate to the same `uvx` path once the
prepared launcher in [`support/npm-launcher/`](../../support/npm-launcher/) is published; it is
deliberately unpublished today, so npm is not currently an install route.

Optional extras:

| Extra | Adds |
|---|---|
| `semantic-openai` | HTTP client and OpenAI SDK for provider dispatch |
| `portable-recovery` | Argon2 support for portable key recovery |

## First run: the setup wizard

The first bare `yoetz` on an interactive terminal starts the setup wizard
([ADR-012](../adr/ADR-012-first-run-setup-wizard.md)). Every non-interactive invocation — CI, pipes
— prints help instead.

The wizard:

1. Lists automatically detected supported harnesses (Codex in v0.1) and asks which one to connect.
2. Asks which installation to use when several Codex CLI binaries are found on your `PATH`, in the
   standard macOS Codex Desktop location, or in the Windows Store Codex App package.
3. Previews the MCP registration and — only after an explicit `Y` — registers `yoetz mcp serve` with
   the chosen Codex. It runs `codex mcp get` first; an existing foreign entry is always preserved,
   never replaced.
4. Checks whether the local service is reachable.
5. Optionally collects a reviewed nonsecret provider preset or a custom HTTPS origin and model
   binding. This writes `config.toml`. It never writes secrets.

The official Codex App exists on macOS and Windows. Linux setup uses the same wizard for the
standalone Codex CLI and does not fabricate an app installation that OpenAI does not publish.

Re-run any time with `yoetz setup run`. Inspect posture read-only with `yoetz setup status`. Manage
registration directly with `yoetz integrate mcp status|preview|install`.

## What stays human-driven

The wizard deliberately stops short of these and prints the exact next commands:

```text
yoetz service run                  # start the persistent local service under a supervisor you choose
yoetz privacy setup                # review recipes, provider binding, and egress policy
yoetz provider endpoint            # bind a reviewed preset or owner-declared HTTPS origin + model
yoetz provider credential set      # provision the API credential through the terminal ceremony
```

`yoetz service run` runs in the foreground on purpose — you choose the supervisor (launchd, systemd,
a terminal). Related: `yoetz service status`, `lock`, `unlock`, `idle-relock`, `stop`.

## What a fresh installation does not do

An unconfigured installation is **zero-egress and deterministic**. It sends nothing anywhere. No
provider is bound, no credential exists, and semantic review is unavailable — checks run the
deterministic packs only and say so in their coverage vector.

That state is fully useful: the ledger, deterministic checks, findings, and receipts all work. You
opt into external review deliberately, or never.

## After setup

Bare `yoetz` opens the interactive menu ([ADR-013](../adr/ADR-013-interactive-control-menu.md)),
including LLM endpoint binding under **LLM provider**. From here:

- [The six operations](six-operations.md) — the actual workflow.
- [Privacy and semantic review](privacy-and-semantic-review.md) — before you enable any egress.
- [`docs/runbooks/codex-integration.md`](../runbooks/codex-integration.md) — integration detail and
  the exact tested Codex version set.
