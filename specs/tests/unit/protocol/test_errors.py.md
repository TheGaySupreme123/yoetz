# tests/unit/protocol/test_errors.py — public error vocabulary and sanitization

**Wave:** A/B | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/errors.py`, `src/yoetz/cli/exits.py`, `src/yoetz/mcp/errors.md`
**Imported by:** the protocol and boundary unit suite

## Purpose

Lock the public error codes, the bounded error dataclass, and the safe-details sanitizer so that
user-visible failures never leak implementation internals.

## Public surface

- `test_public_error_code_membership` — every registry code is present and no extras appear.
- `test_operation_error_is_bounded` — message, details, and correlation IDs are constrained.
- `test_safe_details_sanitizes_input` — arbitrary structures are reduced to bounded safe values.
- `test_exception_mapping_covers_known_failure_families` — the classifier maps known failures to
  the right public code.
- `test_internal_unknowns_degrade_to_internal_error` — unknown exceptions do not invent a new
  public code.

## Behavior

The suite proves:

- public codes exactly match the registry;
- retryable state is separate from user blame;
- safe details never echo raw payloads, SQL, or secrets;
- correlation IDs are present when the boundary requires them;
- unknown failures collapse to the bounded fallback code.

## Errors and edge cases

- A code missing from the registry is a release defect.
- A sanitized detail that still contains user content fails the test.

## Invariants

1. Public errors are finite and explicit.
2. Sanitization is bounded and non-echoing.
3. Internal exception shape never becomes public API.

## Tests

- `tests/unit/protocol/test_errors.py`

## Open questions

None.
