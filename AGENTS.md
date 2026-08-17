# AGENTS.md

Short contract for coding agents and humans editing this repository. Human process detail lives in
[`CONTRIBUTING.md`](CONTRIBUTING.md) — follow that for intake, design gates, and review disposition.

## Authority order

For public behavior, resolve in this order:

1. [`docs/adr/`](docs/adr/) — architecture decisions;
2. [`docs/INTERFACES.md`](docs/INTERFACES.md) — shared names and trust boundaries;
3. the code and the tests that lock it.

For exact wire shape and byte identity, [`schemas/`](schemas/) and [`fixtures/`](fixtures/) win over
prose. Start at [`docs/architecture.md`](docs/architecture.md) to find the owning module.

Do not invent behavior that contradicts those authorities. When behavior changes, update the ADR or
affected `docs/` page in the same change.

## Intake (required)

1. Search issues/PRs for duplicates.
2. Open an issue before coding; link it from the PR.
3. For design-gated areas (protocol, privacy/egress, storage/durability, release/packaging, ADR or
   `OPEN_QUESTIONS` flips), wait for maintainer acknowledgement on the issue before opening a PR.

## Local verification

```text
uv sync
uv run pytest <path-to-touched-tests>
```

Use Ruff and the pinned npm Pyright (`npx --no-install pyright`) from repository metadata. Prefer the
smallest relevant test slice.

For any packaged-resource inventory change, use the owning fixed-point command instead of running
its dependent generators individually:

```text
uv run python scripts/sync_resource_ripple.py --write
uv run python scripts/sync_resource_ripple.py --check
```

## Hard rules

- Do not weaken the honesty rules in [`CONTRIBUTING.md`](CONTRIBUTING.md). Coverage-bounded wording,
  idempotent retryable writes, no user-controlled content in structural tables/logs/errors,
  deterministic reproducibility, fenced advisory semantic output, and independently authorized
  network channels are load-bearing, not stylistic.
- Do not hand-edit generated or frozen artifacts (lock files, generated schema mirrors, release
  manifests); regenerate via owning scripts.
- Never copy gitignored local drafting inputs under `docs/architecture/` (or similar) into public
  files. Note that `docs/architecture.md` — the public overview — is a different, tracked file.
- Disposition every human and code-review-agent comment on a PR: fix it, or reply why it is invalid
  or out of scope. Silence is not merge-ready.
