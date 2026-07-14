# tests/unit/version/test_manifest.py — installed version manifest contract

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-006, ADR-007 | **Imports (spec-tree):**
`src/yoetz_core/version.md`, `src/yoetz_core/protocol/schemas.md`
**Imported by:** the version unit suite

## Purpose

Lock the version manifest as a deterministic report of installed identities, support limits, and
resource parity.

## Public surface

- `test_manifest_has_all_required_fields` — the manifest shape is complete.
- `test_manifest_is_deterministic_from_supplied_probes` — identical probe inputs yield identical
  output.
- `test_resource_parity_reports_mismatches` — missing/extra/corrupt resources are surfaced.
- `test_unavailable_optional_dependencies_are_reported_honestly` — optional missing pieces are
  limitations, not lies.

## Behavior

The suite proves:

- package, protocol, engine, policy, and storage identities are present;
- supported and unsupported capabilities are distinguished honestly;
- resource manifest bytes and hashes are stable;
- unavailable optional dependencies are reported as `{status: "absent"}`, never `null` or inferred.

## Errors and edge cases

- A manifest that omits a required identity fails.
- A manifest that invents unsupported capability fails.

## Invariants

1. Version manifest construction is deterministic.
2. Support limits are explicit.
3. Resource parity is part of the contract.

## Tests

- `tests/unit/version/test_manifest.py`

## Open questions

None.
