# schemas/service/control-hello-result-1.0.0.schema.json — local control handshake result

**Wave:** C | **ADRs:** ADR-008 | **Imports (spec-tree):** control-hello and service-status schemas,
protocol IDs | **Imported by:** local clients, MCP bridge, future UI and control-schema tests

## Purpose

Freeze the service identity, generation, readiness and exact method surface returned after a valid
local control hello.

## Public surface

- Draft 2020-12, `$id`
  `https://schemas.yoetz.dev/0.1/service/control-hello-result-1.0.0.schema.json`.
- Owning helper: control hello result wire helper.
- Closed required fields: `protocol_version`, `service_version`, `service_instance_id`,
  `service_generation`, `status`, `allowed_methods`, `schema_manifest_digest`.
- `status` is an offline `$ref` to service-status 1.0.0.

## Behavior

Protocol is const `1.0`; service version is bounded SemVer; instance ID is canonical `svc_` UUIDv4;
generation is a canonical positive-decimal string. `allowed_methods` is sorted unique
and drawn only from the exact thirty-value ASCII-sorted registry `backup_execute`,
`backup_preview`, `check`,
`import_codex_jsonl`, `integration_execute`, `integration_preview`, `migrate_execute`,
`migrate_preview`, `observation_ingest`, `observation_pause`, `observation_resume`,
`observation_revoke`, `observation_status`, `privacy_get_effective`, `privacy_get_setup`,
`privacy_propose_policy`, `privacy_receipts_get`, `privacy_receipts_list`,
`privacy_tighten_policy`, `publish_work`, `receipt`, `respond`, `restore_execute`,
`restore_preview`, `review`, `service_lock`, `service_status`, `service_stop`, `start`, `status`.
For an authenticated `mcp_bridge` hello, `allowed_methods` is exactly the six workflow methods
`check`, `publish_work`, `receipt`, `respond`, `start`, and `status`; import/review, maintenance,
integration, observation, lifecycle and privacy support methods are neither advertised nor accepted.
The six privacy methods and five observation methods are ordinary least-authority CLI/UI methods
only. There is no ordinary decide/confirm method. Manifest digest is
`sha256:` plus 64 lowercase hex. Duplicate service identity/version/generation inside `status` must
equal the envelope.

## Errors and edge cases

Unknown/duplicate/unsorted method, mismatch between status/envelope, zero/negative/leading-zero
generation, invalid ID/version/digest, unknown/null field, or offline `$ref` failure rejects the
handshake. Allowed method presence is capability, not vault readiness or caller authorization.

## Invariants

1. One hello result names one exact service generation and schema set.
2. Method advertisement is finite and sorted.
3. Locked/draining status cannot be hidden by method advertisement.
4. No path, PID, user, credential or secret is exposed.
5. Method advertisement cannot upgrade a client kind; MCP receives exactly six workflow methods.

## Tests

`tests/unit/protocol/test_service_control_schemas.py` and
`tests/subprocess/test_service_lock_and_confidential_unlock.py`.

## Open questions

None.
