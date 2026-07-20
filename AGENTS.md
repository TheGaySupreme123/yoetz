# AGENTS.md

Short contract for coding agents and humans editing this repository. Human process detail lives in
[`CONTRIBUTING.md`](CONTRIBUTING.md) — follow that for intake, design gates, and review disposition.

## Authority order

For public behavior, resolve in this order:

1. [`docs/adr/`](docs/adr/) — architecture decisions;
2. [`specs/INTERFACES.md`](specs/INTERFACES.md) — shared names and trust boundaries;
3. the owning file under [`specs/`](specs/) for the concrete path (see
   [`specs/FILE_MANIFEST.md`](specs/FILE_MANIFEST.md)).

Do not invent behavior that contradicts those authorities. When behavior changes, update the owning
spec with the code.

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

Use Ruff and the pinned npm Pyright (`npx --no-install pyright`) from repository metadata. Prefer
the smallest relevant test slice.

## Hard rules

- Do not hand-edit generated or frozen artifacts (lock files, generated schema mirrors, release
  manifests); regenerate via owning scripts.
- Never copy ignored local drafting inputs under `docs/architecture/` (or similar) into public
  files.
- Disposition every human and code-review-agent comment on a PR: fix it, or reply why it is invalid
  or out of scope. Silence is not merge-ready.
