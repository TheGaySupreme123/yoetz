# schemas/service/control-result-1.0.0.schema.json — local control success/error envelope

**Wave:** C | **ADRs:** ADR-004, ADR-008, ADR-009 | **Imports (spec-tree):** control-request; six
operation result schemas; importer, maintenance and integration result types; service-status,
privacy policy/setup/egress-receipt, version-manifest and public error boundaries | **Imported by:**
local clients, MCP bridge/UI and control tests

## Purpose

Freeze a bounded, method-discriminated result envelope that preserves RPC/service-generation
identity and never leaks exception text, unlock state details or arbitrary error messages. The six
workflow results reuse their operation schemas; this artifact owns every support success body.

## Public surface

- Draft 2020-12, `$id` `https://schemas.yoetz.dev/0.1/service/control-result-1.0.0.schema.json`.
- A disjoint closed `oneOf` with one `ok` and one `error` branch for each of the exact twenty-five
  method constants. Every branch requires only `protocol_version`, `rpc_id`,
  `service_instance_id`, `service_generation`, `method`, `outcome`, and `body`.
- `outcome` is branch-constant `ok|error`; `method` is branch-constant and selects the exact body.
  Cancel frames are one-way and have no result branch; only their targeted original call resolves.

## Behavior

Identity/version/method rules equal control-request. All inline objects and nested records use
`additionalProperties: false`; arrays have explicit caps and canonical ordering. Shared IDs,
digests, timestamps, frontiers, coverage, service status and privacy policy use their frozen
offline schemas/registries, never permissive string/object placeholders.

### Six workflow success branches

Each method has a separate `outcome=ok` branch whose `body` is an offline `$ref`:

| Method constant | Exact `body` schema |
|---|---|
| `start` | `operations/start-result-1.0.0` |
| `publish_work` | `operations/publish-work-result-1.0.0` |
| `check` | `operations/check-result-1.0.0` |
| `respond` | `operations/respond-result-1.0.0` |
| `status` | `operations/status-result-1.0.0` |
| `receipt` | `operations/receipt-result-1.0.0` |

These operation result schemas retain their own success/public-error union. Control `ok` means the
method dispatch completed and returned its reviewed public result; it does not rewrite a workflow
error into a transport failure.

### Nineteen support success branches

This artifact owns the following exact closed success `$defs`; `v` means required
`schema_version: "1.0.0"`. Bracketed fields are optional; all other fields are required. Every
support body below also requires the common body-level `privacy_projection` except the five
explicit structural/audit-recursion exemptions `service_status`, `service_lock`, `service_stop`,
`privacy_receipts_list`, and `privacy_receipts_get`.

