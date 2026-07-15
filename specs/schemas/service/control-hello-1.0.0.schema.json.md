# schemas/service/control-hello-1.0.0.schema.json — local control connection handshake request

**Wave:** C | **ADRs:** ADR-008 | **Imports (spec-tree):** local-service/control protocol and
canonical JSON specs | **Imported by:** service client/server handshake, CLI/MCP bridge/UI and
service-control schema tests

## Purpose

Freeze the first secret-free JSON message on an authenticated local control connection so clients
and service agree on protocol/resource identity before any method call.

## Public surface

- Draft 2020-12, `$id` `https://schemas.yoetz.dev/0.1/service/control-hello/1.0.0`.
- Closed required fields only: `protocol_version`, `client_kind`, `client_version`,
  `connection_nonce`, `schema_manifest_digest`.
- `protocol_version` is const `1.0`; `client_kind` is `cli|mcp_bridge|ui`.

## Behavior

`client_version` is bounded canonical SemVer. `connection_nonce` is exactly 64 lowercase hex and is
fresh connection binding, not a credential or reusable authorization. `schema_manifest_digest` is
`sha256:` plus 64 lowercase hex and binds the client's installed frozen-schema set. The object is
strict JSON, has no optional/null fields, and carries no method, path, PID, username, task content,
provider data, key locator, passphrase, challenge response or arbitrary metadata.

## Errors and edge cases

Unknown/missing field, wrong constant/enum, prerelease-invalid SemVer, reused/malformed nonce,
digest mismatch, float, duplicate key or oversize rejects the connection before requests. A valid
hello does not authenticate a local human or unlock the vault.

## Invariants

1. Handshake is bounded, closed and secret-free.
2. Client kind never upgrades human authority.
3. Schema identity is explicit before RPC.
4. Confidential ingress is a separate bounded binary protocol with no JSON field/schema.

## Tests

`tests/unit/protocol/test_service_control_schemas.py` and
`tests/subprocess/test_service_lock_and_confidential_unlock.py`.

## Open questions

None.
