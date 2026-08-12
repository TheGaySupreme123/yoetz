# Yoetz

Yoetz is a local-first, open-source system for recording structured work evidence, checking it
deterministically, and producing **honest receipts about what was and was not verified**.

It is built for agent-assisted work. An agent publishes bounded facts about what it is doing — plan,
claims, actions, results, evidence — into a local ledger; Yoetz checks that record with versioned
deterministic policy packs, optionally adds advisory semantic review inside a privacy policy you
control, and issues a receipt whose wording never outruns its coverage.

The thing Yoetz refuses to do is the point. It will not tell you work is correct. It will tell you
exactly what was checked, at what coverage, and what remains open.

## Install

```text
uv tool install --managed-python --python 3.14.6 "yoetz==0.1.0"
yoetz
```

The supported install path is Python via [`uv`](https://docs.astral.sh/uv/); `uvx yoetz` works for a
one-off run.

`yoetz` at a terminal opens a full-screen interface, and the first run walks setup inside it:
what was detected, whether you trust this project, the exact proposed change, and an explicit
approval before anything is applied. You do not need to know what MCP, hooks, policy digests, or
vaults are to finish it, and you are never asked to configure a provider — local verification is
complete without one.

Everything non-interactive is unchanged. Pipes, redirects, CI, `yoetz --help`, `--json` output,
named subcommands, and `yoetz mcp serve` behave exactly as before; a bare `yoetz` with a
redirected stream still prints help. Set `YOETZ_TUI=0` for the prompt-loop menu instead.

Full walkthrough: [Install and first run](docs/usage/install-and-first-run.md) and
[The terminal interface](docs/usage/terminal-interface.md). A coding agent installing Yoetz for
its user should follow [Agent start](docs/usage/agent-start.md): setup's questions appear only on
the human's own terminal, and that page says what to run, what to ask, and what to recommend.

For Codex observation, source files and configuration are not activation proof. Setup offers one
standing-trust preview bound to the exact selected Codex executable and an explicitly selected,
existing Codex home. Before consent, only that executable's `--version` runs, with both Codex home
variables redirected to a fresh owner-private temporary home that is removed afterward; setup does
not inspect the approved home's plugin inventory. Only explicit digest-bound approval permits the
scoped inventory/add commands and disclosed marketplace, config, scratch, and versioned-cache
effects in the selected home. Even an `active` result proves installed inventory and
cache/config state for future sessions—not that a later session loaded a hook or delivered an
observation.

## The six operations

`start`, `publish_work`, `check`, `respond`, `status`, `receipt` — identical contracts on the CLI and
over MCP. Everything else (import, review, backup/restore/migrate, integration, version, service) is
a bounded support surface, not a seventh operation.

Yoetz works with any agent over MCP with no integration, no installed skill, and no configuration.
Codex is the first harness with a first-party integration because its skill surface delivers the
guidance natively — but the guidance is harness-neutral, owned once under [`guidance/`](guidance/),
and shipped byte-identically everywhere. Integration buys ergonomics, never a stronger claim.

See [The six operations](docs/usage/six-operations.md).

## Two defaults, deliberately separate

A fresh installation's unconfigured seed is **zero-egress and deterministic**: nothing leaves your
machine before first-run setup commits a policy, and Yoetz is fully useful in that state. Setup's
proposed privacy policy states whether Yoetz may check PyPI for package updates (default yes, with an
opt-out). That bounded check carries only the `yoetz` package identity and version, never task or
user content, and it never upgrades the package for you; decline it for a zero-network installation.
Rerunning setup does not suspend or revoke an existing standing policy: ordinary activity remains
governed by that policy until the user commits a replacement.

External semantic review is a separate explicit decision. When you choose it, the CLI's recommended
`assisted-review` recipe shows and confirms a standing policy that sends the reviewer a structured
packet built from the ledger — goal, obligations, claims, timeline, deterministic findings and their
bases, coverage gaps, and bounded problem-local excerpts already recorded in the case. Sensitive and
confidential content is off, the never-send set is absolute, and only a reauthenticated local human
can loosen policy.

Review then runs direct-to-agent: the reviewer returns a bounded challenge, the agent acts, supplies
evidence, revises, disputes, or states a limitation, and rechecks. No human prompt for routine
retries.

See [Privacy and semantic review](docs/usage/privacy-and-semantic-review.md) and
[`PRIVACY.md`](PRIVACY.md).

## How it is put together

One trusted persistent local service owns the encryption keys, decrypted state, storage writers,
privacy authority, and provider access. CLI, MCP, and the terminal interface are clients — they
hold none of those things. The interface in particular is presentation only: it dispatches through
the same application services the commands do, and no secret ever enters it, because credential
entry suspends the interface and hands the terminal to the existing confidential ceremony.
External disclosure is denied by default and must pass centralized classification, policy,
minimization, secret scanning, exact destination binding, and durable structural audit.

See [Architecture](docs/architecture.md).

## Documentation

- [Using Yoetz](docs/usage/) — install, the terminal interface, operations, privacy, providers,
  receipts.
- [Architecture](docs/architecture.md) — topology, module map, honesty rules.
- [`docs/adr/`](docs/adr/) — architecture decisions; the top authority for public behavior.
- [`docs/INTERFACES.md`](docs/INTERFACES.md) — shared names, types, ports, trust boundaries.
- [`docs/OPEN_QUESTIONS.md`](docs/OPEN_QUESTIONS.md) — decisions taken, and the gates still open
  before release claims can strengthen.
- [`docs/`](docs/) — full index, including protocol pages and runbooks.

## Status

v0.1, pre-release; the first release will be a **public alpha**. Every public claim in
[`docs/public-claims.json`](docs/public-claims.json) is bound to real checked-in evidence and is
currently flagged `not_yet_evidenced` — no release has shipped, so no release-gated claim is
asserted. Every reviewed provider preset resolves to a real runtime factory, so a preset you can
select is a preset Yoetz can dispatch — but none of the non-official presets has recorded live
evidence yet, so none is claimed as a confirmed working endpoint. That claim stays gated by the
capability evidence described in [ADR-006](docs/adr/ADR-006-semantic-provider-profile.md).

Two independent threat reviews — of the key hierarchy and recovery design, and of the service,
credential, and egress boundaries — are open release gates and have **not** been completed. Codex
integration likewise ships with an empty tested-version set, so it is recorded as untested rather
than supported. `docs/OPEN_QUESTIONS.md` is the full list of what still gates a stronger claim.

## Contributing

Contributions are welcome with a high bar: search for duplicates, open an issue first, wait for
maintainer acknowledgement on design-gated areas, and disposition every review comment — including
code-review agents — before merge. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`AGENTS.md`](AGENTS.md).

- Bugs and change requests: GitHub issues (use the forms; blank issues are disabled).
- Security: [`SECURITY.md`](SECURITY.md) — private vulnerability reporting or `security@yoetz.dev`.
- Conduct: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — `conduct@yoetz.dev`.

Private strategy and architecture drafting inputs under `docs/architecture/` are intentionally
gitignored. The public ADRs, docs, code, and tests must remain self-contained without them.

Licensed under the [Apache License 2.0](LICENSE), using the official unmodified license text and the
SPDX expression `Apache-2.0`; Yoetz does not add a fabricated project-wide ownership notice.
