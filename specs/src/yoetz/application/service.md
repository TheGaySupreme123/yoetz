# src/yoetz/application/service.py — ready-service application facade

**Wave:** D | **ADRs:** ADR-001–ADR-008 | **Imports (spec-tree):** `protocol/models.md`,
`protocol/errors.md`, `ports/start_catalog.md`, `ports/runtime.md`, `ports/semantic.md`,
`ports/clock.md`, `ports/ids.md`, `ports/diagnostics.md`, `domain/privacy.md`,
`application/egress.md`, application use-case modules |
**Imported by:** `service/daemon.md` only in production; application tests

## Purpose

Defines the one use-case facade constructed inside the unlocked, ready per-user service. It wires
ports, applies one failure/cancellation policy, and delegates the six workflow operations. CLI,
MCP, and future UI do not construct or import this facade; they use `ControlClientPort` through
`service/client.md`.

## Public surface

- `@dataclass(frozen=True, slots=True) class Application` with injected
  `start_catalog`, `runtime`, semantic/egress coordinator, clock, IDs, diagnostics, and immutable
  verification policy.
- Exactly six async workflow methods: `start`, `publish_work`, `check`, `respond`, `status`, and
  `receipt`, with the existing exact request/result types.
- Service-internal `async project_result_for_client(client_kind, method, result) ->
  ProjectedControlBody`, the only route by which an application/support result may become an
  ordinary CLI, MCP, or UI response.
- Support methods `import_codex_jsonl`, `review`, `get_privacy_setup`,
  `get_effective_privacy_policy`, `propose_privacy_policy`, `tighten_privacy_policy`,
  `list_privacy_receipts`, and `get_privacy_receipt`; they are
  not extra workflow operations or MCP tools. Policy/disclosure decision completion is a separate
  service-internal call used only by `HumanControlService` with its consumed reauth proof.
- `async close()` — idempotent ready-composition shutdown.
- `class ReadyApplicationFactory` with `async open(ServiceReadyContext) -> Application`; this is
  service-internal and rejects any context not carrying the current vault/service generation.
- `@dataclass(frozen=True, slots=True) class ServiceReadyContext` — current service/vault
  generations, ready `BundleRuntimePort`, verified catalog, policy gateway, and structural startup
  evidence; nonserializable and constant-redacted.

There is no per-command `RuntimeFactory`, CLI scope, MCP lifespan factory, direct key argument, or
client-callable application constructor.

## Behavior

### Ready-only construction

`ReadyApplicationFactory.open` is called only by `service/daemon.md` after:

1. the daemon holds per-user singleton/catalog generation authority;
2. the service vault is `ready` and exposes opaque key/credential handles through its ports;
3. platform paths, versions, schemas, migrations, object formats, and packaged resources pass the
   startup gate;
4. the service-owned lazy bundle runtime is constructed;
5. the central privacy/egress gateway is active; only policy-approved provider adapters with an
   opaque vault credential handle may be constructed.

The factory validates one service/vault generation binding, constructs one application, and never
returns it to a client. Locked/draining state cannot call it. Relock closes the application before
the vault clears handles; later unlock constructs a fresh instance.

### Delegation and runtime routing

Each method is entered only after service control validation and lifecycle admission. It rechecks
ready generation, validates cross-field/session/actor/state invariants, routes the exact task
through the service-owned `BundleRuntimePort`, delegates once, releases its usage reference, and
returns the internal result to the daemon only. The facade never imports SQLite, object files, keyring, local socket,
MCP, Typer, or provider SDKs.

### Service-owned client disclosure projection

The daemon must call `project_result_for_client` after internal result validation and before any
ordinary control serialization, tracing, summary rendering, or response write. The projection uses
one frozen client/sink matrix:

| Source | Required sink |
|---|---|
| `mcp_bridge` workflow result | `agent_context` |
| ordinary `cli` workflow/support result, including `--json`, redirected output, or a TTY | `agent_context` |
| ordinary `ui` workflow/support result | `agent_context` |
| authenticated foreground YZH1 preview/policy-diff view | `trusted_human_control` |
| local semantic runtime input | `local_model` |

TTY presence, caller-supplied actor labels, output mode, or a client claim never upgrades an
ordinary result to `trusted_human_control`. A future desktop UI obtains that sink only through the
separate authenticated confidential human-control ceremony.

