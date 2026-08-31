# Using Yoetz

Task-oriented guides. For the system's shape see [`docs/architecture.md`](../architecture.md); for
the decisions behind it see [`docs/adr/`](../adr/).

- [Install and first run](install-and-first-run.md) — install, first run, starting the service,
  what a fresh installation will and will not do.
- [Agent start](agent-start.md) — the same installation addressed to a coding agent: what it runs
  itself, what it asks the user, and where it hands over the terminal.
- [The terminal interface](terminal-interface.md) — the full-screen interface, its status
  vocabulary, keys, and slash commands.
- [The six operations](six-operations.md) — `start`, `publish_work`, `check`, `respond`, `status`,
  `receipt`, end to end.
- [Importing bounded Codex JSONL](importing-codex-jsonl.md) — stage, review, authorize, and resume
  one exact local `codex exec --json` import.
- [Privacy and semantic review](privacy-and-semantic-review.md) — the zero-egress default, the
  policy profiles, and what changes when you turn external review on.
- [Auto-approving an MCP route](auto-approving-agents.md) — the host-declared strict route that
  cannot request external semantic review.
- [Providers and credentials](providers.md) — reviewed presets, owner-declared endpoints, and the
  credential ceremony.
- [Receipts and coverage](receipts-and-coverage.md) — how to read a receipt and why the wording is
  deliberately narrow.

Agent-facing *runtime* guidance is separate and lives in [`guidance/`](../../guidance/). It ships
byte-identically to every harness and over MCP, so it is the authority for how an agent should
behave once Yoetz is running. Its `request-templates.md` fallback keeps requests authorable when a
host drops schema metadata. These pages are for the human operating the installation, with one
exception: [Agent start](agent-start.md) addresses the agent performing an installation, before any
of that guidance is reachable.
