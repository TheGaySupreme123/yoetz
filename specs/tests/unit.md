# tests/unit/ — pure contract, domain, kernel, and boundary unit suite

**Wave:** A–F | **ADRs:** all | **Imports (spec-tree):** protocol/domain/kernel/config/
observability specs and frozen fixtures | **Imported by:** pull-request and release gates

## Purpose

Lock the smallest deterministic behaviors without SQLite, filesystem mutation, MCP, Codex, provider
network, key store, or wall clock. Unit failures should identify one contract rule. Adapter behavior
belongs in integration/conformance suites and is not mocked here to make architecture disappear.

## Public surface

Required test modules and shared support files:

```text
tests/unit/
  adapters/
    test_codex_jsonl.py
    test_codex_discovery.py
    test_codex_lifecycle.py
    test_codex_mcp_registration.py
    test_codex_plugin.py
    test_codex_skill_integration.py
    test_git_subject_state.py
  protocol/
    test_ids.py
    test_strict_json.py
    test_canonical_vectors.py
    test_request_and_entry_identity.py
    test_errors.py
    test_coverage.py
    test_models_and_schemas.py
    test_service_control_schemas.py
  domain/
    test_values.py
    test_event_payloads.py
    test_findings.py
    test_receipts.py
  kernel/
    test_reducers_each_family.py
    test_replay_and_projections.py
    test_deterministic_checks.py
    test_policy_work_integrity.py
    test_policy_research_evidence.py
    test_ranking.py
    test_receipt_builder.py
  application/
    test_error_mapping.py
    test_harness_mcp_service.py
    test_integrations.py
    test_service_facade.py
    test_unit_of_work.py
    test_verdict_rules.py
    test_semantic_post_validation.py
  config/
    test_load_precedence.py
    test_models.py
    test_owner_declared_endpoint.py
    test_paths.py
    test_privacy_desired.py
  cli/
    test_elevated.py
    test_hooks.py
  observability/
    test_logging_allowlist.py
    test_privacy.py
  privacy/
    test_policy_and_contracts.py
  service/
    test_client.py
    test_confidential_client.py
    test_confidential_protocol.py
    test_control_protocol.py
    test_elevated_bootstrap.py
    test_lifecycle.py
    test_runtime_context.py
    test_secret_memory.py
    test_unlock_throttle.py
    test_vault_state.py
  version/
    test_manifest.py
tests/conftest.py
tests/fixture_loader.py
tests/builders/
  __init__.py
  ids.py
  clock.py
  events.py
  operations.py
```

