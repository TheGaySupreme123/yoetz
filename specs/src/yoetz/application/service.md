# src/yoetz/application/service.py — ready-service application facade

**Wave:** D | **ADRs:** ADR-001–ADR-008 | **Imports (spec-tree):** `protocol/models.md`,
`protocol/errors.md`, `ports/start_catalog.md`, `ports/runtime.md`, `ports/semantic.md`,
`ports/clock.md`, `ports/ids.md`, `ports/diagnostics.md`, `domain/privacy.md`,
`config/models.md`, `application/egress.md`, application use-case modules |
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
- `@dataclass(frozen=True, slots=True) class VerificationPolicy` — the application-safe immutable
  copy of the effective `[verification]` choices: exact
  `semantic: disabled|optional|required` and `max_findings: int` in `1..10`. Its
  `default_check_mode` maps those values one-to-one to
  `deterministic_only|semantic_if_configured|semantic_required`. It contains no provider,
  credential, mutable config, or policy-pack implementation.
- `enum ProjectionRenderMode` — exactly `human_readable|machine_readable`.
- `@dataclass(frozen=True, slots=True) class ClientProjectionContext` — service-owned
  `client_kind`, `render_mode`, and boolean `output_is_controlling_tty` facts. It is internal,
  nonserializable, and cannot carry a requested sink. `fail_safe(client_kind)` constructs
  `machine_readable` plus `output_is_controlling_tty=False`.
- `ProjectedControlBody` — the exact Python union of the six operation result models and
  `JsonObject` for already-validated support results. It is not a seventh wire schema and cannot
  contain lifecycle/control-error results.
- `resolve_client_disclosure_sink(ClientProjectionContext) -> LocalDisclosureSink` — returns
  `local_human_view` only for `cli + human_readable + output_is_controlling_tty`; every other
  complete or contradictory combination returns `agent_context`.
- Exactly six async workflow methods: `start`, `publish_work`, `check`, `respond`, `status`, and
  `receipt`, with the existing exact request/result types.
- `@dataclass(frozen=True, slots=True, repr=False) ControlProjectionBinding` carries the exact
  admitted RPC/service generation/method, original request identity, catalog-resolved route
  identity digest, and canonical control request. It contains no presentation choice.
- Service-internal `async project_result_for_client(context, binding, result) ->
  ProjectedControlBody`, the only route by which an application/support result may become an
  ordinary CLI, MCP, or UI response.
- `async projection_binding_facts(method, request, result) -> ProjectionBindingFacts` resolves the
  active route through `StartCatalogPort.resolve_route(session_id)`, requires exact task/session
  agreement, and returns no route digest only for a truly installation-scoped support result.
- Support methods `import_codex_jsonl`, `review`, `privacy_get_setup`,
  `privacy_get_effective`, `privacy_propose_policy`, `privacy_tighten_policy`,
  `privacy_receipts_list`, and `privacy_receipts_get`; the six `privacy_*` names are exactly the
  ordinary-control `ControlMethod` wire tokens, with no facade alias or spelling translation. They are
  not extra workflow operations or MCP tools. Policy/disclosure decision completion is a separate
  service-internal call used only by `HumanControlService` with its consumed reauth proof.
- `async close()` — idempotent ready-composition shutdown.
- `class ReadyApplicationFactory` with `async open(ServiceReadyContext) -> Application` and an
  async callable `(service_generation, vault_generation) -> Application`; this is
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

For privacy support, daemon dispatch is an identity mapping from the six registered wire tokens to
the six same-named facade methods. The two receipt methods then delegate internally to
`PrivacyAuditPort.list_receipts` and `PrivacyAuditPort.get_receipt`, respectively; those port names
are internal storage-query vocabulary and never become alternate control tokens. The remaining
four methods delegate to the same-named functions in `application/privacy_policy.md`.
The daemon serializes `PrivacyReceiptPage` directly into the list body and maps
`PrivacyReceiptView | None` into the registered `found|not_found` get-result union.

### Service-owned client disclosure projection

The daemon must obtain catalog-backed `ProjectionBindingFacts`, construct the exact
`ControlProjectionBinding`, and call `project_result_for_client` after internal result validation
and before any ordinary control serialization, tracing, summary rendering, or response write. The projection uses
one frozen client/sink matrix:

| Source | Required sink |
|---|---|
| `mcp_bridge` workflow result | `agent_context` |
| ordinary `cli` workflow/support result rendered human-readably on its attached controlling TTY | `local_human_view` |
| ordinary `cli` workflow/support result requested as `--json`, redirected, piped, or non-TTY | `agent_context` |
| ordinary `ui` workflow/support result | `agent_context` |
| authenticated foreground YZH1 preview/policy-diff view | `trusted_human_control` |
| local semantic runtime input | `local_model` |

The service resolves the two CLI branches fail-safe from one exact `ClientProjectionContext`. The
context is constructed at the trusted service/client-adapter boundary and passed intact by the
daemon; it is not derived from the operation actor/client identity and never accepts a sink. Any
absent presentation facts use `ClientProjectionContext.fail_safe`, and any contradictory,
redirected, non-TTY, or machine-readable state resolves to `agent_context`. A caller-supplied actor
label or arbitrary sink claim never selects a branch, and ordinary TTY output never upgrades to
`trusted_human_control`. A future desktop UI obtains that sink only through the separate
authenticated confidential human-control ceremony.

`protocol/models.md` owns a closed field-classification registry for every success/support result.
IDs, enum codes, booleans, bounded counts, canonical digests, frontiers, policy/version identities,
and fixed omission metadata are `public_structural`. Every task/user-derived string, excerpt,
finding summary/detail, receipt prose, imported summary, selected path, command/diff text, or other
content-bearing leaf is a `CandidateContextItem` with its exact JSON Pointer and `DataCategory`.
An unregistered new field fails closed as content and blocks release until classified; it never
inherits structural status by type alone.

The facade sends the complete set of content-bearing leaves as one bounded candidate with exact
purpose `client_result_projection` and a trusted `ProjectionAuditContext` to
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

The coordinator owns the one objectless `AgentProjectionAuditSubject` reservation/commit for both
ordinary sinks. Workflow provenance is attached only when session, writer, and frozen frontier are
all present; absent provenance disables the self-authored bypass and does not reject support
projection. A support result without a workflow request ID receives one internal `req_` projection
identity. Review is projected once over its complete canonical internal body; the one resulting
`privacy_projection` value is inserted byte-identically at the review root and nested check result
only because the frozen public review shape requires both locations.

The local receipt proves only what Yoetz released across its service-to-client
`agent_context|local_human_view` boundary. It does not attest what an MCP host, CLI consumer,
terminal environment, agent, external model, or local runtime does with approved bytes afterward.
Setup and public docs must state that content authorized for `agent_context` may enter the host
agent's model context and may then be governed by that host's separate retention/egress policy.

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
