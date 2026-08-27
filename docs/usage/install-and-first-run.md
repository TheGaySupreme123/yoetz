# Install and first run

> Local Cursor support is configured explicitly, not by ambient discovery. Use
> `yoetz integrate cursor plugin preview` with an exact isolated Cursor configuration root and
> project; see [the Cursor integration runbook](../runbooks/cursor-integration.md). Cursor Cloud is
> not supported.

## Install

The supported install path is Python via [`uv`](https://docs.astral.sh/uv/):

```text
uv tool install --managed-python --python 3.14.6 "yoetz==0.1.0"
yoetz
```

`uvx yoetz` works for a one-off run. If you are a coding agent installing Yoetz on a user's
behalf, follow [Agent start](agent-start.md) instead — setup's questions require the human's own
terminal. With `uv` already installed, `npx yoetz` delegates to the same exact-version `uvx` path.
The launcher pins the
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

1. **Detection.** Supported harnesses (Codex in v0.1), your project and its canonical Git common
   root (or resolved non-Git directory), whether system secure storage is available, and whether
   Yoetz is connected yet.
2. **Which installation**, when several Codex binaries are found on your `PATH`, in the standard
   macOS Codex Desktop location, or in the Windows Store Codex App package. Friendly names lead;
   executable paths appear on selection and under `D`.
3. **Project trust.** The repository root and what project-local guidance and hooks are permitted
   to do. Starting in a subfolder is called out, with the root the trust applies to.
4. **The exact proposed change** — a discoverable project skill under `.agents/skills/yoetz`,
   managed plugin/hook source files under `.agents/plugins/yoetz`, the `yoetz mcp serve` MCP
   registration, bounded structural event recording, and the approved-check policy digest — plus
   what will *not* happen. Nothing is applied before an explicit approval, and the approval is
   bound to the exact preview and policy digests that were displayed: if either has moved, the
   apply refuses as stale rather than proceeding.
5. **Installation activity**, with each step reported only once its postcondition was checked.
6. **Secure storage** — the system keyring, or a Yoetz passphrase.
7. **Review mode** — finish in complete local-only mode, or configure semantic review.
8. **Semantic setup, when selected** — provider/model, hidden API-key entry, then one exact
   recommended privacy policy. **Assisted review** is recommended only for an exact provider route
   with current reviewed no-training evidence and retention no longer than 30 days; it is bounded
   to the current repository and does not re-prompt for ordinary attempts after approval. Branches
   and linked worktrees share that grant; an independent clone does not. Otherwise
   **Private** is recommended. Accepting it asks nothing
   further; declining it opens the named recipes, and only **Custom** opens the settings
   themselves, in five grouped sections. The exact disclosure and separately reauthenticated
   widening decision remain mandatory.
9. **Finish**, stating each readiness layer separately. When the durable privacy policy permits
   package update checks (product default: on) and a newer `yoetz` release is available on PyPI,
   the finish screen appends a short tip with the installed version, available version, and the
   exact upgrade command `uv tool upgrade yoetz`. Work receipts never carry update metadata.

On later interactive launches, the resume tip may show the same package-update advisory instead of
the generic Codex tip when a newer package is available. `/doctor` reports the package line as
optional with the upgrade command when a newer release is known, or notes that a check could not
be completed when policy allowed the check but the registry was unreachable.

When re-running setup or `/connect` with the same installed package version and a newer release
available, Yoetz offers **upgrade first** or **continue with this version** rather than reinstalling
the same bits. Continuing still adds or repairs harness integration (project skill + structural
plugin sources + MCP) without a package reinstall. Yoetz never auto-upgrades.

Upgrading replaces package bytes without rewriting accepted machine privacy-policy bytes. Eligible
legacy authority may be automatically narrowed onto a bounded pre-upgrade repository route when its
trusted locator next arrives; when no route existed, one first-repository carry-forward is available.
Neither case asks again because no new repository gains authority. Every later repository remains
Private until approved.

**Network honesty:** zero-egress for task content remains the product promise. Structural package
version checks are opt-out network (disable `update_checks` in privacy setup). Non-interactive CLI,
MCP, CI, and pipes do not open that path as a surprise.

Credential status is presence-only. Human output shows the fixed mask `********` when the trusted
service confirms that the configured provider has a stored credential, `not stored` when absence
is confirmed, and `unknown` when the service or vault cannot answer. The mask is constant: it never
contains or encodes any character, length, prefix, suffix, or fingerprint of the API key.
When a credential is already stored for the exact provider/model, setup asks whether to reuse it
(the default) or replace it through a new hidden-input ceremony.

`codex mcp get` runs first; an existing foreign entry is always preserved, never replaced, and
there is no force-replace option anywhere in the interface.

Setup reports these activation layers independently. `installed_exact` at
`.agents/skills/yoetz` proves the reviewed project skill bytes are present, not that a running Codex
session loaded or followed them. `.agents/plugins/yoetz` proves only that Yoetz's managed plugin and
hook source files are present. Codex plugin activation requires a separate standing-trust flow,
which setup offers only through an exact digest-bound preview and explicit approval. The preview is
bound to the selected executable and its SHA-256, an explicitly selected existing absolute,
non-symlink Codex home, the executable's network-free `--version` result, repository marketplace and
home-config preimages/proposals, managed source digest, versioned cache target/digest, and the exact
post-consent `plugin list --marketplace yoetz --json` and `plugin add yoetz@yoetz --json` commands.
Before approval, the version probe forces both Codex home variables to a fresh owner-private
temporary home and removes its scratch afterward; it does not inspect inventory in the selected
home. After approval, both variables are forced to that selected home for list/add, whose possible
scratch, cache, config, and marketplace effects are included in the disclosed activation boundary.
Setup reports `active` only when canonical Codex
inventory says the repository plugin is installed and enabled and its versioned cache is byte-exact
to the managed source, in addition to exact marketplace/config state. MCP `registered` proves only
the separate configuration entry. Neither active inventory nor MCP registration proves a later
session loaded a hook or delivered an observation. A stopped service can prevent a later MCP call,
but it cannot explain a session that never discovered the skill or attempted a Yoetz tool.

Two different things are called a default here, and only one of them is a policy. The **seeded
policy** is `local_only`: every installation starts with external LLM disclosure denied, and
nothing moves it without a provider binding, a stored credential, and a separately reauthenticated
policy commit. The
**recommended answer** to first run's "How should Yoetz review work?" is semantic review, because
an installation that never reaches it can only ever report deterministic coverage. Accepting the
recommendation opens those steps; it does not perform them, and local-only needs no provider and
stays one keystroke away. Setup is not marked complete if a chosen semantic path's provider
credential or privacy decision is incomplete.

The official Codex App exists on macOS and Windows. Linux setup uses the same flow for the
standalone Codex CLI and does not fabricate an app installation that OpenAI does not publish.

Re-run any time with `yoetz setup run` (the prompt-driven wizard, unchanged) or `/connect` in the
interface. Change privacy any time with `yoetz --privacy`. Inspect posture read-only with
`yoetz setup status`. Manage registration directly with `yoetz integrate codex mcp
status|preview|preview-remove|install|remove`. Reverse marketplace activation with
`yoetz integrate codex plugin preview|remove` (cache purge is default-off). See the
[Codex integration runbook](../runbooks/codex-integration.md) removal section.

## Re-run or repair a ceremony

The wizard uses these same commands and trusted boundaries; each remains available directly:

```text
yoetz service run                  # foreground service under a supervisor you choose
yoetz --privacy                    # recommended policy first; customize only when declined
yoetz privacy setup                # equivalent long-form command
yoetz provider endpoint            # bind a reviewed preset or owner-declared HTTPS origin + model
yoetz provider credential set      # provision the API credential through the terminal ceremony
```

`yoetz service run` runs in the foreground on purpose when invoked directly — you choose the
supervisor (launchd, systemd, a terminal). Interactive setup may use the bounded on-demand launcher.
Related: `yoetz service status`, `lock`, `unlock`, `idle-relock`, `stop`.

## What a fresh installation does not do

An unconfigured installation is **provider-egress-free and deterministic**. No task content is sent
to an external provider; the separately configurable structural package update check may still be
enabled. No provider is bound, no credential exists, and semantic review is unavailable — checks
run the deterministic packs only and say so in their coverage vector.

That state is fully useful: the ledger, deterministic checks, findings, and receipts all work. You
opt into external review deliberately, or never.

## After setup

Bare `yoetz` opens the interface. Type `/` for commands — `/status` for layered readiness,
`/connect`, `/privacy`, `/provider`, `/service`, `/doctor`, `/work`, `/check`, `/receipt`.

Set `YOETZ_TUI=0`, or run in an installation without the rendering dependency, to get the
prompt-loop menu ([ADR-013](../adr/ADR-013-interactive-control-menu.md)) instead; it remains
supported and covers the same operations.

From here:

- [Agent start](agent-start.md) — this same setup from the installing agent's side.
- [The terminal interface](terminal-interface.md) — the interface in detail.
- [The six operations](six-operations.md) — the actual workflow.
- [Privacy and semantic review](privacy-and-semantic-review.md) — before you enable any egress.
- [`docs/runbooks/codex-integration.md`](../runbooks/codex-integration.md) — integration detail and
  the exact tested Codex version set.