Tests use `pytest`, frozen builders from `tests/builders`, and fixture bytes through the shared
read-only loader in `tests/fixture_loader.py`. Builders produce valid explicit IDs/times/frontiers;
they do not hide defaults that are correctness-relevant. `tests/conftest.py` wires those shared
fixtures into the suite without adding hidden state.

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
tests/unit/adapters/test_codex_discovery.py
tests/unit/adapters/test_codex_jsonl.py
tests/unit/adapters/test_codex_lifecycle.py
tests/unit/adapters/test_codex_mcp_registration.py
tests/unit/adapters/test_codex_plugin.py
tests/unit/adapters/test_codex_skill_integration.py
tests/unit/adapters/test_git_subject_state.py
tests/unit/application/test_error_mapping.py
tests/unit/application/test_harness_mcp_service.py
tests/unit/application/test_integrations.py
tests/unit/application/test_semantic_post_validation.py
tests/unit/application/test_service_facade.py
tests/unit/application/test_unit_of_work.py
tests/unit/application/test_verdict_rules.py
tests/unit/cli/test_elevated.py
tests/unit/cli/test_hooks.py
tests/unit/config/test_load_precedence.py
tests/unit/config/test_models.py
tests/unit/config/test_owner_declared_endpoint.py
tests/unit/config/test_paths.py
tests/unit/config/test_privacy_desired.py
tests/unit/domain/test_event_payloads.py
tests/unit/domain/test_findings.py
tests/unit/domain/test_receipts.py
tests/unit/domain/test_values.py
tests/unit/kernel/test_deterministic_checks.py
tests/unit/kernel/test_policy_research_evidence.py
tests/unit/kernel/test_policy_work_integrity.py
tests/unit/kernel/test_ranking.py
tests/unit/kernel/test_receipt_builder.py
tests/unit/kernel/test_reducers_each_family.py
tests/unit/kernel/test_replay_and_projections.py
tests/unit/observability/test_logging_allowlist.py
tests/unit/observability/test_privacy.py
tests/unit/privacy/test_catalog_audit.py
tests/unit/privacy/test_local_enforcer.py
tests/unit/privacy/test_policy_and_contracts.py
tests/unit/protocol/test_canonical_vectors.py
tests/unit/protocol/test_coverage.py
tests/unit/protocol/test_errors.py
tests/unit/protocol/test_ids.py
tests/unit/protocol/test_models_and_schemas.py
tests/unit/protocol/test_request_and_entry_identity.py
tests/unit/protocol/test_service_control_schemas.py
tests/unit/protocol/test_strict_json.py
tests/unit/service/test_client.py
tests/unit/service/test_confidential_client.py
tests/unit/service/test_confidential_protocol.py
tests/unit/service/test_control_protocol.py
tests/unit/service/test_elevated_bootstrap.py
tests/unit/service/test_lifecycle.py
tests/unit/service/test_runtime_context.py
tests/unit/service/test_secret_memory.py
tests/unit/service/test_unlock_throttle.py
tests/unit/service/test_vault_state.py
tests/unit/version/test_manifest.py
```

## Behavior

### Protocol cases

- IDs: every kind/prefix, UUIDv4 version/variant/spelling, nil/upper/wrong-prefix/wrong-length/
  non-ASCII rejection, actor format-only assurance, hostile `safe_request_id_from` inputs.
- Strict JSON/canonicalization: every positive/rejection vector, duplicate-name detection before
  model construction, UTF-16 key order, NFC/NFD distinction, floats and negative zero, unsafe
  integers, lone surrogates, exact bytes/digests, set normalization, no hidden hash/locale/TZ input.
- Identity: publication request digest excludes assigned/object/encryption fields; accepted entry
  digest covers the exact structural envelope; canonical-equivalent input retries are identical.
- Errors: all public codes, default retryability, correlation IDs, safe-details allowlist, no input
  echo, last-resort fallback construction without helper calls.
- Coverage: every dimension/default, pairwise weakest behavior for ordered dimensions, unordered
  kind preservation, known-gap sorted union, associativity/commutativity/idempotence where defined,
  and prohibition on averaging.
- Models/schemas: each operation positive/boundary/unknown-field/version case; known vs opaque event
  branches; model/frozen-schema parity and common fallback admission.

### Domain and reducer cases

For every event family, test complete valid payload construction, each required/optional field,
exact bounds, frozen/immutable behavior, conversion from boundary model, canonical-object separation,
and one dedicated rejection per invariant.

`reduce_event` gets one table-driven transition suite per family plus combinations:
plan/obligation supersession, assignment, action/result pairing, evidence and claim graph, repository
state freshness, contradiction creation/resolution, finding response/waiver/expiry, redaction gaps,
check/receipt records, unknown event gap. Assert input state/event are unchanged and equal logical
events yield equal output.

Replay tests compare empty, full, incremental, and arbitrary page boundaries using expected
projection digests. A reducer receives only accepted events; malformed-event behavior is tested at
the boundary, not invented inside the reducer.

### Deterministic policy cases

Each finding kind has:

- minimum triggering sequence;
- closest non-trigger;
- remediation sequence;
- redacted/unknown/weak-coverage variant;
- exact subject refs, frontier, priority inputs, policy/version, coverage, and stable finding ID
  derivation.

Cover open obligations, never-attempted requested item, omitted failure, unsupported claim, result
without action, stale evidence, unresolved contradiction, stale/incomplete ledger, and weak/stale
response. Work-integrity and research-evidence packs reuse engine mechanics but have separate domain
triggers/fixtures.

Ranking tests freeze deduplication, materiality/actionability/evidence ordering, deterministic ID
tie-break, default 3/max 10 cap, suppressed count, independence from input iteration order, and the
single cap-at-least-two slot that keeps one material post-validated reviewer challenge visible without
rewriting deterministic truth.

Receipt tests freeze subject frontier excluding its own event, conclusion wording, per-conclusion
weakest coverage, unresolved/suppressed finding inclusion, redaction/profile effects, versions,
canonical digest, and derived Markdown no-stronger-than-JSON.

### Application boundary helpers

Pure helpers test public-error mapping, deterministic/semantic-required verdict rules, frozen-case
construction, one-to-one deterministic finding bases, canonical review-packet selection and
omissions, semantic post-validation (invented IDs, out-of-case quote, coverage upgrade,
deterministic claim, malformed challenge, stale frontier), deadline arithmetic with injected clock,
and ambiguous-outcome decisions. End-to-end operation orchestration remains
integration/conformance.

### Service boundary

Freeze lifecycle transitions, locked/ready admission, true-idle relock, stale service-generation
fencing, exact control method/schema dispatch, canonical bounded frames, client reconnect and
ambiguous response handling, vault/keyring reason mapping, one-shot secret consumption, page-lock/
no-core capability reporting, and best-effort overwrite. The service-facade unit suite proves all
six operations delegate through one service-owned runtime and that semantic failure returns the
deterministic `incomplete_check` result. No unit constructs a per-client runtime or unlocks through
ordinary control.

Confidential boundary units separately freeze all YZH1/YZS1 golden frames and client sequencing,
prove the helper import graph excludes server authority, and freeze restart-safe throttle record/
clock behavior. They use scripted streams/filesystems/clocks only—no TTY, keyring, or KDF.

### Config, privacy, version

Test strict unknown-key/config profile validation, precedence using supplied mappings (not process
environment), forbidden project config escalation, and the isolated path-safety classifier against
synthetic repository/sync/symlink/permission layouts plus injected mount-table/statfs fixtures.
Overrides remain safety-gated and the unit test never reads the real home or mount table. Also test
the default `semantic=optional`, all four privacy profiles and five orthogonal review-context
profiles, exact `ReviewSelectionPolicy` expansion/intersection, the editable current-data-use
runtime guard, installed-evidence versus user-authored configuration, logging field/mode allowlists,
hostile string/exception objects, no traceback capture, opaque session/request MAC purpose/domain
separation (including trailing-NUL vectors), all privacy detectors, and deterministic manifest
construction from supplied distribution/runtime probe values.

## Errors and edge cases

- Tests fail if they access real HOME, current time, randomness, network, installed provider, key
  store, or writable database.
- Random/clock/ID behavior uses injected deterministic doubles with explicit sequences; exhaustion is
  a test failure.
- Snapshots are permitted only for canonical public artifacts with a semantic assertion beside them.
- No test asserts private implementation details such as internal call count when the contract is
  outcome-based.
- Warnings are errors except a test explicitly exercising one allowlisted warning.

## Invariants

1. Unit suite is offline, process-local, deterministic, and parallel-safe.
2. Every branch/reason code and event family has a named behavioral test.
3. Tests follow public ownership boundaries rather than importing concrete adapters into kernel tests.
4. Golden bytes come from reviewed fixtures, not values recomputed by the code under test.
5. No accepted wording exceeds fixture coverage.

## Tests

The suite is itself run with:

```bash
uv run --locked pytest tests/unit -q --timeout=60
```

Coverage gate: 100% branch coverage for canonicalization, ID/error sanitization, reducers,
deterministic checks, ranking, receipt wording, and privacy fences. Elsewhere, branch deficits
require explicit risk review; a global percentage cannot substitute for critical-path coverage.

Run under at least two `PYTHONHASHSEED` values, UTC and non-UTC TZ, C and UTF-8 locale, normal
and `-O` interpreter in the deterministic matrix.

## Open questions

None.
