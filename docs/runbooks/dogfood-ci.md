# Dogfood CI

`.github/workflows/dogfood.yml` runs an unattended product dogfood on GitHub-hosted runners: every
push to `main`, nightly, on demand, and on a pull request that carries the `dogfood` label. It exists because a full manual dogfood (isolated
instance, three hosts, three operating systems, evidence collection, cleanup) costs an afternoon,
so it happened rarely and late. This workflow keeps the *mechanics* under continuous observation
and leaves the judgment-heavy dogfood — new behaviour, influence, AI-powered review quality — to
the runbooks it links at the end.

## What green means

A green `dogfood-required` check means, for every (host, OS) cell: the candidate wheel built from
the exact revision installed into a pinned disposable instance; its service started, the vault
initialized, the Fireworks provider bound, a repository privacy grant was approved, the host
connected, observation consent was granted; a deterministic ledger probe (start, publish, check,
receipt) completed; hook carrier probes and the observation drain reached `drained` with nothing
pending; the service restarted, was unlocked, and observation state survived; and the instance
was disposed. With `DOGFOOD_FIREWORKS_API_KEY` present it additionally means the credential was
stored and the check reached a real AI-powered review attempt (any attempted status counts,
including a provider that rejects the model; only a pre-dispatch refusal such as
`not_configured` or `blocked_by_policy` is red). With the agent credential for that host present
it also means the native session finished and left no service or storage failure in the hook
diagnostics. Without those secrets the corresponding steps are recorded as skipped and the lane
is an install-and-mechanics smoke, not the full path.

It does **not** mean Yoetz is correct or useful. A native model that ignores the Yoetz tools,
times out, or exits nonzero is recorded in the lane report (`agent_ok: false`) and the lane stays
green unless the `strict_agent` input is set, because model compliance with a one-paragraph prompt
is not what this lane certifies. Read `lane-report.json` before quoting any cell.

## Matrix and connection modes

| OS cell | Runner | Codex CLI | Claude Code | Cursor Agent CLI |
|---|---|---|---|---|
| Linux x86-64 | `ubuntu-24.04` | `setup run --host codex` | `setup run --host claude` with the PAM review answered from a run-scoped runner password | `setup run --host cursor-cli`, same PAM review |
| macOS arm64 | `macos-15` | `setup run --host codex` | `integrate claude plugin export --development-enabled` + `claude --plugin-dir` (plugin-managed MCP) | `integrate cursor project-mcp install` only (MCP, no hooks) |
| Ubuntu 24.04 under WSL 2 | `windows-2025` + `Vampire/setup-wsl` | as Linux | as Linux | as Linux |

Why the macOS substitutes: Claude Code and Cursor plugin installation consume a
`plugin_artifact_apply` review that requires OS user presence — a LocalAuthentication prompt on
macOS, the account password through PAM on Linux and WSL 2 (see
[Claude Code integration](claude-code-integration.md) and [Cursor integration](cursor-integration.md)).
A hosted macOS runner cannot answer a LocalAuthentication dialog, so those two cells use the
documented no-authority substitutes and the lane report records `connection_mode` as
`plugin-dir` or `mcp-only`. The macOS Cursor cell therefore proves MCP only; its hooks are an
open gap. Codex needs no OS presence (`requires_os_presence: false`).

On Linux and WSL 2 the workflow gives the runner account a password generated for that run
(`chpasswd`), and the lane answers the PAM prompt through a real pseudo-terminal. That satisfies
the trusted-console checks rather than bypassing them: the console is the ingress, and the
account password is the authority. Nothing here is a supported product path for users — it is
contributor tooling against a throwaway account on a throwaway machine.

Native agent models: Codex runs on Fireworks through a custom Responses-wire `model_provider`
written into the run's `CODEX_HOME/config.toml`; Claude Code runs on Fireworks through its
Anthropic-compatible Messages endpoint (`ANTHROPIC_BASE_URL`); Cursor's CLI cannot use a custom
provider, so it uses the Cursor account behind `DOGFOOD_CURSOR_API_KEY` with the `cursor_model`
input (default `gpt-5.6-luna-low`, the lowest reasoning tier the CLI lists for that model).
AI-powered review inside Yoetz uses the reviewed `fireworks-responses` profile with the
`semantic_model` input (default `accounts/fireworks/models/glm-5p3-flash`).

