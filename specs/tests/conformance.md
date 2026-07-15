# tests/conformance/ — cross-adapter, public-contract, and honesty conformance suite

**Wave:** A–F | **ADRs:** all | **Imports (spec-tree):** every public schema/port/application/surface
spec and permanent fixture | **Imported by:** PR, release, backward-compatibility, and public-claim
gates

## Purpose

Provide the executable definition of “the same Yoetz” across the in-memory reference adapter,
SQLite/object/key adapters, CLI, and MCP. Storage and transport are replaceable only when they
produce the same canonical public behavior. This suite also binds README/product claims to evidence
and prevents a technically successful path from overstating coverage.

## Public surface

```text
tests/conformance/
  adapters/
    test_ledger_port.py
    test_start_catalog_port.py
    test_object_store_port.py
    test_memory_sqlite_parity.py
  protocol/
    test_frozen_schemas.py
    test_canonical_cross_process.py
    test_idempotency_and_frontiers.py
    test_unknown_events.py
  operations/
    test_start_contract.py
    test_publish_work_contract.py
    test_check_contract.py
    test_respond_contract.py
    test_status_contract.py
    test_receipt_contract.py
  surfaces/
    test_cli_mcp_parity.py
    test_cli_contract_matrix.py
    test_mcp_contract_matrix.py
  honesty/
    test_coverage_weakening.py
    test_receipt_wording.py
    test_adversarial_cases.py
    test_strict_local_zero_egress.py
  privacy/
    test_never_send_scope_and_channels.py
    test_privacy_profiles.py
  compatibility/
    test_backward_read.py
    test_resource_manifest.py
  claims/
    test_local_service_security_doc.py
    test_public_claim_map.py
```

One `ConformanceBackend` test protocol constructs isolated runtimes over memory or durable
adapters with the same injected clock/IDs/policy/provider scripts. Test cases compare canonical
request/result/event/projection/finding/receipt artifacts, not private adapter rows/call traces.

### Exact future-file inventory

This index covers exactly these separately owned future files:

```text
tests/conformance/adapters/test_ledger_port.py
tests/conformance/adapters/test_memory_sqlite_parity.py
tests/conformance/adapters/test_object_store_port.py
tests/conformance/adapters/test_start_catalog_port.py
tests/conformance/claims/test_local_service_security_doc.py
tests/conformance/claims/test_public_claim_map.py
tests/conformance/compatibility/test_backward_read.py
tests/conformance/compatibility/test_resource_manifest.py
tests/conformance/honesty/test_adversarial_cases.py
tests/conformance/honesty/test_coverage_weakening.py
tests/conformance/honesty/test_receipt_wording.py
tests/conformance/honesty/test_strict_local_zero_egress.py
tests/conformance/operations/test_check_contract.py
tests/conformance/operations/test_publish_work_contract.py
tests/conformance/operations/test_receipt_contract.py
tests/conformance/operations/test_respond_contract.py
tests/conformance/operations/test_start_contract.py
tests/conformance/operations/test_status_contract.py
tests/conformance/privacy/test_never_send_scope_and_channels.py
tests/conformance/privacy/test_privacy_profiles.py
tests/conformance/protocol/test_canonical_cross_process.py
tests/conformance/protocol/test_frozen_schemas.py
tests/conformance/protocol/test_idempotency_and_frontiers.py
tests/conformance/protocol/test_unknown_events.py
tests/conformance/surfaces/test_cli_contract_matrix.py
tests/conformance/surfaces/test_cli_mcp_parity.py
tests/conformance/surfaces/test_mcp_contract_matrix.py
```

## Behavior

### Adapter parity

Run every port contract scenario against memory and durable adapters:

- start reserve/resume/complete/quarantine and every phase/lease/generation outcome;
- append/load/freeze/check commit/idempotency/frontier/conflict/unknown event;
- object stage/finalize/verified open, failure atomicity, redaction/missing/quarantine;
- fake semantic outcomes and key backend reasons where applicable.

For equal logical input and deterministic dependencies, require equal public errors/results, assigned
IDs/sequences/digests, projection canonical bytes, findings/ranking, coverage, and receipts. Physical
object ciphertext, nonces, file paths, row IDs, timing, and adapter diagnostics may differ and are
never compared as truth.

### Protocol and operation matrix

For every frozen schema and six operation:

- positive, negative, unknown-field, wrong-version, exact boundary, cap-plus-one;
- canonical-equivalent retry and changed logical reuse;
- expected/current/stale frontier behavior;
- actor/channel assurance non-upgrade;
- invalid known event rejects entire batch; bounded unknown version/type preserves opaque and adds
  projection gap;
- timeout/response-loss retry returns the exact prior public result;
- cancellation and provider degradation follow common envelopes;
- all `ok:false` codes/retryability/correlation/safe-details map identically.

Operation cases run application-direct, CLI JSON, and MCP tool. After parsing the surface envelope,
canonical structured results must match. Human CLI/MCP summaries are checked only for safe,
no-stronger wording.

### CLI contract

