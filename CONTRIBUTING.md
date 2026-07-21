# Contributing to Yoetz

Thank you for considering a contribution. External work is welcome when it meets a high bar: this
project is **open with rigor**, not invitation-only and not closed to contributions.

Yoetz is spec-first: the authoritative description of every future file lives under
[`specs/`](specs/), one Markdown owner per file, at the mirrored path
(`specs/src/yoetz/protocol/canonical.md` for `src/yoetz/protocol/canonical.py`). Read
[`specs/README.md`](specs/README.md) before your first change — it explains the method, the public
authority order (`docs/adr/` → `specs/INTERFACES.md` → the owning file spec), and the honesty rules
that bind every spec. Agents and humans also share the short contract in [`AGENTS.md`](AGENTS.md).

## Before you write code

1. **Search for duplicates** in issues and pull requests. Do not open a second thread for the same
   problem.
2. **Open an issue first** using the bug report or change request form. A pull request without a
   linked issue is incomplete and may be closed or converted to an issue.
3. **Wait for acknowledgement** when the work is design-gated. Design-gated areas are:
   - protocol contracts;
   - privacy and data-egress behavior;
   - storage and durability;
   - release pipelines and packaging surfaces;
   - new ADRs or flips in [`specs/OPEN_QUESTIONS.md`](specs/OPEN_QUESTIONS.md).

   Maintainer acknowledgement on the issue is required before you open a PR in those areas. Docs
   clarifications, typo fixes, and narrowly scoped bugfixes outside those areas may proceed once the
   issue exists.
4. Find the owning spec for the file you want to change in
   [`specs/FILE_MANIFEST.md`](specs/FILE_MANIFEST.md).
5. If your change affects public behavior — a new field, a changed error code, a different check
   outcome — **update the spec before or alongside the code**. Coding around an ambiguity instead of
   fixing the spec creates exactly the drift this method exists to prevent.
6. Use the exact names already registered in [`specs/INTERFACES.md`](specs/INTERFACES.md) for
   anything shared across modules; add a name there first if it doesn't exist yet.

## Local setup

This project uses `uv` for environment, lock, and build management, pinned per
[`pyproject.toml`](pyproject.toml). Typical loop:

```text
uv sync
uv run pytest <path-to-touched-tests>
```

The pinned toolchain is Ruff for lint/format and the official npm-distributed Pyright
(`npx --no-install pyright`) in strict mode for type checking; Node/npm are contributor and CI
prerequisites only, never an end-user runtime requirement.

## Making a change

- Make the smallest change that satisfies the spec.
- Run the tests for the area you touched, not the whole suite by default:
  - [`tests/unit.md`](specs/tests/unit.md) for the touched module family;
  - [`tests/conformance.md`](specs/tests/conformance.md) when your change crosses a
    runtime/storage boundary;
  - [`tests/packaging.md`](specs/tests/packaging.md) when your change affects release artifacts or
    public metadata.
- Avoid mixing unrelated behavioral changes into one patch.
- Keep release-related and security-sensitive changes clearly labeled in your commit/PR
  description.
- Preserve the public/private boundary: nothing under `docs/architecture/` or any other
  ignored local drafting input belongs in a public file.

## Pull request checklist

Use the repository pull request template. Every PR must:

1. Link the prior issue (`Fixes #N` or `Refs #N`).
2. Confirm duplicate search and, for design-gated work, maintainer acknowledgement on that issue.
3. List owning spec paths updated when behavior changes.
4. Paste the verification commands you ran.
5. Confirm the public/private boundary check.
6. Disposition every review comment before merge (see below).

## Review comment disposition

Every human reviewer comment and every code-review-agent comment must be handled before merge:

- **Fix it**, or
- **Reply** explaining why the comment is invalid or out of scope.

Silence, ignoring a thread, or “will do later” without maintainer agreement is not merge-ready.

## What not to do

- Don't open a PR without a linked issue and duplicate search.
- Don't hand-edit a generated or frozen artifact (a lock file, a generated schema mirror, a release
  manifest) — regenerate it through its owning script.
- Don't invent a second formatter, linter, or type-checker configuration; the pinned tools in
  `pyproject.toml` and the development-only `package.json` are the only ones this project uses.
- Don't add a new distribution surface without its own spec and design-gate acknowledgement. The
  end-user install path is Python/`uv` (see [`README.md`](README.md)); the one reviewed exception
  is the delegation-only npm launcher at `support/npm-launcher/` (ADR-012), which must stay
  unpublished (`"private": true`) — publishing it is a separate deliberate release decision,
  never a side effect of another change.

## Security and conduct

Please don't file a security vulnerability as an ordinary issue — see [`SECURITY.md`](SECURITY.md).
Conduct concerns go to the contact in [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), not a public
issue or the security channel.