`protocol/models.md` owns a closed field-classification registry for every success/support result.
IDs, enum codes, booleans, bounded counts, canonical digests, frontiers, policy/version identities,
and fixed omission metadata are `public_structural`. Every task/user-derived string, excerpt,
finding summary/detail, receipt prose, imported summary, selected path, command/diff text, or other
content-bearing leaf is a `CandidateContextItem` with its exact JSON Pointer and `DataCategory`.
An unregistered new field fails closed as content and blocks release until classified; it never
inherits structural status by type alone.

The facade sends the complete set of content-bearing leaves as one bounded candidate to
`PrivacyCoordinator.prepare_local_disclosure`. The returned projection is deterministic:

- an approved leaf retains its schema-valid value;
- a denied/removed leaf is replaced in place by exactly
  `{ "omitted": true, "category": <DataCategory>, "reason":
  "local_disclosure_not_authorized" }`;
- structural leaves remain byte-equivalent;
- the top-level `privacy_projection` record contains sink, durable
  `local_disclosure_receipt_id`, policy ID/version/digest, sorted included/blocked categories, and
  sorted omitted JSON Pointers.

There is no free-form marker, null substitution, whole-result fallback that can conceal which
field was removed, or bridge-local discretion. If no content category is allowed, the caller still
receives the structural result plus markers and receipt. Initial audit reservation failure returns
the fixed retryable control error `privacy_projection_unavailable` before any content result is
serialized. Same logical request/result/sink/policy digest replays the same projection and receipt;
changed result bytes or policy require a new projection.

The local receipt proves only what Yoetz released across its service-to-client `agent_context`
boundary. It does not attest what an MCP host, CLI consumer, agent, external model, or local runtime
does with approved bytes afterward. Setup and public docs must state that content authorized for
`agent_context` may enter the host agent's model context and may then be governed by that host's
separate retention/egress policy.

Semantic evaluation receives only an already classified/minimized/policy-approved outbound case
from the egress gateway. If semantic work is required but unavailable/refused/timed out/invalid,
the deterministic check completes as `incomplete_check` with semantic coverage absent and the
agent-visible result states the limitation; the entire deterministic operation does not fail.

Unexpected failures cross one sanitized defect boundary. Cancellation propagates unless an
already-admitted shielded commit must resolve durably. Connection loss to a client never cancels a
commit or changes application lifetime; request-ID replay resolves ambiguity.

### Shutdown and relock

`close` stops new method entry, cancels noncommitting/provider work, allows admitted shielded
commits to resolve under the lifecycle bound, closes provider/semantic coordinator, then closes
the bundle runtime and catalog-facing resources. It does not release service singleton or vault
itself; daemon lifecycle owns their outer order. It emits no stdout and deletes no user data.

## Errors and edge cases

- Calling before ready, after relock, during drain, or under a stale service/vault generation is a
  bounded `vault_locked`/`service_draining` control failure; no direct runtime is constructed.
- Missing/locked bundle key, storage contradiction, migration, and provider failures preserve
  their bounded mappings. Semantic provider failure follows the incomplete-check rule above.
- Partial ready startup closes acquired resources in reverse order and never publishes the
  application to dispatch.
- Result-construction/schema failure is an internal defect, never client blame.

## Invariants

1. Only a ready daemon owns `Application`; ordinary client packages cannot construct or import it.
2. Exactly six workflow methods exist and CLI/MCP reach the identical instance through control.
3. Keys, credentials, decrypted vault state, paths, SQLite connections, and provider SDK objects
   are never application public fields.
4. No network/file operation occurs inside a SQLite write transaction.
5. Relock invalidates the application generation and closes every ready-only capability.
6. Provider input can originate only from the central egress-policy gateway.
7. No ordinary client receives an internal result or content-bearing leaf before the service-owned
   local-disclosure projection and durable receipt.

## Tests

- `tests/unit/application/test_service_facade.py` covers delegation, generation gates, error/
  cancellation mapping, and close.
- `tests/integration/service/test_daemon_clients.py` proves all clients share one ready application.
- `tests/integration/service/test_locked_ready_transitions.py` proves no application exists while
  locked and a fresh one is constructed after unlock.
- `tests/conformance/surfaces/test_cli_mcp_parity.py` proves semantic/schema/error parity and exact
  projection parity for the same sink policy; no test bypasses service projection.

## Open questions

None.