## Lane phases

`scripts/dogfood_ci/lane.py` runs the cell; `scripts/dogfood_ci/ceremony.py` drives the
trusted-console ceremonies from a pseudo-terminal. Phases, in order, each recorded as a step:

1. **install** — `scripts/provision_test_instance.py create` (disposable, six-hour expiry,
   runtime-pinned root), `service isolation`, `instance status`, `version`, `service run`,
   `service initialize-passphrase`, `provider endpoint --provider fireworks`,
   `provider credential set`, then — with a credential — `service restart` plus
   `service unlock`, because the running service composes provider readiness only when it
   starts and a credential stored afterwards is verified live but not reflected in
   `provider status` until a restart (a lock/unlock does not refresh it either); then
   `privacy setup` (recipe 3, Assisted review, when a credential exists; Private otherwise),
   `provider status`, `setup status`.
2. **connect** — the host connection per the table, then `observe grant --workspace <project>`
   and `observe status`.
3. **ledger** — the host's session-start carrier first (`hooks observe`,
   `hooks claude-observe`, or `hooks cursor-observe`), whose auto-attach creates the
   workspace's task and names the session to attach to, exactly as a native host does; then
   `start` (attach to that session), `publish-work` (dry run, then real: one plan and one
   obligation), `status`, `check` (`semantic_required` with a credential),
   `service diagnostics --request-id <check>` plus every correlation id in the instance's
   diagnostics ring, `privacy receipts list`, `receipt` (markdown), the host's post-event
   carrier, `observe drain` (must reach `terminal: drained`, `pending_after: 0`),
   `observe status`. A task created before the carrier makes the carrier's
   `create_or_attach` refuse with `workspace_task_exists`, which is the product's rule against
   accidental sibling tasks, not a lane defect.
4. **native** — one headless agent session in the probe project (`codex exec`, `claude -p`,
   `cursor-agent -p`) asked to call `start`, `publish_work`, and `receipt` and answer `DONE`;
   then `observe status`, `observe drain`, `observe status`, `service status`. A
   `service_unavailable`, `storage_*`, or `vault_locked` hook diagnostic after the agent is
   catastrophic; a non-zero agent exit is not.
5. **lifecycle** — `service restart`, `service unlock` (headless runners have no keyring, so the
   restarted service comes back `locked`), one more `semantic_required` check on the probe
   session (informational, recorded as `semantic.after_restart`), `observe status` again.
6. **teardown** — `scripts/provision_test_instance.py dispose --retain-logs`, always.

Bounded waits poll `service status --json`; nothing sleeps to infer success.

## Secrets and environment

Create a repository environment named `dogfood` (the workflow references it; GitHub creates it
on first use) and add these secrets there or at repository level:

| Secret | Used for | When absent |
|---|---|---|
| `DOGFOOD_FIREWORKS_API_KEY` | AI-powered review credential; Codex and Claude agent model | credential and semantic steps are skipped, privacy recipe falls back to Private, Codex and Claude agent runs are skipped |
| `DOGFOOD_CURSOR_API_KEY` | Cursor Agent CLI | the Cursor agent run is skipped (the Cursor cell still proves install, connection, ledger, drain) |
| `DOGFOOD_R2_ACCOUNT_ID`, `DOGFOOD_R2_ACCESS_KEY_ID`, `DOGFOOD_R2_SECRET_ACCESS_KEY`, `DOGFOOD_R2_BUCKET` | Copy of every lane's evidence to Cloudflare R2 | evidence stays on the 30-day workflow artifact only |

The vault passphrase and the runner account password are generated per run inside the job and
never leave it. Secrets reach the lane only as environment variables of that one step; the lane
redacts every saved file against the secret values and against the runner's home, checkout,
instance base, and temp paths before writing it.

Cost: the Fireworks calls are a handful of small requests per cell; the Cursor run bills the
Cursor account for one short session per Linux/macOS/WSL cell. Runner minutes dominate; macOS
minutes carry GitHub's multiplier, and the repository is public.

