# src/yoetz/ports/subject_state.py — structural artifact-state capture boundary

**Wave:** B/D | **ADRs:** ADR-002, ADR-009, ADR-011 | **Imports (spec-tree):**
`domain/values.md`, `protocol/errors.md` | **Imported by:** `adapters/git_subject_state.md`, CLI
state-capture support, capability/subprocess tests

## Purpose

Own the narrow effect boundary that produces comparable `SubjectStateRef` values without returning
repository content. This makes freshness capture explicit and swappable while keeping general live
artifact inspection outside v0.1.

## Public surface

- `class SubjectStateCapturePort(Protocol)` with
  `capture(command: SubjectStateCaptureCommand) -> SubjectStateCaptureResult`.
- `SubjectStateCaptureCommand(workspace: LocalWorkspaceHandle,
  expected_format: SubjectStateFormat)` — local-only command; the workspace handle is
  non-serializable and never enters an event/result/log.
- `SubjectStateCaptureResult(status, subject_state, format, limitations, bytes_hashed,
  files_hashed)` — bounded structural result.
- `SubjectStateStatus` — `captured|state_not_observed|unsupported|changed_during_capture`.
- `SubjectStateFormat` — v0.1 value `git_structural_v1`.
- `SubjectStateLimitation` — closed codes including `not_git`, `unsafe_root`, `submodule_present`,
  `symlink_unsupported`, `object_format_unsupported`, `read_limit_exceeded`,
  `file_limit_exceeded`, `git_failed`, and `input_changed`.
- `LocalWorkspaceHandle` — opaque client-local validated directory descriptor; it has no wire or
  canonical JSON representation.
- `MAX_SUBJECT_STATE_HASH_BYTES = 67_108_864` and
  `MAX_SUBJECT_STATE_FILES = 10_000`.

## Behavior

The port receives one explicit trusted local workspace selected by the caller's local trust flow.
It never accepts a path from an MCP request or ledger event. A complete capture returns one
`SubjectStateRef(tree_digest, diff_digest, described_state="git_structural_v1")`; capture always
includes supported untracked regular files rather than exposing a partial-selection option, and both digests use
the canonical `sha256:<64 lowercase hex>` form. `tree_digest` commits to the complete supported
state, while `diff_digest` commits to the supported delta relative to HEAD. The fixed
`described_state` token carries no repository text and does not participate in equality.

Only `status=captured` may return a subject state. Every unsupported, partial, over-limit, unsafe,
or changing capture returns `subject_state=None`, at least one closed limitation, and zero public
content. Counts are bounded structural diagnostics, not evidence that omitted files were unchanged.

The port defines capability and values only. It performs no I/O, selects no adapter, publishes no
event, opens no bundle, and changes no coverage. A caller may attach a successful result to an
ordinary action/result/evidence/claim publication; server-side validation continues to treat the
caller and channel according to their actual provenance.

## Errors and edge cases

- An empty/unvalidated workspace handle or unknown format is invalid before adapter dispatch.
- A result with a digest and non-`captured` status is invalid.
- A captured result missing either digest, fixed format token, or bounded counts is invalid.
- Limits are hard failure boundaries; no prefix digest is represented as the whole state.
- Cancellation yields `state_not_observed`; it never reuses a previous digest implicitly.

## Invariants

1. Structural capture returns digests and closed metadata only, never repository content or paths.
2. Partial or changing input never produces a comparable `tree_digest`.
3. The port performs no I/O and creates no ledger/disclosure receipt.
4. Capture provenance never upgrades publication/authorship/observation coverage.
5. General artifact inspection is not expressible through this port.

## Tests

- `tests/unit/domain/test_values.py` locks result/value validation and state relations.
- `tests/subprocess/test_cli_invocations.py` locks the installed CLI support command and no-content
  output.
- `tests/capability/test_codex_resume_reattach.py` uses exact captured states around a material edit.
- `tests/packaging/test_service_boundary_imports.py` locks the client-local import exception.

## Open questions

None.
