# tests/unit/adapters/providers/test_factory_dispatch.py — provider factory dispatch suite

**Wave:** E | **ADRs:** ADR-006, ADR-014 | **Imports (spec-tree):**
`src/yoetz/adapters/providers/factory.md`, `src/yoetz/config/write.md`,
`src/yoetz/config/models.md` | **Imported by:** test runner

## Purpose

Prove that every endpoint profile the setup surface can write resolves to exactly one runtime
factory. The failure this suite exists to prevent is silent: a preset that writes cleanly into
`config.toml`, reports no error at startup, and only fails at dispatch with `factory_unavailable`
after an agent has already asked for the review.

## Public surface

Assertions over `external_factory_builders_from_config` for each bundled preset.

## Behavior

Each preset selects its exact factory type and its exact host, port, and path — including the
non-default path prefixes (`/inference/v1`, `/api/v1`, `/v1beta/openai`) that a wrong table entry
would silently change. The owner-declared origin still reaches the Responses factory with its
parsed host and port. Chat Completions bindings assert unknown data-use facts, which is what keeps
them out of the assisted-eligible path.

## Errors and edge cases

An unregistered profile ID and an absent provider both yield no builder rather than a default
factory: dispatching a wrongly shaped request to an unknown surface is worse than not dispatching.

## Invariants

1. Profile ID → factory type → endpoint facts is asserted as one chain, never sampled.
2. No live network call and no credential appears anywhere in this suite.

## Tests

This file is the executable owner.

## Open questions

None.
