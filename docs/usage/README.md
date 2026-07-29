# Using Yoetz

Task-oriented guides. For the system's shape see [`docs/architecture.md`](../architecture.md); for
the decisions behind it see [`docs/adr/`](../adr/).

- [Install and first run](install-and-first-run.md) — install, first run, starting the service,
  what a fresh installation will and will not do.
- [The terminal interface](terminal-interface.md) — the full-screen interface, its status
  vocabulary, keys, and slash commands.
- [The six operations](six-operations.md) — `start`, `publish_work`, `check`, `respond`, `status`,
  `receipt`, end to end.
- [Privacy and semantic review](privacy-and-semantic-review.md) — the zero-egress default, the
  policy profiles, and what changes when you turn external review on.
- [Auto-approving an MCP route](auto-approving-agents.md) — the host-declared strict route that
  cannot request external semantic review.
- [Providers and credentials](providers.md) — reviewed presets, owner-declared endpoints, and the
  credential ceremony.
- [Receipts and coverage](receipts-and-coverage.md) — how to read a receipt and why the wording is
  deliberately narrow.

Agent-facing guidance is separate and lives in [`guidance/`](../../guidance/). It ships
byte-identically to every harness and over MCP, so it is the authority for how an agent should
behave; these pages are for the human operating the installation.
