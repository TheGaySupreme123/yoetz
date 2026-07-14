# schemas/privacy/egress-receipt-1.0.0.schema.json — durable structural egress audit receipt

**Wave:** B/E | **ADRs:** ADR-004, ADR-006, ADR-009 | **Imports (spec-tree):**
privacy-policy and outbound-case schemas, canonical protocol | **Imported by:** PrivacyAuditPort,
outbound gateway, local inspection/export, privacy receipts and public claim tests

## Purpose

Freeze one durable, plaintext-free structural receipt for every successfully reserved terminal
external-request decision and every physical attempt, including machine-scoped channels with no task. An
initial reservation failure is the explicit pre-prompt/pre-dispatch no-receipt exception. The
receipt supports local accountability without turning logs into a copy of transmitted or blocked
content.

## Public surface

- `$schema`: Draft 2020-12.
- `$id`: `https://schemas.yoetz.dev/core/0.1/privacy/egress-receipt/1.0.0`.
- Media type: `application/vnd.yoetz.egress-receipt+json`.
- Closed fields: `schema_version`, `receipt_id`, `request_id`, `privacy_proposal_id`, optional
  `authorization_id`, optional `dispatch_id`, `channel`, `outcome`, optional
  `dispatch_started_at`, `finished_at`, `scope`, `purpose`, `destination`, `policy`, `consent_source`,
  `approved_categories`, `blocked_categories`, `counts`, `transformations`, `secret_scan`,
  optional `request_commitment`, conditionally required `safe_failure_reason`, and `audit_store_version` (integer
  constant `1`).

## Behavior

`receipt_id` is `egr_`, request is existing `req_`, privacy proposal/audit reservation is `ppr_`,
optional dispatch is `dsp_`, and optional authorization is `aut_`, each plus canonical lowercase
UUIDv4. The proposal ID is the stable key for a pre-dispatch decision receipt; the schema never
invents a dispatch or authorization merely to record a block, wait, denial, or expiry. The channel
enum covers `llm_inference`,
`product_telemetry`, `crash_diagnostics`, `update_checks`, and `capability_testing`. Scope is always
present and uses the privacy-policy schema's exact ancestor-chain shape: every kind carries
`installation_id`; workspace/task/request add `workspace_ref_commitment`; task/request add
`task_id`; request adds `request_id`. Taskless update, telemetry, crash and capability actions use
machine scope without inventing a task/session ID.

`outcome` is the exact terminal `PrivacyOutcome`: `blocked_by_policy|blocked_forbidden_data|
classification_uncertain|human_denied|approval_expired|channel_unavailable|
provider_refused|timeout|invalid_response|transport_failed|late|stale|audit_failed|completed`.
No in-flight `dispatched` outcome and no additional denial, cancellation, or unknown-dispatch
outcome token exists. `awaiting_human`, `approved`, and `receipt_pending` are internal audit states,
never receipt values. Destination follows
outbound-case rules. Policy carries ID, version, digest, and scope digest; every unkeyed digest is
`sha256:<64 lowercase hex>`.
`consent_source` is exactly the shared `ConsentSource` enum:
`none|baseline_policy|scoped_local_human|per_request_local_human`. A denial or block uses `none`;
its outcome/reason carries the denial semantics rather than inventing another consent source. The
receipt has no human name, proof/challenge, credential, unlock fact, or prompt.

Approved/blocked category arrays are sorted unique. `counts` carries candidate, included, removed,
approved and blocked item counts; candidate/final UTF-8 bytes; estimated input tokens when
available; and exact final `request_body_bytes` when `dispatch_id` is present. Authentication
metadata and HTTP/TLS framing bytes are not counted as user-data body bytes. `transformations` records only
minimized item count, redacted span count, and blocked item count. `secret_scan` records registry/
scanner version, match count, and pass/fail; it never stores matched text or detector snippets.

The schema has two conditional receipt forms. A pre-dispatch decision receipt is keyed by
`privacy_proposal_id`; if authorization was never minted, it omits both `authorization_id` and
`dispatch_id`. An authorization that expires or is revoked before I/O may retain
`authorization_id` while omitting `dispatch_id`. Whenever `dispatch_id` is present,
`authorization_id`, `dispatch_started_at`, `request_commitment`, and
`counts.request_body_bytes` are all required. Whenever `dispatch_id` is absent,
`dispatch_started_at`, `request_commitment`, and `counts.request_body_bytes` are forbidden.

`request_commitment` is a closed object with exactly required fields `algorithm` and `commitment`.
`algorithm` is the constant `hmac-sha256/yoetz-privacy-egress-request-v1`; `commitment` matches
`^hmac-sha256:[0-9a-f]{64}$`. The opaque `privacy_audit` handle and request-body bytes are absent.
v0.1 has one stable installation-derived audit key and no audit-key slot/rotation interface, so
`key_slot_ref` is not a field. The commitment is computed over exact final provider/application request-body
bytes after deterministic adapter rendering and immediately before I/O. Credential-bearing auth
metadata, HTTP/TLS framing, and transport-generated fields are excluded. Blocked/denied
receipts omit it because forbidden content must not be canonicalized into structural logs.

