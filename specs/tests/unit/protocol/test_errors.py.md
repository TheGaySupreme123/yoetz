# tests/unit/protocol/test_errors.py — public error vocabulary and sanitization

**Wave:** A/B | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/errors.py`
**Imported by:** the protocol and boundary unit suite

## Purpose

Lock the public error codes, the bounded error dataclass, and the safe-details sanitizer so that
user-visible failures never leak implementation internals.

## Public surface

- `test_public_error_code_membership` — the 22 public codes are exact with no extras.
- `test_protocol_reason_registry_is_exact_and_import_order_independent` — the frozen 127-member
  Wave B reason set declared by the B0 dependency root has exact membership, ordering source,
  grammar, and no runtime registration surface.
- `test_operation_error_is_bounded` — message, details, and correlation IDs obey exact limits.
- `test_operation_error_exception_value_contract` — constructor defaults, frozen slots,
  `Exception.args`, `str`, and safe deterministic dataclass representation are exact.
- `test_safe_details_allowlist_and_types_are_exact` — all allowed keys/types survive and every
  unknown, invalid, recursive, coercion-only, or oversized value is omitted.
- `test_correlation_binding_lifecycle` — internal `None`, first binding, same-ID rebinding,
  different-ID rejection, and public serialization-before-binding have exact behavior; binding
  preserves already-normalized `safe_details` verbatim, including enum-derived entries.
- `test_public_dict_shape_and_copy_are_exact` — required fields, conditional `safe_details`, enum
  serialization, ordinary dictionary copies, and insertion order are exact.

## Behavior

The suite proves:

- `PublicErrorCode` has exact bases `(str, Enum)`, explicit values equal to its 22 member names, and
  exact order; `SafeDetailValue` is exactly `str | int`, while every emitted runtime scalar is an
  exact built-in `str`/`int` and excludes `bool` or scalar subclasses;
- public codes and protocol reason codes exactly match their two closed registries;
- the registry includes `privacy_receipt_not_durable` and
  `provider_attempt_provenance_is_not_final`, and includes the schema-instance boundary reason
  `schema_instance_invalid` between `schema_id_mismatch` and `schema_kind_mismatch` in exact ASCII
  order, while the dependency-root module imports no privacy/provider/coordinator type;
- importing `errors`, `canonical`, `ids`, `coverage`, and `schemas` in every relevant order yields
  the same reason set and no module mutates it;
- retryable state is separate from user blame;
- `PublicOperationError(code, message, retryable)` applies exact `None` defaults, is frozen and
  slotted, stores normalized details in declaration order, sets `Exception.args == (message,)`, and
  satisfies `str(error) == message`;
- its deterministic dataclass representation contains the five already-safe stored fields and
  never contains the original details object, unknown-key values, rejected values, or another
  exception string;
- a `code` must have actual runtime type `PublicErrorCode`, not merely a spoofed `__class__`, and
  non-enum `code` and non-boolean `retryable` constructor values raise the two exact programmer
  `TypeError` messages, while supplied correlation IDs use exact built-in strings and the direct
  canonical `err_` UUIDv4 validator without importing `protocol.ids`;
- mixed-invalid construction proves the exact `code` -> `message` -> `retryable` ->
  `correlation_id` -> `safe_details` validation order, and non-string correlation values at either
  construction or binding use `public_error_invalid_correlation_id` rather than `TypeError`;
- safe details retain only the exact 16 keys and per-key value domains from `protocol/errors.md`;
  unknown keys are dropped without invoking their `__str__`, iterating nested structures, or
  echoing raw payloads, SQL, filesystem paths, or secrets;
- a raising or spoofed `__class__` fails closed, a non-`Mapping` impersonator is not read as a
  mapping, a non-`Enum` impersonator is not accepted as a structural enum, and an accepted Enum's
  `.value` is snapshotted exactly once;
- integer details reject `bool`, negative values, and coercion; enum-like details accept only the
  named actual `Enum` values, not raw caller strings; all emitted scalar values are exact built-in
  `str`/`int`, so hostile scalar subclasses are rejected rather than retained or coerced; `field`,
  version, schema-name, quarantine-code, and reason-code validators are exercised at both sides of
  every bound; field-pointer vectors include
  the empty root, `/`, empty tokens, `~0`/`~1`, and rejection of missing slash, invalid/dangling
  tilde escape, control/DEL, non-ASCII, and 257-byte inputs;
- deep errors may carry `correlation_id=None`, but public serialization fails with
  `public_error_missing_correlation_id` until a canonical `err_` UUIDv4 is bound; first binding
  returns a distinct frozen value, same-ID rebinding returns that exact object, and a different-ID
  rebind fails with `public_error_invalid_correlation_id`;
- `as_public_dict()` emits ordinary keys in exact `code`, `message`, `retryable`,
  `correlation_id` order, serializes `code.value`, omits empty details, and appends a nonempty
  `safe_details` ordinary dictionary copied in ASCII key order; mutating either returned dictionary
  cannot mutate the error.

Exception-family classification and unknown-exception fallback are boundary-owned tests in
`tests/unit/mcp/test_errors.py` and `tests/unit/cli/test_errors.py`; this dependency-root module
does not import application/adapters or classify by exception class name/message.

## Errors and edge cases

- A code missing from the registry is a release defect.
- A sanitized detail that still contains user content fails the test.
- A `bool` that survives in any `SafeDetailValue` position fails the test.
- A public dictionary containing empty `safe_details`, a nonordinary nested mapping, or any sixth
  key fails the test.
- Construction with an unregistered protocol reason must raise ordinary
  `ValueError("unregistered_protocol_reason_code")`, not create a new protocol/public error.

## Invariants

1. Public errors are finite and explicit.
2. Sanitization is bounded and non-echoing.
3. Internal exception shape never becomes public API.
4. Public serialization copies normalized data and has one exact conditional field.

## Tests

- `tests/unit/protocol/test_errors.py`

## Open questions

None.