| Method constant | Exact closed `body` definition |
|---|---|
| `import_codex_jsonl` | `{v, request_id:req_, task_id:tsk_, session_id:ses_, source_identity_digest:digest, report_object_id:obj_, report_digest:digest, imported_count:uint, quarantined_count:uint, unknown_count:uint, malformed_count:uint, batch_count:uint, first_frontier:Frontier, last_frontier:Frontier, coverage:Coverage, gap_codes:sorted-unique-token[0..128], codex_capability_profile_id:token, mapping_version:token}`. |
| `review` | `{v, request_id:req_, task_id:tsk_, session_id:ses_, at_frontier:Frontier, source_identity_digests:sorted-unique-digest[1..32], check_result:<check-result-1.0.0 $ref>, comparison_coverage:Coverage, counts:{cooperative:uint, imported:uint, artifact_evidence:uint, unmatched:uint, unknown:uint, redacted:uint, unavailable:uint}}`. |
| `backup_preview` | `{v, request_digest:digest, task_id:tsk_, frontier:Frontier, mode:machine_bound\|portable_recovery, destination_commitment:hmac-sha256, object_count:uint, estimated_ciphertext_bytes:uint, privacy_audit_object_count:uint, privacy_audit_snapshot_digest:digest, version_manifest:<version-manifest $ref>, warnings:sorted-unique-token[0..64], plan_digest:digest}`. |
| `backup_execute` | `{v, request_id:req_, task_id:tsk_, frontier:Frontier, mode:machine_bound\|portable_recovery, backup_manifest_digest:digest, backup_set_digest:digest, object_count:uint, privacy_audit_object_count:uint, privacy_audit_snapshot_digest:digest, database_digest:digest, [recovery_artifact_digest:digest], completed_at:timestamp}`; recovery digest is required only for portable mode and forbidden otherwise. |
| `restore_preview` | `{v, request_digest:digest, source_manifest_digest:digest, task_id:tsk_, backup_frontier:Frontier, [active_frontier:Frontier], new_route_identity_digest:digest, key_classification:machine_bound\|portable_recovery, migration_needed:bool, warnings:sorted-unique-token[0..64], plan_digest:digest}`. |
| `restore_execute` | `{v, request_id:req_, task_id:tsk_, restored_frontier:Frontier, [prior_route_identity_digest:digest], active_route_identity_digest:digest, backup_manifest_digest:digest, replay_digest:digest, completed_at:timestamp}`. |
| `migrate_preview` | `{v, request_digest:digest, task_id:tsk_, from_version:positive-canonical-decimal, to_version:positive-canonical-decimal, current_frontier:Frontier, required_migration_ids:ordered-unique-token[1..64], preflight_backup_mode:machine_bound\|portable_recovery, warnings:sorted-unique-token[0..64], plan_digest:digest}`. |
| `migrate_execute` | `{v, request_id:req_, task_id:tsk_, from_version:positive-canonical-decimal, to_version:positive-canonical-decimal, backup_manifest_digest:digest, frontier_before:Frontier, frontier_after:Frontier, replay_digest:digest, completed_at:timestamp}`. |
| `integration_preview` | Closed union: preview `{v, operation:preview, action:install\|replace\|remove\|noop, state_before:IntegrationState, source_digest:digest, [installed_digest:digest], compatibility:supported\|unsupported\|untested, file_changes:FileChange[0..64], warnings:sorted-unique-token[0..64], preview_digest:digest}`; status `{v, operation:status, state:IntegrationState, source_digest:digest, [installed_digest:digest], compatibility:supported\|unsupported\|untested, file_states:FileState[0..64], managed_marker_valid:bool}`. |
| `integration_execute` | `{v, action:install\|replace\|remove\|noop, state_before:IntegrationState, state_after:IntegrationState, source_digest:digest, [installed_digest:digest], changed_files:sorted-unique-relative-path[0..64], preview_digest:digest}`. |
| `service_status` | Offline `$ref` to `service/service-status-1.0.0`. |
| `service_lock` | Offline `$ref` to `service/service-status-1.0.0`; returned state must be `locked` for a successful result. |
| `service_stop` | `{v, state:draining, accepted:true}`; it never claims process exit before the connection closes. |
| `privacy_get_setup` | `{v, setup:<setup-wizard-contract $ref additionally constrained to service message_type question\|policy_review\|decision_required\|complete\|cancelled\|setup_error>, privacy_projection}`. |
| `privacy_get_effective` | `{v, policy:<privacy-policy-1.0.0 $ref>, privacy_projection}`. |
| `privacy_propose_policy` | Closed union: `{v, outcome:decision_required, proposal_id:ppr_, proposal_digest:sha256:<64 lowercase hex>, candidate_policy_digest:sha256:<64 lowercase hex>, expected_policy_version:positive-canonical-decimal, expires_at:timestamp}` or `{v, outcome:tightening_applied, policy:<privacy-policy $ref>, revoked_authorization_count:uint, closed_session_count:uint, provider_reconciliation:ProviderReconciliation}`. |
| `privacy_tighten_policy` | `{v, outcome:tightening_applied, policy:<privacy-policy-1.0.0 $ref>, revoked_authorization_count:uint, closed_session_count:uint, provider_reconciliation:ProviderReconciliation}`. |
| `privacy_receipts_list` | `{v, snapshot_generation:positive-canonical-decimal, receipts:PrivacyReceiptView[0..100], [next_cursor:authenticated-base64url[1..1024]]}`. Receipts are sorted `(finished_at, receipt_id)` descending, and the optional cursor is bound to the exact snapshot generation and request filters. |
| `privacy_receipts_get` | Closed union: `{v, outcome:found, receipt:PrivacyReceiptView}` or `{v, outcome:not_found}`. |

