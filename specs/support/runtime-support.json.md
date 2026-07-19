# support/runtime-support.json — exact artifact runtime and integration support allowlist

**Wave:** F | **ADRs:** ADR-003, ADR-005, ADR-006, ADR-007, ADR-008, ADR-009, ADR-010, ADR-011 | **Imports (spec-tree):** capability
matrix, release evidence, version/resource specs | **Imported by:** packaged support mirror,
`src/yoetz/version.py`, startup diagnostics, public claim map

## Purpose

Turn tested release evidence into the exact allowlist used for write admission and support claims.
Documentation, version ranges, and neighboring successful cells are never substitutes for an entry.

## Public surface

A canonical strict-JSON object with:

- `schema: "yoetz.runtime-support/1"`, `manifest_version`, and `release_version`;
- `package_artifact`, a tagged `external` reference because a package cannot contain its own raw
  artifact digest; the release bundle binds the artifact digest to this support-manifest digest;
- `resource_set_digest`, the self-excluding set identity defined by the resource-manifest contract;
- `capability_matrix`, `release_evidence`, and `dependency_lock`, each a tagged evidence reference:
  `{status:"absent",reason_code:<bounded token>}` while unavailable, or
  `{status:"external",digest:"sha256:<hex>"}` when a separately produced immutable input exists;
- `runtime_cells`: ASCII-sorted exact combinations of CPython implementation/version/SOABI,
  normalized platform tag, OS floor, architecture, APSW version, SQLite version/source ID,
  amalgamation flag, and compile-options digest;
- `mcp_cells`: exact SDK version and explicitly supported protocol-version set;
- `codex_profiles`: exact Codex version, capability-profile ID/version, integration modes,
  `trigger_hook` as an explicit tagged `present|absent` value, observation-hook status (always
  `absent` in v0.1), and required passing case IDs; no min/max range. A present trigger freezes the
  lifecycle event, `reground_status` action, payload/privacy profile, coalescing/loop guard, and
  failure policy;
- `key_backend_cells`: exact platform/backend distribution/backend-classification and supported
  backup modes;
- `local_service_cells`: exact platform, AF_UNIX adapter, peer-credential primitive,
  control-protocol version, frame/schema set, singleton strategy, and required passing case IDs;
- `secret_memory_cells`: measured mutable-allocation, page-lock, core-dump, overwrite, and
  capability-gate outcomes without a perfect-zeroization claim;
- `user_presence_cells`: exact candidate-artifact digest, normalized platform/release cell,
  reviewed adapter/profile identity, OS-authentication primitive, authenticated-prompt,
  trusted-display/action-binding, one-use-attestation, and availability outcomes, capability-
  evidence digest, and required passing case IDs;
- `session_event_cells`: exact lock/suspend monitor behavior and required passing case IDs;
- `privacy_enforcement_cells`: exact policy/classifier/minimizer/scanner/gateway/receipt versions,
  profile/channel/local-sink matrix, zero-egress evidence, and required passing case IDs;
- `subject_state_cells`: exact Git/object-format and platform profile, capture format/version,
  file/byte caps, exclusion behavior, sanitized-environment profile, path/content-free result
  evidence, and required passing case IDs;
- `provider_profiles`: zero or more exact SDK/endpoint-profile/model/schema-policy cells, each with
  a versioned customer-content-training/retention/provider-human-access data-use record, review/
  expiry timestamps, evidence digest, and derived `assisted_recommendation_eligible` boolean;
- `denied_cells` and `limitations`: sorted bounded reason records;
- `manifest_digest`: SHA-256 of canonical content with this field omitted.

Every optional capability uses a tagged object with `status: "present"|"absent"`; neither `null`
nor a missing ambiguous field is permitted.

## Behavior

`generate_capability_matrix.py` and release-evidence review propose this source file. Release review
accepts only cells whose complete required case set passed against the exact candidate artifact and
whose evidence digests are included. Rows are sorted by canonical tuple identity; duplicate,
overlapping, inferred, stale, inconclusive, or mixed-artifact rows are forbidden.

