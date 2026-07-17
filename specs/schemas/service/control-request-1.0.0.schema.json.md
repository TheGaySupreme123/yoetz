# schemas/service/control-request-1.0.0.schema.json — local control call/cancel envelope

**Wave:** C | **ADRs:** ADR-004, ADR-008, ADR-009 | **Imports (spec-tree):** protocol IDs; six
operation request schemas; importer, maintenance and integration ports; privacy policy/setup
schemas | **Imported by:** service control server/clients and framing tests

## Purpose

Freeze the authenticated-local-control RPC envelope as a complete closed union. The six workflow
methods reuse their reviewed operation schemas; this artifact itself owns every support-method body
definition. Vault secrets intentionally have no JSON method/body.

## Public surface

- Draft 2020-12, `$id` `https://schemas.yoetz.dev/0.1/service/control-request-1.0.0.schema.json`.
- Owning model: `ControlRequest`.
- A disjoint closed `oneOf` containing twenty-five method-specific call branches plus one cancel
  branch. Every call branch requires only `kind`, `protocol_version`, `rpc_id`,
  `service_instance_id`, `service_generation`, `method`, `body`, and optional `deadline_ms`;
  `method` is a branch-specific constant and `body` is branch-specific and closed.
- The cancel branch requires only `kind`, `protocol_version`, `rpc_id`, `service_instance_id`,
  `service_generation`, and `target_rpc_id`; it has no method/body/deadline.

## Behavior

`kind` is const `call` or `cancel`; protocol const `1.0`; RPC/target IDs are canonical `rpc_`
UUIDv4; instance is canonical `svc_` UUIDv4; generation is positive canonical decimal.
`deadline_ms` is JSON integer 1..300000, never float/string/null. Unless a `$ref` below states a
stricter bound, all strings are NFC, all arrays are bounded/sorted-unique where described, and all
inline objects (including nested objects) use `additionalProperties: false`. Shared IDs, digest,
SemVer, timestamp, frontier, coverage, public-error and policy shapes use their frozen offline
schemas/registries; they are not reimplemented as permissive strings.

### Six workflow call branches

The exact method/body mappings are offline `$ref`s to the six reviewed request artifacts:

| Method constant | Exact `body` schema |
|---|---|
| `start` | `operations/start-request-1.0.0` |
| `publish_work` | `operations/publish-work-request-1.0.0` |
| `check` | `operations/check-request-1.0.0` |
| `respond` | `operations/respond-request-1.0.0` |
| `status` | `operations/status-request-1.0.0` |
| `receipt` | `operations/receipt-request-1.0.0` |

Each row is a separate call `oneOf` branch. A body valid for one operation but paired with another
method is invalid.

### Nineteen support call branches

This schema owns the following `$defs`; `v` below means required `schema_version: "1.0.0"`.
`location` is a required 1..4096-character explicitly selected local locator with no NUL/control
character, represented as constant-redacted after parsing; semantic path/root/symlink checks remain
with the owning support port. `digest` means its owning canonical SHA-256 form. Bracketed fields are
optional; every other listed field is required.