`IntegrationState` is exactly `absent|installed_exact|modified|partial|incompatible|unsafe`.
`FileChange` is the closed record `{action:create|replace|remove|unchanged, relative_path,
[before_digest], [before_size], [after_digest], [after_size]}` with presence gated by action;
`FileState` is `{relative_path, state:absent|exact|modified|unexpected, [digest], [size]}`. Relative
paths are canonical contained POSIX paths, never absolute/traversing. Integer counts/sizes are
bounded nonnegative JSON integers; `hmac-sha256` uses the frozen keyed-commitment form.
`ProviderReconciliation` is the closed record `{policy_version:positive-canonical-decimal,
activated_count:uint, deactivated_count:uint,
unavailable_binding_digests:sorted-unique-sha256[0..32]}`.

`PrivacyReceiptView` is a closed tagged union. The `network_egress` branch is
`{kind:network_egress, receipt:<privacy/egress-receipt-1.0.0 $ref>}`. The
`local_disclosure` branch is `{kind:local_disclosure, receipt:LocalDisclosureReceipt}` where
`LocalDisclosureReceipt` has the same closed structural identity, policy, scope, purpose,
outcome/reason, category/count/transformation/scan, consent, finish-time, and
`audit_store_version=1` fields as the egress receipt, substitutes required
`sink:local_model|agent_context|local_human_view|trusted_human_control` for `channel`, and forbids
`authorization_id`, `dispatch_id`, `dispatch_started_at`, `request_commitment`, provider/model/
endpoint destination fields, and all plaintext/content/object-dereference fields. It uses the exact
egress-receipt outcome/reason compatibility matrix. Both wrapper branches and both receipt shapes
use `additionalProperties:false`; the list/get result cannot be used to retrieve proposal/object
content.

### Control error branches

For each of the twenty-five method constants there is a distinct `outcome=error` branch with the
same exact body `$def`: `{code, retryable}` and no message/details. `code` is
`protocol_mismatch|frame_invalid|frame_too_large|request_cancelled|request_timeout|vault_locked|
service_draining|method_forbidden|service_generation_changed|privacy_projection_unavailable|
internal_error`; `retryable` is a
boolean. No null/free-text branch exists. `service_generation_changed` is the wire control reason;
the ordinary client closes the stale session and maps it to public `SERVICE_UNAVAILABLE`, never a
new workflow error code.
`privacy_projection_unavailable` is valid only for a ready method whose internal result could not
obtain its initial local-audit reservation; it is always retryable, contains no result body, and
maps to public `SERVICE_UNAVAILABLE`.

Handshake/fixed errors plus the closed structural `service_status`, `service_lock`, and
`service_stop` success bodies are exempt from `privacy_projection`: they are available while no
unlocked `Application`/audit key exists and their schemas contain only allowlisted fixed
state/version/generation/capability codes and booleans. `privacy_receipts_list` and
`privacy_receipts_get` are also projection/audit-exempt, but only while ready and only for an
authenticated ordinary CLI/UI caller; their bodies expose already-durable structural audit views,
so creating another receipt for inspection would recurse. All six workflow successes already
carry their operation body projection, and every other ready non-exempt support success requires
one body-level common receipt-bound projection. A nested previously projected public result such
as `review.check_result` retains that historical nested projection while the outer review body
records the current disclosure; the one-projection rule applies independently to each disclosed
result layer.
`service_lock` is always structural/exempt and returns only its post-lock status after the
application closes; it never depends on a local-audit call.

## Errors and edge cases

Request/result identity or method mismatch, cross-paired method/body, error body on ok, success
fields on error, unknown code/field, unvalidated nested success body, nonboolean retryability, raw
exception/path/PID/key/provider details, or changed generation fails client parsing. Internal error
uses only the fixed code. A control result for a cancel-frame RPC ID is invalid.

## Invariants

1. Every result binds exact request, method, service instance and generation.
2. Errors contain only fixed code and retryability.
3. Every one of the twenty-five success bodies is closed here or by the exact named offline
   operation/schema `$ref`; no open-dict support result exists.
4. Secret/confidential-ingress material has no result field.
5. Backup preview/execute cannot omit or hide the privacy-audit sidecar/object subset inside the
   undifferentiated total object count.

## Tests

`tests/unit/protocol/test_service_control_schemas.py` enumerates all fifty result branches,
cross-pairs every method/body, validates every nested support field gate (including required backup
privacy-audit counts/digests), and rejects a cancel result. Subprocess frame tests and CLI/MCP
conformance matrices cover transport mapping.

## Open questions

None.
