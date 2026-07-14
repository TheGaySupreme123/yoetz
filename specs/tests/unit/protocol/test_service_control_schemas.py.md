# tests/unit/protocol/test_service_control_schemas.py — frozen local-control schema matrix

**Wave:** C/F | **ADRs:** ADR-008 | **Imports (spec-tree):** five service schemas, schema registry,
protocol IDs and exact control method registry | **Imported by:** unit/schema/release gates

## Purpose

Prove the five service-control schemas are closed, mutually consistent, safe while locked and exactly
mirrored by installed resources; confidential ingress remains impossible to encode as JSON.

## Public surface

Table-driven positive/boundary/negative cases cover control hello/result, twenty-three exact call
branches plus cancel, forty-six method-specific ok/error result branches, every service status
enum/capability, and all ten fixed control error codes.

## Behavior

Validate canonical protocol/version/UUIDv4/generation, the 64-lowercase-hex connection nonce,
digest/SemVer forms, sorted unique method/capability arrays, exact optional deadline/relock bounds,
disjoint union branches and status/envelope identity equality. Cross-pair every method with every
other request and success body; all mismatches fail directly in the envelope schema, with the six
workflow `$ref`s and seventeen inline support definitions resolved offline. Error results accept
only code+retryable. Inspect every branch to prove no secret, passphrase, unlock field, arbitrary
message, service-internal path, PID, username, key locator or provider credential appears; the
explicit redacted maintenance/integration locators are allowed only in their exact branches.
The client-kind matrix proves MCP hello advertises exactly six workflow methods and rejects all
seventeen support branches, especially `import_codex_jsonl`, before base64 decode or persistence.
Status vectors include `locked/uninitialized/human_authority_unavailable` with no workflow or
external-provider capability and existing-keyring ready-local with external-provider omitted;
invalid reason/mode/state combinations fail.
Backup preview/execute vectors require `privacy_audit_object_count` and
`privacy_audit_snapshot_digest` in addition to total object count, reject omission/unknown aliases,
and prove neither field accepts content, paths, or an unkeyed non-digest placeholder.

## Errors and edge cases

Exercise unknown/missing/null/duplicate nested fields, wrong branch, ordinary/import/absolute
cap-plus-one, malformed/noncanonical base64, floats, leading-zero generation, unsorted/duplicate
arrays, stale identity, unknown method/error, schema digest mismatch, network `$ref`, malformed
confidential bytes presented as JSON, a result for cancel, and success/error body confusion.

## Invariants

1. Root/model/installed accepted sets agree.
2. Complete envelope unions never bypass method-body validation or fall back to an open dict.
3. Locked status remains safely serializable without secret state.
4. Confidential ingress has no JSON representation.
5. All schema resolution is offline.
6. A valid support schema never upgrades MCP authority.

## Tests

Run `uv run --locked pytest tests/unit/protocol/test_service_control_schemas.py -q`; all branch/
reason-code paths in service schema adapters require coverage.

## Open questions

None.
