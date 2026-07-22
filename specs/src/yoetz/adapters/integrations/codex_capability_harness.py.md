# src/yoetz/adapters/integrations/codex_capability_harness.py — Gate-2 Codex conduit skeleton

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/src/yoetz/adapters/integrations/codex_discovery.py.md` |
**Imported by:** capability Gate-2 tests

## Purpose

Capture exact Codex executable identity (path, full SemVer including prerelease, sha256 digest)
and expose a fail-closed availability entry point for future app-server conduit checks. Discovery
alone never becomes a supported Codex profile claim.

## Public surface

- `CODEX_ARTIFACT_UNAVAILABLE` — structural reason token.
- `CodexArtifactIdentity` — path, `reported_version`, `executable_digest`.
- `capture_codex_artifact_identity(path, *, reported_version)` — pure file digest capture.
- `discover_codex_capability_artifact()` — discovery + identity, or `None`.
- `evaluate_codex_conduit_availability()` — `("codex_conduit_driver_unavailable", identity)` or
  `("codex_artifact_unavailable", None)`; discovery alone is never reported as ready.

## Behavior

`capture_codex_artifact_identity` reads regular-file bytes only (never executes). Digests use
`sha256:` prefixes. `discover_codex_capability_artifact` walks `discover_codex_binaries()` and
skips candidates without a parseable full version. `evaluate_codex_conduit_availability` is the
documented Gate-2 entry point; app-server protocol driving is explicitly out of scope for this
module.

## Errors and edge cases

- Missing/non-regular/symlink paths raise `ValueError` with a fixed reason code.
- Empty discovery returns unavailable rather than inventing support.

## Invariants

1. Prerelease/build suffixes are retained verbatim.
2. Unavailable never becomes a silent pass.
3. No app-server or model activation claims.

## Tests

- `tests/unit/adapters/test_codex_capability_harness.py`
- `tests/capability/test_codex_conduit_harness.py`

## Open questions

None.
