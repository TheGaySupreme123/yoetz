# tests/unit/service/test_elevated_bootstrap.py — elevated consent pending + catalog vectors

**Wave:** D | **ADRs:** ADR-015, ADR-016 | **Imports (spec-tree):**
`src/yoetz/service/elevated_bootstrap.md`, `src/yoetz/config/paths.md`,
`src/yoetz/protocol/canonical.md` | **Imported by:** `specs/tests/unit.md`

## Purpose

Freeze the service-side consent contract: catalog `implemented` flags, owner-only pending state,
danger digest, fifteen-minute TTL, exact single-shot approval, phrase placeholder in
`approve_command`, status projection, inherited-FD secret ingress, and tamper detection.

## Public surface

Pytest module; no exports. Covers `prepare_pending`, `load_pending`, `approve_pending`,
`clear_pending`, `projection_for_status`, `status_payload`, `catalog_payload`, `read_secret_fd`,
and `ElevatedBootstrapError.reason`.

## Behavior

Assert catalog lists default-safe MCP/privacy-tighten ops, marks vault initialize and credential
set/rotate `implemented=true`, and marks phrase-only / privacy-widen / idle-relock rows
`implemented=false`. Assert prepare refuses unimplemented ops and invalid digests/bindings.
Assert prepare for vault initialize writes pending state, projection uses
`<confirmation_phrase>` placeholder (live phrase not pre-filled in `approve_command`), approve
consumes pending (second approve → `pending_absent`), and FD primitives reject 0/1/2/empty/oversized.
Assert tampered danger text fails closed on load.

## Errors and edge cases

All filesystem tests use an isolated state directory. FD tests use inherited pipes and never
stdin/stdout/stderr. No real vault, service daemon, user home, or provider credential is required.

## Invariants

1. Phrase-only catalog rows are not preparable until durable grant consumption exists.
2. Approval is exact pending ID + danger digest + phrase and is single-shot.
3. Secret reading is impossible through stdin/stdout/stderr.
4. Agent projection never auto-fills the live confirmation phrase into `approve_command`.

## Tests

Self; indexed by `specs/tests/unit.md`.

## Open questions

None.