A `codex_profiles.trigger_hook.status=present` row is admitted only when E-013 passes for that exact
cell. Neighboring versions and event-name documentation cannot supply the row. The trigger performs
only the coverage-neutral re-grounding action; observation-hook status remains absent. A
`subject_state_cells` row is admitted only when E-015 proves the complete ADR-011 privacy, bound,
race, and no-service-reachability matrix for that exact artifact/platform/Git cell.

At development time the file contains no supported cells, uses typed absent evidence references,
and carries a `development_unverified` limitation. The package-artifact reference remains tagged
`external` without an embedded raw artifact digest at every stage; the external release bundle
binds the raw artifact digest and this manifest's digest in one record. A release with an advertised write-support claim must contain
every and only the exact passing cells. Optional provider absence does not invalidate `local_only`
deterministic/service cells. An upstream version not listed is `untested`; it is never accepted
because it lies between listed versions.

`assisted_recommendation_eligible=true` is derived, never hand-entered: the exact provider cell and
data-use evidence are current; `customer_content_training=prohibited`; retention is `none|bounded`
with its exact ceiling; and provider-human access is `prohibited|restricted`. The flag is
recommendation evidence, not technical proof of provider behavior. Unknown/stale posture must set
it false. It fences runtime dispatch only when the user-controlled policy field
`require_current_provider_data_use_evidence` is true; a technical user may explicitly turn that
guard off through a trusted custom policy transition and then receives no upstream no-training
claim.

Pristine automatic OS-keyring initialization requires an exact passing `key_backend_cells` row and
an exact passing `user_presence_cells` row for the same candidate artifact and normalized release
cell. A keyring row alone is insufficient. Missing, absent, inconclusive, stale, cross-artifact, or
mismatched presence evidence yields `human_authority_unavailable` and authorizes no keyring/vault
mutation. This composite gate does not apply retroactively to loading an already committed
keyring-mode vault; missing current presence instead limits that ready generation to local work and
fences external activation.

The source and packaged mirror change together. Runtime verifies canonical JSON, self-digest, the
self-excluding resource-set identity, and exact cell match before granting write/semantic
capabilities. Raw artifact identity and post-build evidence are verified by release/install tooling
against their external references; runtime never pretends an absent reference is present. The
manifest is an evidence-bound allowlist, not a signature; it never claims authenticity by itself.

## Errors and edge cases

Unknown fields/statuses, float, duplicate key/cell, range syntax, wildcard platform/version,
inconclusive advertised cell, digest mismatch, unsupported normalization, missing limitation, or
source/package byte difference blocks release and write admission. Public diagnostics report only
bounded cell identity/reason and never host paths, usernames, credentials, or raw evidence.

## Invariants

1. Every supported cell is exact and directly backed by the named candidate evidence.
2. The file cannot infer continuous Codex, Python, provider, or platform support.
3. Optional absence is a tagged structural record, never `null`.
4. Equal evidence/policy inputs produce equal canonical bytes.
5. Runtime cannot widen this reviewed allowlist.
6. No supported keyring cell implies a supported user-presence cell; pristine auto-initialization
   requires an exact same-artifact intersection.
7. Provider technical compatibility and upstream assisted-review recommendation eligibility are
   separate, exact, evidence-bound facts.
8. Trigger-hook support and structural subject-state support are exact cells; neither is inferred
   from documentation, a neighboring version, or another platform.

## Tests

`tests/capability/`, `tests/packaging/test_platform_and_sqlite_gate.py`,
`tests/packaging/test_version_manifest.py`, `tests/conformance/claims/test_public_claim_map.py`, and
resource byte-parity/corruption tests.

## Open questions

None.

Exact cell contents are empirical release-lock outputs under the applicable gates among E-001
through E-015. No cell or evidence digest may be filled from documentation, a version label, or an
unexecuted test plan.
