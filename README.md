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

The working v0.1 architecture uses one trusted persistent local service. CLI, MCP, and future UI
processes are communication surfaces; they do not own encryption keys, decrypted state, storage
writers, privacy authority, or provider access. External disclosure is denied by default and must
pass centralized classification, policy, minimization, secret scanning, exact destination binding,
and durable structural audit.

Private strategy/architecture drafting inputs under `docs/architecture/` are intentionally ignored.
The public ADRs and `specs/` tree must remain self-contained without them.

Licensed under the [Apache License 2.0](LICENSE), using the official unmodified license text and
the SPDX expression `Apache-2.0`; Yoetz does not add a fabricated project-wide ownership notice.
