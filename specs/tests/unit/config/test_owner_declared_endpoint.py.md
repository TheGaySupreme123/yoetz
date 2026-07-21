# tests/unit/config/test_owner_declared_endpoint.py — ADR-014 provider TOML tests

**Wave:** C | **ADRs:** ADR-006, ADR-014 | **Imports (spec-tree):** `config/models.md`,
`config/write.md`, `adapters/providers/openai_responses.md` | **Imported by:** none

## Purpose

Prove constrained `https_origin` validation, secret/free-URL rejection, official vs
owner-declared mutual exclusion, TOML round-trip, and unknown data-use for owner-declared hosts.

## Public surface

None — pytest module.

## Behavior

Parametrized invalid origins; mutual-exclusion reason codes; `write_provider_binding` round-trip;
`owner_declared_data_use_profile` never `recommendation_eligible`.

## Errors and edge cases

Covered as expected `ConfigError` reason codes.

## Invariants

Secret values never appear in exception repr.

## Tests

This file is the test.

## Open questions

None.
