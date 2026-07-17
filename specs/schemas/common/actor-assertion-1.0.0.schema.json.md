# schemas/common/actor-assertion-1.0.0.schema.json — caller-asserted actor boundary

**Wave:** A | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/protocol/models.md`, `specs/schemas/README.md`
**Imported by:** request schemas, event schemas, and public validation fixtures

## Purpose

Describe the caller-asserted actor shape that appears in public requests and imported observations.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/0.1/common/actor-assertion-1.0.0.schema.json`.
- Owning model: `ActorAssertionModel`.

## Behavior

This schema is a closed object with the following required/optional fields:

- `actor_id` required; validates the shared caller-asserted ASCII pattern
  `^[A-Za-z0-9._:-]{1,128}$`. `agt_` is a readable convention, not a required prefix, UUID shape,
  or server-minted identity.
- `actor_type` required; one of the public actor kinds from the registry.
- `display_name` optional bounded string.
- `asserted_by` optional bounded string describing who made the assertion.

The schema is purely shape validation. It does not confer authentication or assurance; the server
assigns those semantics later. Extra properties are forbidden, and field values remain bounded.

## Errors and edge cases

- A non-string, empty, over-128-byte, non-ASCII, whitespace-bearing, or otherwise disallowed
  `actor_id` fails; mixed case is allowed because letters are caller assertion text, not UUID hex.
- Extra keys fail closed.
- The schema must not be treated as proof of identity.

## Invariants

1. Assertion shape is not assurance.
2. Actor IDs remain opaque caller assertions and never imply authentication or uniqueness.
3. Extra keys are forbidden.

## Tests

- `tests/unit/protocol/test_models_and_schemas.py`
- `tests/conformance/protocol/test_frozen_schemas.py`

## Open questions

None.
