# tests/unit/application/test_integrations.py — harness intent, consent, and privacy

**Wave:** D | **ADRs:** ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):**
`src/yoetz/application/integrations.py.md`, `src/yoetz/ports/integrations.py.md` |
**Imported by:** the application unit suite

## Purpose

Lock explicit harness admission, preview-bound confirmation, modified-copy double consent,
read-only status, safe removal refusal, and path-free integration diagnostics.

## Public surface

- explicit `HarnessId` construction and redacted request representation;
- install confirmation/stale-preview tests;
- modified replacement and exact removal tests;
- status/diagnostic privacy tests.

## Behavior

Structural port doubles record every call. Invalid or string-cast harness values fail before any
port call. Install and remove preview first, require explicit acceptance of the exact returned
digest, and never convert a generic yes into modified-copy replacement consent. Status calls only
the read method. Diagnostics contain no project-root field or representation.

## Errors and edge cases

- Missing explicit acceptance is `confirmation_required`.
- Changed preview digest is `preview_stale`.
- Modified replacement without the pre-preview flag is `modified_copy`.
- Removal of a nonexact copy is `remove_refused` without mutation.

## Invariants

1. The harness discriminator is required and never inferred.
2. Mutation requires preview-bound explicit consent.
3. Project paths never enter diagnostics or representation.
4. Status cannot mutate the integration port.

## Tests

- `tests/unit/application/test_integrations.py`

## Open questions

None.
