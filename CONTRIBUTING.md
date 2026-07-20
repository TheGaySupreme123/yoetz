# Contributing to Yoetz

Thank you for considering a contribution. This project is spec-first: the authoritative description
of every future file lives under [`specs/`](specs/), one Markdown owner per file, at the mirrored
path (`specs/src/yoetz/protocol/canonical.md` for `src/yoetz/protocol/canonical.py`). Read
[`specs/README.md`](specs/README.md) before your first change — it explains the method, the public
authority order (`docs/adr/` → `specs/INTERFACES.md` → the owning file spec), and the honesty rules
that bind every spec.

## Before you write code

1. Find the owning spec for the file you want to change in
   [`specs/FILE_MANIFEST.md`](specs/FILE_MANIFEST.md).
2. If your change affects public behavior — a new field, a changed error code, a different check
   outcome — **update the spec before or alongside the code**. Coding around an ambiguity instead of
   fixing the spec creates exactly the drift this method exists to prevent.
3. Use the exact names already registered in [`specs/INTERFACES.md`](specs/INTERFACES.md) for
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

## Review flow

v0.1 uses a lightweight spec-first pull request flow with no mandatory PR template:

1. Propose the change (issue or draft PR).
2. Update the relevant spec alongside the code if behavior changes.
3. Run the relevant test slice locally.
4. Hand off for review once behavior and docs agree.

## What not to do

- Don't hand-edit a generated or frozen artifact (a lock file, a generated schema mirror, a release
  manifest) — regenerate it through its owning script.
- Don't invent a second formatter, linter, or type-checker configuration; the pinned tools in
  `pyproject.toml` and the development-only `package.json` are the only ones this project uses.
- Don't add a public npm launcher or similar new distribution surface without its own spec — the
  end-user install path is Python/`uv` (see [`README.md`](README.md)).

## Security and conduct

Please don't file a security vulnerability as an ordinary issue — see [`SECURITY.md`](SECURITY.md).
Conduct concerns go to the contact in [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), not a public
issue or the security channel.