## Evidence

Each cell uploads `dogfood-<host>-<os>` (30 days) containing:

- `lane-report.json` — `yoetz.dogfood-lane/1`: identity (source ref, package digest, host
  version, platform cell), `semantic` (`semantic_status`, `semantic_reason`, provenance
  presence), `agent` (exit, timeout, output digest, `mapping_present`), `observation` (per drain
  and status snapshot), every step with status, exit code, duration, and reason, and the
  `verdict`.
- `lane-summary.md` — the same steps as the table that also lands in the job summary.
- `NN-<step>.json` — the bounded stdout (parsed JSON where the command emitted it) and the last
  4 KiB of stderr per step, redacted.
- `agent-output.txt`, `agent-stderr.txt`, `agent-last-message.md` (Codex) — the native session's
  own output, redacted.
- `service.log`, `instance-logs/` — the instance's service output and retained logs.

With the R2 secrets, the same tree is copied to
`r2://<bucket>/dogfood/<yyyy-mm-dd>/<run_id>-<attempt>/<host>-<os>/`. Nothing reads that bucket
back; it is a durable place to look when a later question needs the history.

Reports contain no secrets by construction (redaction) but do contain the probe project's
relative structure, command names, bounded reason tokens, and the native agent's text. Treat the
bucket as private.

## Running it before merge

The workflow file must exist on `main` before `workflow_dispatch` can target any branch. To
exercise a branch earlier, add the `dogfood` label to its pull request: the `pull_request`
trigger then runs the full matrix on that head (and again on each push while the label stays).
Pull requests without the label skip both lane matrices, and `dogfood-required` reports the skip
without failing.

## Running a lane yourself

From a clean checkout on macOS or Linux, with `uv`, Node, and the host CLI installed:

```text
export FIREWORKS_API_KEY=...                     # optional
export DOGFOOD_OS_PASSWORD=...                   # Linux only, your account password, optional
uv run --no-project --python 3.14.6 python scripts/dogfood_ci/lane.py \
    --host codex --tag dfl --evidence ~/.yz-dogfood-evidence --host-path "$(command -v codex)"
```

The lane provisions `~/.yz-instances/<tag>` and disposes it at the end; it never touches the
everyday installation (ADR-028 runtime pin). Use a fresh `--host-config-root` if you do not want
your own `~/.codex`, `~/.claude`, or `~/.cursor` to receive the connection. `--connection-mode`
forces one of `setup-run`, `plugin-dir`, `mcp-only`, or `none`; `--skip-agent` and
`--skip-restart` shorten a run. A lane must never be pointed at `/tmp` (the runtime refuses the
symlinked path) or at a base inside a repository.

## Known gaps and follow-ups

- macOS Claude and Cursor cells do not exercise the marketplace/plugin install path (OS presence).
  A self-hosted macOS runner with an unlocked session could close this; not planned.
- Codex hooks in `codex exec` run only with `--dangerously-bypass-hook-trust`; interactive hook
  trust is a per-user Codex decision that a runner cannot make, so the lane passes that flag and
  records it. Hook evidence from the Codex cell therefore proves the Yoetz ingress, not Codex's
  trust flow.
- The Cursor CLI has no supported custom-provider option, so its cell costs Cursor credits.
- WSL 2 is exercised here for the first time in CI; the certified cells (ADR-007) are unchanged,
  and the runbook for [Linux and WSL](linux-and-wsl.md) remains the authority on what is claimed.
- The lane uses the strict/policy route `policy` so the agent route can dispatch AI-powered
  review; a cell with no Fireworks credential still connects with that route but records
  `semantic_status: not_configured`.
- Model output is bounded by the prompt, not by turn or budget flags (Claude's current CLI has no
  turn cap); the agent step has a wall-clock timeout instead.

## See also

- [Independent test instances](test-instances.md) — the provisioning contract the lane builds on.
- [Codex dogfood parity](codex-dogfood.md) — the stricter manual gate for Codex worktree runs.
- [AI-powered review dogfood](semantic-dogfood.md) — profiles and the provenance gate this lane
  reports against.
- [Influence dogfood](influence-dogfood.md) — what this lane deliberately does not measure.
