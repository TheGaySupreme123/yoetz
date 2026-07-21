# Yoetz

Yoetz is being designed as a public, local-first, open-source system for recording structured
work evidence, checking it deterministically, and producing honest receipts about what was and was
not verified.

This repository is currently in the **specification phase**. It intentionally contains no product
implementation yet. The design is being frozen in natural language first, with one Markdown owner
for every planned source file, schema, fixture, test, script, workflow, resource, and public
document.

Start here:

- [`specs/README.md`](specs/README.md) — how to read the specification tree and its current status;
- [`specs/FILE_MANIFEST.md`](specs/FILE_MANIFEST.md) — exhaustive one-to-one future-file ownership;
- [`specs/INTERFACES.md`](specs/INTERFACES.md) — shared types, constants, ports, and trust boundaries;
- [`specs/OPEN_QUESTIONS.md`](specs/OPEN_QUESTIONS.md) — resolved founder decisions plus empirical
  and independent-review gates still required before release;
- [`docs/adr/`](docs/adr/) — public architecture decisions;
- [`PRIVACY.md`](PRIVACY.md), its future-file owner
  [`specs/repository/PRIVACY.md`](specs/repository/PRIVACY.md), and
  [`docs/adr/ADR-009-data-egress-privacy.md`](docs/adr/ADR-009-data-egress-privacy.md) — the
  user-facing privacy promise and its enforceable technical boundary.

## Getting started

The supported install path is Python via [`uv`](https://docs.astral.sh/uv/):

```text
uv tool install --managed-python --python 3.14.6 "yoetz==0.1.0"
yoetz
```

(`uvx yoetz` works for a one-off run. `npx yoetz` will delegate to the same `uvx` path once the
prepared npm launcher in [`support/npm-launcher/`](support/npm-launcher/) is published; it is
deliberately unpublished today.)

The first bare `yoetz` on an interactive terminal starts a **setup wizard**
([ADR-012](docs/adr/ADR-012-first-run-setup-wizard.md)). It discovers installed Codex CLI
binaries on your PATH (showing a choice when several exist), previews and — only after your
explicit confirmation — registers `yoetz mcp serve` with the chosen Codex
(`codex mcp get` first; an existing foreign entry is always preserved, never replaced), checks
whether the local service is reachable, and prints the exact next commands for the parts that
stay deliberately human-driven:

1. `yoetz service run` — start the persistent local service under a supervisor you choose;
2. `yoetz privacy setup` — review recipes, provider binding, and egress policy (zero-egress
   until you commit otherwise);
3. `yoetz provider credential set` — provision the LLM API credential through the confidential
   terminal ceremony (never a flag, file, or environment variable).

Re-run any time with `yoetz setup run`; inspect posture read-only with `yoetz setup status`;
manage registration directly with `yoetz integrate codex mcp status|preview|install`. Every
non-interactive bare invocation (CI, pipes) still prints help.

The working v0.1 architecture uses one trusted persistent local service. CLI, MCP, and future UI
processes are communication surfaces; they do not own encryption keys, decrypted state, storage
writers, privacy authority, or provider access. External disclosure is denied by default and must
pass centralized classification, policy, minimization, secret scanning, exact destination binding,
and durable structural audit.

Yoetz works with any agent over MCP. A host needs no integration, no installed skill, and no
configuration: it gets the six operations, a short set of always-delivered instructions, and the
same guidance documents every harness ships, fetchable on demand. Codex is the first harness with a
first-party integration because its skill surface delivers that guidance natively — the guidance
itself is harness-neutral, owned once under `guidance/`, and shipped byte-identically everywhere.
Harness integration is a port with Codex as its first adapter, so a fork can make Yoetz first-party
on another harness by adding an adapter and a profile, without touching the core
([`docs/adr/ADR-010`](docs/adr/ADR-010-harness-integration-port.md)). Integration buys ergonomics,
never a stronger claim: an agent publishing over MCP earns the weakest honest coverage, and the
coverage vector says so exactly.

Two defaults are deliberately separate. An unconfigured installation stays zero-egress and
deterministic. When a technical user chooses external semantic review, the CLI's recommended
`assisted-review` recipe shows and confirms a standing workspace policy that sends the reviewer a
useful structured packet: goal, obligations, claims, material timeline, deterministic findings and
their exact bases, coverage gaps, and bounded problem-local evidence/test/diff/source excerpts
already recorded in the case. Sensitive/confidential content is off, never-send remains absolute,
and the recipe is recommended only for an exact endpoint profile with a current data-use record
stating training `prohibited`, retention `none|bounded`, and provider human access
`prohibited|restricted`. Known-broad, unknown, or stale posture removes the recommendation.

Inside that confirmed policy, review is direct-to-agent: the reviewer returns a bounded challenge
to the main agent, which can act, provide evidence, revise its claim, dispute with evidence, or state
an unresolved limitation, then recheck. Routine checks and retries need no human prompt. Users can
choose stricter, broader, custom, or forked behavior; changed forks do not automatically inherit
upstream privacy/support claims.

Private strategy/architecture drafting inputs under `docs/architecture/` are intentionally ignored.
The public ADRs and `specs/` tree must remain self-contained without them.

## Contributing

Contributions are welcome with a high bar: search for duplicates, open an issue first, wait for
maintainer acknowledgement on design-gated areas, update owning specs with behavior changes, and
disposition every review comment (including code-review agents) before merge. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) and [`AGENTS.md`](AGENTS.md).

- Ordinary bugs and change requests: GitHub issues (use the forms; blank issues are disabled).
- Security: [`SECURITY.md`](SECURITY.md) — private vulnerability reporting or `security@yoetz.dev`.
- Conduct: [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — `conduct@yoetz.dev`.

Licensed under the [Apache License 2.0](LICENSE), using the official unmodified license text and
the SPDX expression `Apache-2.0`; Yoetz does not add a fabricated project-wide ownership notice.