Freeze command/help/flag/input/output/exit behavior:

- six operation commands and support commands;
- flags vs JSON input and strict unknown-field behavior;
- JSON success/error envelopes on stdout, diagnostics on stderr;
- human views derived from structured truth;
- exact exits 0/2/10/11/20/30/40/70/130;
- findings do not turn successful checks into process failure;
- noninteractive/TTY, secret input, install preview/consent, and MCP stdout-purity rules.

### MCP contract

Freeze initialize/version negotiation, tools/list six names/input schemas/annotations, valid tool
calls, structuredContent/output-schema parity, safe text summary, `isError`, unknown tool vs
unknown method, validation location sanitization, application/public/unexpected exception fences,
cancellation, EOF/shutdown, fallback validation, malformed/oversized frame policy, and no stdout
noise.

The nested last-resort `INTERNAL_ERROR` object is fault-tested with safe helpers themselves
raising; each output schema admits it before stdin is accepted.

### Coverage and adversarial honesty

Run all ten `ADV-*` fixtures against both adapters/surfaces. Assert exact finding kind/refs/
origin/policy/frontier, stable ordering/cap/suppressed count, response effects, and receipt outcome.

Systematically weaken one dimension at a time—self-asserted, published-only, mutable reference,
stale, redacted, unknown event, imported partial, no semantic/provider failure—and assert no
conclusion becomes stronger. Lint structured and rendered outputs for forbidden unqualified
“verified/proved/complete/authenticated/lossless” language.

### Strict-local zero egress

Under denial of every non-allowlisted OS/socket route and with provider credentials absent:

1. construct only strict-local runtime; provider module/client must not be instantiated;
2. run all six operations, both policy packs, backup/restore where supported, CLI and MCP;
3. permit only the exact profiled service/confidential/local-model and release-tested OS credential,
   user-presence, and session-lifecycle local IPC needed by the case; make every arbitrary local or
   external DNS/socket/HTTP/subprocess attempt fail loudly and record zero forbidden attempts;
4. inspect config/log/error/receipt for no suggestion that semantic review occurred;
5. prove deterministic findings/receipt remain useful.

Tests also place fake credential-looking environment values and prove strict-local neither reads nor
reports them.

### Privacy and local-service public claims

Run all four privacy profiles × five review-context profiles through the same cases and prove
selector intersection and disclosure-policy intersection are monotone, context selection never
grants category/class/scope/provider authority, never-send/out-of-scope data cannot be approved,
every-request approval binds the exact prepared case, trusted-provider scope remains enumerated,
and the five network channels never inherit one another's consent. Freeze all five transparent CLI
recipes, the exact assisted expansion, exact eligible `prohibited + none|bounded +
prohibited|restricted` provider evidence, negative known-broad/unknown/stale states, and automatic
in-policy agent-to-agent review. The local-service security document and public claim map must state exactly what
the executable endpoint, keyring, lock/relock, confidential-ingress, same-UID limitation, and
memory-hardening evidence supports—never that local IPC or OS keyrings are universally secure.

### Backward-read and resources

For every released schema/object/storage/projection fixture:

- open copies with current artifact;
- verify canonical stored bytes/digests;
- preserve unknown events;
- replay/rebuild or return explicitly supported read-only result;
- perform migration only in a separate test copy, never rewrite fixture;
- require old public result/receipt interpretation or a documented protocol-major incompatibility.

Root schemas/migrations/skills/policy data/fixture manifests equal sdist/wheel/installed bytes and
runtime resource digests.

### Public claim map

A reviewed machine-readable table maps each public README/help/skill/support-matrix statement to:
claim ID, exact wording, coverage qualifier, owning spec/ADR, conformance test IDs, required platforms/
harness versions, and most recent evidence artifact. CI fails on a new capability sentence without
a map entry or a mapped test skipped on an advertised target.

## Errors and edge cases

- Adapter-specific exceptions/types never become expected public outputs.
- Normalized comparison may remove only documented nondeterministic diagnostics (latency, temp path);
  it cannot ignore IDs/digests/frontiers/coverage/versions.
- Skips are failures for advertised capabilities. Optional live/platform cases produce an explicit
  unsupported evidence record, never an implicit pass.
- Conformance fixtures are read-only; regeneration is a separate reviewed command.
- A test that relies on any ignored local planning input for expected behavior fails the
  self-containment gate; expected rules must be present in ADR/INTERFACES/owning spec.

## Invariants

1. One logical contract spans reference, durable, CLI, and MCP surfaces.
2. Every capability claim maps to passing artifact-level evidence.
3. Equal canonical inputs/versions produce equal deterministic public outputs.
4. Weaker observation/evidence/freshness never yields stronger language.
5. Strict-local is useful and performs zero egress.
6. Released data remains readable under the stated compatibility policy.

## Tests

```bash
uv run --locked pytest tests/conformance -m "not capability_live" -q --timeout=300
```

Release records exact package/resource/runtime/platform identities, normalized test report, fixture
manifest digest, skips (must be none for claimed capabilities), and evidence digest.

## Open questions

None.
