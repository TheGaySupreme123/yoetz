# src/yoetz/protocol/errors.py — public error codes and bounded protocol errors

**Wave:** A/B | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`protocol/models.md`, `cli/exits.md`, `mcp/errors.md`, `application/service.md`,
`adapters/sqlite/*`, `adapters/providers/*`
**Imported by:** nearly every public adapter and application module

## Purpose

This file defines the public error vocabulary that the rest of the system is allowed to surface.
It is the narrow boundary between internal failures and user-visible failures. If the code here is
too loose, every other layer starts leaking implementation details into CLI output, MCP errors,
logs, and receipts.

## Public surface

| Name | Signature (natural language) |
|---|---|
| `PublicErrorCode` | enum of the 22 public error codes in `specs/INTERFACES.md` |
| `PublicOperationError` | frozen exception/dataclass carrying `code`, `message`, `retryable`, `correlation_id`, `safe_details` |
| `ProtocolValueError` | internal-only value error with a bounded `reason_code: str` |
| `normalize_safe_details(...)` | helper that returns a bounded mapping or tuple of primitive-safe diagnostic items |
| `public_error_code_for_exception(...)` | adapter-side classifier from known internal failures to public codes |

## Behavior

`PublicErrorCode` is the only public code enum. Its members and spellings are fixed by the shared
interface registry:

- `INVALID_REQUEST`
- `PROTOCOL_VERSION_UNSUPPORTED`
- `SESSION_NOT_FOUND`
- `SESSION_CONFLICT`
- `IDEMPOTENCY_CONFLICT`
- `OPERATION_PENDING`
- `FRONTIER_CONFLICT`
- `EVENT_INVALID`
- `LIMIT_EXCEEDED`
- `BUNDLE_BUSY`
- `STORAGE_UNSAFE`
- `STORAGE_CORRUPT`
- `MIGRATION_REQUIRED`
- `SERVICE_UNAVAILABLE`
- `VAULT_LOCKED`
- `PRIVACY_AUTHORITY_REQUIRED`
- `PROVIDER_UNAVAILABLE`
- `PROVIDER_REFUSED`
- `PROVIDER_TIMEOUT`
- `SEMANTIC_RESULT_INVALID`
- `CANCELLED`
- `INTERNAL_ERROR`

`PublicOperationError` is the structured failure object used across application, CLI, and MCP.
It must:

- keep the public code explicit;
- keep the message short, safe, and user-facing;
- record whether the caller may retry without changing the request;
- carry a generated correlation ID when the failure reaches a boundary that logs it;
- expose only bounded safe details, never raw payloads, SQL, filesystem paths outside the safe
  diagnostic view, provider output, or secrets.

`ProtocolValueError` is not a user-facing error. It is raised by strict protocol parsers,
canonicalizers, and validators when something is structurally invalid but should not yet be mapped
to a public operation code.

`normalize_safe_details(...)` returns only primitives, bounded strings, small tuples/lists, and
allowlisted keys. Its output is suitable for inclusion in logs, MCP structured errors, or summary
objects, but it is never a place to echo user input verbatim.

`public_error_code_for_exception(...)` is an adapter helper, not a domain rule. It maps:

- validation and protocol-shape failures to `INVALID_REQUEST`;
- missing session/bundle state to `SESSION_NOT_FOUND` or `BUNDLE_BUSY` depending on phase;
- ledger conflicts to `SESSION_CONFLICT`, `IDEMPOTENCY_CONFLICT`, or `FRONTIER_CONFLICT`;
- unsupported or unsafe storage state to `STORAGE_UNSAFE`, `STORAGE_CORRUPT`, or
  `MIGRATION_REQUIRED`;
- absent/draining service or authenticated-control failure to `SERVICE_UNAVAILABLE`; locked vault
  to `VAULT_LOCKED`; attempted policy widening/approval through an untrusted surface to
  `PRIVACY_AUTHORITY_REQUIRED`;
- provider transport/model failures to the provider/semantic codes;
- cancellation to `CANCELLED`;
- all other unexpected failures to `INTERNAL_ERROR`.

The exact adapter mappings are owned by `specs/src/yoetz/cli/exits.md` and
`specs/src/yoetz/mcp/errors.md` and must stay stable across transports.

## Errors and edge cases

- `PublicOperationError.message` must be short and safe. It may summarize a failed operation, but
  it must not embed arbitrary payload text.
- `safe_details` is optional and may be empty.
- `ProtocolValueError.reason_code` must come from a bounded registry of machine-readable reasons
  such as `duplicate_object_key`, `invalid_utf8`, or `noncanonical_integer_string`; it must never
  contain raw user content.
- `PublicErrorCode` is never inferred from Python exception class names alone.
- Unknown internal exceptions degrade to `INTERNAL_ERROR` at the public boundary, but still get a
  correlation ID for local diagnostics.

## Invariants

1. Public errors are deterministic and bounded.
2. Public error handling never requires a traceback to explain the user-visible outcome.
3. No public boundary may invent a new code outside `PublicErrorCode`.
4. `ProtocolValueError` stays internal and low-level.
5. `retryable` is a claim about the operation state, not about whether the process crashed.

## Tests

- `tests/unit/protocol/test_errors.py` — enum membership, formatting, bounded details, reason-code
  validation, and no-raw-input guarantees.
- `tests/subprocess/test_cli_streams_and_exits.py` — exit-code mapping from public codes.
- `tests/conformance/surfaces/test_mcp_contract_matrix.py` — structured MCP error results and
  sanitized summaries.

## Open questions

None.