| Method constant | Exact closed `body` definition |
|---|---|
| `import_codex_jsonl` | `{v, request_id:req_, session_id:ses_, writer_id:wri_, source_kind:file\|stdin, source_encoding:base64, source_bytes_base64, codex_version:SemVer, codex_capability_profile_id:token, mapping_version:token, exit_status:int[-1..255], stderr_present:bool, stderr_truncated:bool, stderr_captured_bytes:int[0..65536]}`; decoded source is at most 4 MiB and raw stderr/path/argv/cwd are absent. |
| `review` | `{v, request_id:req_, session_id:ses_, writer_id:wri_, at_frontier:Frontier, source_identity_digests:sorted-unique digest[1..32], mode:deterministic_only\|semantic_if_configured\|semantic_required}`. |
| `backup_preview` | `{v, request_id:req_, session_id:ses_, destination:location, mode:machine_bound\|portable_recovery, expected_frontier:Frontier}`. It is read-only and secret-free even in portable mode. |
| `backup_execute` | The exact `backup_preview` body plus required `confirmed_plan_digest:digest`; no confirmation boolean or secret field. For portable mode, confidential ingress may stage one service-internal `RecoverySecret` only after local-human confirmation of this exact request+plan digest, and execution consumes it once. |
| `restore_preview` | `{v, request_id:req_, source:location, destination_policy:new_route_only, recovery_mode:machine_bound\|portable_recovery, [expected_task_id:tsk_], [expected_active_frontier:Frontier]}`; it inspects only structural manifest/key classification and never asks for or stages a recovery secret. |
| `restore_execute` | The exact `restore_preview` body plus required `confirmed_plan_digest:digest`. Portable mode requires a one-shot service-internal `RecoverySecret` staged only after the local human confirms that exact request+plan digest; the JSON body contains no handle/token. |
| `migrate_preview` | `{v, request_id:req_, session_id:ses_, target_storage_version:positive-canonical-decimal, expected_frontier:Frontier}`. |
| `migrate_execute` | The exact `migrate_preview` body plus required `confirmed_plan_digest:digest`. |
| `integration_preview` | A closed union of `{v, operation:preview, request_id:req_, project_root:location, action:install\|replace\|remove, replace_modified:bool}` and `{v, operation:status, project_root:location}`. |
| `integration_execute` | `{v, request_id:req_, project_root:location, action:install\|replace\|remove, preview_digest:digest, explicitly_accepted:true, replace_modified:bool}`. |
| `service_status` | `{}`. |
| `service_lock` | `{}`. |
| `service_stop` | `{}`. |
| `privacy_get_setup` | An offline `$ref` to `privacy/setup-wizard-contract-1.0.0` additionally constrained to client `message_type` `begin\|answer\|review\|cancel`; `propose` and `tighten` use their dedicated methods below. |
| `privacy_get_effective` | `{v, scope:AuthorizationScope}` where the closed union is machine `{kind:machine, installation_id:ins_}`; workspace `{kind:workspace, installation_id:ins_, workspace_ref_commitment:hmac-sha256:<64 lowercase hex>}`; task `{kind:task, installation_id:ins_, workspace_ref_commitment:hmac-sha256:<64 lowercase hex>, task_id:tsk_}`; request `{kind:request, installation_id:ins_, workspace_ref_commitment:hmac-sha256:<64 lowercase hex>, task_id:tsk_, request_id:req_}`. Descendant fields are forbidden on shallower branches. |
| `privacy_propose_policy` | `{v, expected_policy_digest:sha256:<64 lowercase hex>, candidate_policy:<privacy-policy-1.0.0 $ref>}`. It can queue widening but carries no decision/confirmation/proof. |
| `privacy_tighten_policy` | `{v, expected_policy_digest:sha256:<64 lowercase hex>, candidate_policy:<privacy-policy-1.0.0 $ref>}`; the server must prove the candidate is a mathematical subset before commit. |
| `privacy_receipts_list` | `{v, filters:{[outcome:PrivacyOutcome], [channel:EgressChannel], [sink:LocalDisclosureSink], [provider_id:token], [endpoint_profile_id:token], [policy_version:positive-canonical-decimal], [scope_kind:machine\|workspace\|task\|request], [finished_from:timestamp], [finished_through:timestamp]}, page_size:uint[1..100]=50, [cursor:authenticated-base64url[1..1024]]}`; filters are closed and `finished_from <= finished_through`. |
| `privacy_receipts_get` | `{v, receipt_id:egr_}`. |

`source_bytes_base64` is canonical padded RFC 4648 base64, at most 5,592,408 ASCII characters,
and must decode to at most 4,194,304 bytes. If `stderr_present=false`, `stderr_truncated` is false
and `stderr_captured_bytes` is zero; no branch carries stderr bytes. To preserve the importer's
frozen 4 MiB exact-source limit without an unbounded stream or path grant, this one branch is allowed only when the
whole canonical control frame remains within `MAX_CONTROL_FRAME_BYTES = 6_291_456`; every other
call retains the 1_048_576-byte method-body ceiling and the same 6_291_456 absolute frame guard.
The decoded bytes are moved immediately into the importer's one-shot sensitive byte source and are
never logged, traced, included in errors, placed in RPC replay/idempotency caches, spooled by the
control layer, or otherwise persisted as control state. `import_codex_jsonl` is CLI/trusted-local-UI
support only: it is never advertised to or accepted from `mcp_bridge`, regardless of body validity.

The six privacy methods get setup/effective policy, propose/tighten policy, or structurally inspect
privacy receipts. List/get are CLI/UI-only, snapshot-stable, contain no content/object lookup, and
are projection/audit-exempt so inspection cannot recursively create a receipt. They contain no
ordinary decide/confirm operation; foreground human decisions use the
separate `HumanControlService`, and MCP bridge client kind cannot invoke privacy setup/preview.
`service_lock` and `service_stop` remain secret-free. Confidential unlock/recovery/credential bytes
use separately framed bounded confidential ingress and cannot be represented by any method/body.
Maintenance ordering is fixed: secret-free preview -> exact plan display/confirmation -> optional
confidential `portable_recovery` ingress bound to request+plan digest -> execute. Decline/stale plan
never prompts for or captures recovery material.

## Errors and edge cases

Mixed call/cancel fields, a method/body discriminator mismatch, unknown method, wrong generation/
instance, reused RPC with changed bytes, cancel targeting itself/unknown completed call, cap
overflow, unknown envelope/nested field, malformed base64/body, or missing offline `$ref` fails
before application dispatch. Deadline expiry maps to the bounded control error result. A cancel
frame is one-way: the original call eventually returns its durable result or `request_cancelled`;
the cancel frame itself never receives a success result that could falsely prove cancellation.

## Invariants

1. Call and cancel branches are disjoint and envelope-closed.
2. Every one of the twenty-five method bodies has one exact closed branch in this artifact; no
   registry entry can resolve to `dict[str, JsonValue]` or an unvalidated body.
3. Generation/instance fence stale clients.
4. No JSON request can carry vault unlock material.
5. MCP can represent only the six workflow call branches; support-call validity never grants MCP
   authority.

## Tests

`tests/unit/protocol/test_service_control_schemas.py` enumerates all twenty-six request branches,
cross-pairs every method with every other body, and rejects unknown nested fields. Subprocess
framing/cancellation/import-cap tests and
`tests/subprocess/test_service_lock_and_confidential_unlock.py` cover the effect boundaries.

## Open questions

None.
