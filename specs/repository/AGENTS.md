# AGENTS.md — short contract for coding agents and humans

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** `specs/README.md`,
`repository/CONTRIBUTING.md`, `FILE_MANIFEST.md`, `INTERFACES.md`
**Imported by:** coding agents, contributors, and maintainers working in this repository

## Purpose

Provide a short, enforceable contract for agents and humans editing this tree: where authority
lives, which process gates apply, which commands to run, and which boundaries must never be
crossed. Human process detail remains in `CONTRIBUTING.md`; this file must stay short.

## Public surface

The document must cover:

- public authority order for behavioral changes;
- issue-first intake and design-gate reminder (pointer to `CONTRIBUTING.md`);
- local verification commands (`uv`, focused pytest, pinned lint/type tools);
- prohibition on hand-editing generated or frozen artifacts;
- public/private boundary (`docs/architecture/` and other ignored drafting inputs);
- pointer to `CONTRIBUTING.md` as the human process source of truth.

## Behavior

Authority for public behavior is:

1. `docs/adr/` for architecture decisions;
2. `specs/INTERFACES.md` for shared names and trust boundaries;
3. the owning file under `specs/` for the concrete future path (see `specs/FILE_MANIFEST.md`).

Agents must not invent behavior that contradicts those authorities. When behavior changes, update
the owning spec with the code.

Intake follows `CONTRIBUTING.md`: search for duplicates, open an issue first, wait for maintainer
acknowledgement on design-gated areas (protocol, privacy/egress, storage/durability, release/
packaging, ADR or `OPEN_QUESTIONS` flips), then open a PR that links the issue and completes the
pull-request template.

Default local loop:

```text
uv sync
uv run pytest <path-to-touched-tests>
```

Use Ruff and the pinned npm Pyright (`npx --no-install pyright`) as declared by repository
metadata. Prefer the smallest relevant test slice over the whole suite.

Do not hand-edit generated or frozen artifacts (lock files, generated schema mirrors, release
manifests); regenerate them through their owning scripts. Do not copy ignored local drafting
inputs under `docs/architecture/` (or similar) into public files.

Every human and code-review-agent comment on a PR must be fixed or answered before merge, per
`CONTRIBUTING.md`.

## Errors and edge cases

- Coding around a missing or ambiguous spec without updating the owning file is incomplete.
- Opening a PR without a linked issue is incomplete.
- Leaking private drafting material into public paths is a boundary failure.

## Invariants

1. Specs and ADRs govern behavior; this file only points agents at them.
2. `CONTRIBUTING.md` owns the human contribution process.
3. Generated and frozen artifacts are regenerated, never hand-patched.
4. Public files stay free of ignored private drafting inputs.

## Tests

- `tests/packaging.md` — public boundary and contributor-facing docs consistency when packaging
  checks cover repository root docs.

## Open questions

None.