`safe_failure_reason` is forbidden when `outcome=completed` and required for every other outcome.
The schema enforces this complete closed compatibility matrix; a reason cannot be omitted or paired
with another outcome:

| `outcome` | permitted `safe_failure_reason` |
|---|---|
| `blocked_by_policy` | `policy_denied|scope_mismatch|purpose_not_allowed|destination_not_allowed|category_not_allowed|insufficient_approved_context` |
| `blocked_forbidden_data` | `never_send_detected` |
| `classification_uncertain` | `classification_uncertain` |
| `human_denied` | `human_denied` |
| `approval_expired` | `authorization_expired|authorization_stale|authorization_reused` |
| `channel_unavailable` | `channel_unavailable` |
| `provider_refused` | `provider_refused` |
| `timeout` | `provider_timeout|deadline_expired` |
| `invalid_response` | `provider_invalid_response` |
| `transport_failed` | `provider_unavailable|transport_failed|outcome_unknown` |
| `late` | `late` |
| `stale` | `stale` |
| `audit_failed` | `audit_failed` |
| `completed` | field forbidden |

Raw exceptions, URLs, paths, input, output, stderr and response bodies are forbidden.
`PrivacyAuditPort` persists receipts independently of task ledgers so taskless channels are covered
and privacy evidence cannot be lost merely because no session exists. `LocalDisclosureReceipt`
uses the same outcome/reason conditional matrix even though it substitutes `LocalDisclosureSink`
for the network channel and has no external dispatch fields.

`audit_failed/audit_failed` is valid only for an already durable audit reservation whose later
pre-dispatch audit transition failed closed and was completed after storage recovery. If the initial
reservation itself fails, no `EgressReceipt` exists: the application returns a bounded
`audit_failed` status with no receipt/proposal identity and no prompt, authorization, or dispatch.
After consumption, temporary receipt-write failure is internal `receipt_pending`; recovery writes
the real attempt outcome rather than `audit_failed`.

## Errors and edge cases

- A taskless receipt with fabricated task ID is invalid; machine scope plus absent operation is valid.
- Any receipt with dispatch ID/time but no authorization or request commitment fails closed.
- Any receipt without a dispatch ID that contains a dispatch time or request commitment fails.
- A v0.1 non-LLM `channel_unavailable` receipt requires outcome `channel_unavailable`, no
  `authorization_id`, no dispatch ID/time, no request commitment, and omission of the attempt-only
  `request_body_bytes` field; it represents capability absence before I/O, not attempted or ambiguous
  transport.
- A pre-dispatch blocked/denied receipt with a request commitment fails to avoid committing denied input.
- A `request_commitment` with any algorithm other than the exact v0.1 constant, with
  `key_slot_ref`, or with a noncanonical commitment string fails closed.
- A non-`completed` outcome without `safe_failure_reason`, a `completed` outcome with one, or any
  cross-paired outcome/reason fails closed; the same rule applies to `LocalDisclosureReceipt`.
- Retrying after a consumed physical attempt creates a new authorization, dispatch, and receipt;
  the request commitment may match only when the exact final application body bytes match.
- Failure to durably prepare the audit record prevents dispatch; failure after possible I/O yields
  canonical outcome `transport_failed` with reason `outcome_unknown`, or outcome `late` with reason
  `late`, through crash recovery—never a fabricated denial or invented outcome.
- Receipt inspection/export never resolves commitment, source refs, or policy refs to plaintext.
- The initial reservation-failure exception cannot be represented as a receipt because no durable
  reservation exists; code/tests must not fabricate `ppr_`/`egr_` evidence for it.

## Invariants

1. Every successfully reserved network-channel decision and every physical attempt can produce the
   same structural receipt type, including taskless structural decisions.
2. Receipts contain no user content, secrets, paths, prompts, provider responses, or raw diagnostics.
3. Attempted final request-body bytes are bound by a purpose-scoped keyed commitment held inside
   the trusted service; auth metadata/framing are not claimed, and a pre-dispatch decision is
   instead bound to its proposal/reservation and carries no fake attempt.
4. Enabling LLM inference never suppresses or authorizes another channel receipt.
5. Audit durability cannot be bypassed by CLI, MCP, agent, adapter, or provider.
6. Receipt wording never proves provider receipt, retention, deletion, or confidentiality.
7. `channel_unavailable` is structurally distinguishable from a physical attempt and can never
   carry attempt-only fields.
8. `audit_store_version`, commitment algorithm/shape, and dispatch-conditioned body count are exact
   v0.1 wire constraints, not implementation-selected options.
9. Outcome/reason compatibility is total and closed: success has no failure reason and every failure
   has exactly one reason permitted for that outcome.

## Tests

- `tests/unit/privacy/test_policy_and_contracts.py`
- `tests/integration/privacy/test_egress_gateway.py`
- `tests/conformance/privacy/test_never_send_scope_and_channels.py`
- `tests/packaging/test_privacy_docs_and_resources.py`
- Tests also cover pre-dispatch structural subjects, the initial-reservation no-receipt exception,
  rejection of `dispatched`/`key_slot_ref`, total outcome/reason conditionality for egress and local
  disclosure receipts, and recovery from `receipt_pending`.

## Open questions

None.
