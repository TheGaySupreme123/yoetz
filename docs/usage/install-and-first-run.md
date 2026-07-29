# Install and first run

## Install

The supported install path is Python via [`uv`](https://docs.astral.sh/uv/):

```text
uv tool install --managed-python --python 3.14.6 "yoetz==0.1.0"
yoetz
```

`uvx yoetz` works for a one-off run. `npx yoetz` will delegate to the same `uvx` path once the
prepared launcher in [`support/npm-launcher/`](../../support/npm-launcher/) is published; it is
deliberately unpublished today, so npm is not currently an install route. The launcher pins the
exact Python distribution, passes arguments through unchanged, inherits stdio so the child sees
your real terminal, and propagates exit codes — including `128+n` for a signal. It installs
nothing itself: when `uv` is missing it prints the install command and stops.

Compatibility extras (the standard install already contains these exact dependencies):

| Extra | Adds |
|---|---|
| `semantic-openai` | Existing install-command alias for the HTTP client and OpenAI SDK |
| `portable-recovery` | Existing install-command alias for Argon2 recovery/passphrase support |

## First run

The first bare `yoetz` on an interactive terminal opens the full-screen interface in first-run
mode ([ADR-017](../adr/ADR-017-full-screen-terminal-interface.md), amending
[ADR-012](../adr/ADR-012-first-run-setup-wizard.md)). Every non-interactive invocation — CI,
pipes, redirected streams — prints help instead, exactly as before.

Setup is a linear path inside the interface, each finished step collapsing into a short line:

1. **Detection.** Supported harnesses (Codex in v0.1), your project and whether it is a Git
   repository, whether system secure storage is available, and whether Yoetz is connected yet.
2. **Which installation**, when several Codex binaries are found on your `PATH`, in the standard
   macOS Codex Desktop location, or in the Windows Store Codex App package. Friendly names lead;
   executable paths appear on selection and under `D`.
3. **Project trust.** The repository root and what project-local guidance and hooks are permitted
   to do. Starting in a subfolder is called out, with the root the trust applies to.
4. **The exact proposed change** — managed guidance and hooks, the `yoetz mcp serve` MCP
   registration, bounded structural event recording, and the approved-check policy digest — plus
   what will *not* happen. Nothing is applied before an explicit approval, and the approval is
   bound to the exact preview and policy digests that were displayed: if either has moved, the
   apply refuses as stale rather than proceeding.
5. **Installation activity**, with each step reported only once its postcondition was checked.
6. **Secure storage** — the system keyring, or a Yoetz passphrase.
7. **Review mode** — finish in complete local-only mode, or configure semantic review.
8. **Semantic setup, when selected** — provider/model, hidden API-key entry, all thirteen privacy
   answers, the exact disclosure preview, and the separately reauthenticated widening decision.
   The suggested first-run draft is **Metadata only**: structural context only, with a foreground
   confirmation before every provider request. This is a privacy-minimizing usable starting point,
   not consent; **Private** remains available for no network egress, and broader recipes remain
   explicit choices.
9. **Finish**, stating each readiness layer separately.

Credential status is presence-only. Human output shows the fixed mask `********` when the trusted
service confirms that the configured provider has a stored credential, `not stored` when absence
is confirmed, and `unknown` when the service or vault cannot answer. The mask is constant: it never
contains or encodes any character, length, prefix, suffix, or fingerprint of the API key.

`codex mcp get` runs first; an existing foreign entry is always preserved, never replaced, and
there is no force-replace option anywhere in the interface.

Local-only remains the safe default and needs no provider. Semantic review is available from the
same first-run flow when selected; setup is not marked complete if its provider credential or
privacy decision is incomplete.

The official Codex App exists on macOS and Windows. Linux setup uses the same flow for the
standalone Codex CLI and does not fabricate an app installation that OpenAI does not publish.

Re-run any time with `yoetz setup run` (the prompt-driven wizard, unchanged) or `/connect` in the
interface. Inspect posture read-only with `yoetz setup status`. Manage registration directly with
`yoetz integrate mcp status|preview|install`.

## Re-run or repair a ceremony

The wizard uses these same commands and trusted boundaries; each remains available directly:

```text
yoetz service run                  # foreground service under a supervisor you choose
yoetz privacy setup                # all 13 answers, exact preview, trusted decision
yoetz provider endpoint            # bind a reviewed preset or owner-declared HTTPS origin + model
yoetz provider credential set      # provision the API credential through the terminal ceremony
```

`yoetz service run` runs in the foreground on purpose when invoked directly — you choose the
supervisor (launchd, systemd, a terminal). Interactive setup may use the bounded on-demand launcher.
Related: `yoetz service status`, `lock`, `unlock`, `idle-relock`, `stop`.

## What a fresh installation does not do

An unconfigured installation is **zero-egress and deterministic**. It sends nothing anywhere. No
provider is bound, no credential exists, and semantic review is unavailable — checks run the
deterministic packs only and say so in their coverage vector.

That state is fully useful: the ledger, deterministic checks, findings, and receipts all work. You
opt into external review deliberately, or never.

## After setup

Bare `yoetz` opens the interface. Type `/` for commands — `/status` for layered readiness,
`/connect`, `/privacy`, `/provider`, `/service`, `/doctor`, `/work`, `/check`, `/receipt`.

Set `YOETZ_TUI=0`, or run in an installation without the rendering dependency, to get the
prompt-loop menu ([ADR-013](../adr/ADR-013-interactive-control-menu.md)) instead; it remains
supported and covers the same operations.

From here:

- [The terminal interface](terminal-interface.md) — the interface in detail.
- [The six operations](six-operations.md) — the actual workflow.
- [Privacy and semantic review](privacy-and-semantic-review.md) — before you enable any egress.
- [`docs/runbooks/codex-integration.md`](../runbooks/codex-integration.md) — integration detail and
  the exact tested Codex version set.
