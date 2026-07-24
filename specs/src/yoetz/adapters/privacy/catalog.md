# src/yoetz/adapters/privacy/catalog.py — catalog-backed policy store and privacy audit

**Wave:** C–E | **ADRs:** ADR-003, ADR-008, ADR-009 | **Imports (spec-tree):**
`domain/privacy.md`, `ports/privacy.md`, `ports/objects.md`, SQLite connection/repository contracts |
**Imported by:** runtime composition, privacy recovery/maintenance tests

## Purpose

Implement `PrivacyPolicyStorePort` and `PrivacyAuditPort` for machine/workspace/task/request scopes,
including taskless network channels. The catalog stores structural state plus task-bundle encrypted
object references where content exists; it never needs a taskless content-encryption key in v0.1.

## Public surface

- `class CatalogPrivacyPolicyStore(PrivacyPolicyStorePort)`.
- `class CatalogPrivacyAudit(PrivacyAuditPort)`.
- `CatalogPrivacyAudit.live_object_roots(...) -> PrivacyAuditObjectRoots` — generation-bound
  catalog roots for task-bundle privacy objects; service-internal, path-free, and read-only.
- `CatalogPrivacyAudit.get_receipt(...)` / `list_receipts(...)` — indexed bounded structural
  inspection with authenticated service-owned pagination cursors and no object dereference.
- `CatalogPrivacyPolicyStore.reseed_untouched_bootstrap_default(scope, *, expected_current,
  replacement) -> PrivacyPolicy` — adapter-internal upgrade path that carries an installation whose
  stored policy is still the untouched first-run bootstrap seed forward to the current shipped
  default. It is not part of `PrivacyPolicyStorePort` and is never reachable from a caller request.
- Internal frozen row codecs for policy generation, overlay, proposal, authorization, dispatch, and
  receipt state. They are not exported or exposed through application results.

`complete_agent_projection` receives an authority-free `AgentProjectionRequest`, mints the keyed
control/internal/projection commitments inside this adapter, and inserts the subject plus terminal
receipt in one `BEGIN IMMEDIATE`. Exact replay returns the same subject; contradiction fails
closed. Receipt cursors bind the exact query and snapshot boundary under the audit MAC key.

## Behavior

Policy writes and audit transitions use bounded `BEGIN IMMEDIATE` transactions with generation and
digest compare-and-set predicates. Encryption/object finalization and scans occur before the short
transaction. Structural rows contain IDs, scope commitments, versions/digests, enums, timestamps,
counts, state, and encrypted-object references only—never policy preview excerpts, transmitted
bytes, destination URLs, credentials, exception text, or raw provider responses.

`reserve(PrivacyAuditSubject)` has three exact branches. A task-owned `DisclosureProposal` stages and
finalizes one `ObjectKind.privacy_audit` encrypted object in that task's bundle, then inserts the
proposal row with its `ObjectRef` in state `reserved` and increments the task's monotonic
`privacy_root_generation`. It does not insert a task-ledger object-inventory row. A
`PreDispatchAuditDecision` inserts only its
closed structural fields in state `decision_receipt_pending`; it has no object reference and cannot
be approved or authorized. An `AgentProjectionAuditSubject` inserts only its keyed internal-result/
projection commitments, method/scope/policy identity, pointer/category decisions and counts; it has
no encrypted duplicate object, no unkeyed plaintext-derived digest, and can take only the atomic
`approved -> local_disclosure_pending -> local_disclosure_completed` branch. Failure before any
branch commits returns initial `audit_failed` with no
receipt ID, prompt, authorization, or dispatch. An object finalized before a failed row transaction
is an ordinary unreferenced encrypted orphan.

`live_object_roots` reads the active route identity and every noncleared `privacy_audit` ObjectRef for
the exact task in one catalog snapshot, sorts them by object ID, and returns the generation plus
canonical root-set digest. Terminal/denied/expired/quarantined age does not remove a root. v0.1 has
no individual audit-content clear operation; roots remain for the supported installation-data
lifetime. Any future clear must be an explicit privacy-audit redaction transaction that preserves
structural receipts and increments root generation before GC eligibility.

The audit state machine and one-use authorization semantics exactly implement `ports/privacy.md`,
including `decision_receipt_pending -> decision_completed` and
`authorized -> receipt_pending -> attempt_completed`, plus atomic agent-context/local-model
consumption and terminal local-disclosure completion.
Every decision, network-attempt, and local-disclosure receipt is validated against the same closed
outcome/reason matrix before its terminal transaction; success forbids a reason and failure requires
one compatible reason.
Taskless channels use installation/catalog scope. A task-bound receipt may reference a task/session
commitment but does not append a task event. Startup recovery marks expired proposals/grants,
reconciles `receipt_pending` attempts with terminal receipts, and never infers approval from a
missing row.

`get_receipt` and `list_receipts` read only the receipt/order/filter columns and canonical structural
receipt in `privacy_audit_records`. They never join to, open, or return the proposal ObjectRef.
Filters and sort order exactly follow `ports/privacy.md`; the opaque cursor is authenticated by the
service and is not stored as caller-controlled catalog text.

Re-seeding the shipped default is gated on two independent conditions inside one
`BEGIN IMMEDIATE`: the stored current row must still carry first-run provenance — `change_kind`
`seed` with no `source_proposal_id`, which only the bootstrap seed writes — and its decoded policy
must equal the expected old default in every field. Contents alone cannot prove origin, so an owner
tightening or approved expansion that happens to reproduce the old default's fields keeps its
policy. The replacement is written as a new superseding version whose `policy_digest` is derived
from the shipped default's own revision identity, never inherited from the policy it replaces:
two different policy payloads sharing one digest would break the digest compare-and-set that guards
later tightenings.

Policy transition rows contain only their closed nonsecret structural diff; content-bearing policy
diffs are invalid in v0.1. Policy tightening increments generation, revokes incompatible
pending/approved grants, and emits a local gateway-revocation notification after commit. Loosening
requires the canonical prepared-diff digest and authenticated decision; the diff itself is structural
rather than an encrypted content object. Backups include catalog privacy state plus referenced bundle objects according to the same
encrypted backup contract; redacted exports contain structural summaries only.

## Errors and edge cases

Missing/corrupt policy fails closed to local-only/denied and blocks mutations until recovery. A
durable `receipt_pending` attempt is ambiguous and quarantined from selection until receipt repair;
recovery records its real bounded outcome, never a fabricated `audit_failed`. A later audit
transition failure for an existing reservation may complete `audit_failed/audit_failed` after the
store recovers. Object-finalized/transaction-failed artifacts are unreferenced orphans. Clock expiry is
rechecked inside the authorizing/consuming transaction.

If any catalog-held root is missing, wrong-task/kind, digest-mismatched, or undecryptable, recovery
CASes its audit row to `quarantined` without clearing the ref, fences task content disclosure/resume,
and exposes only bounded audit degradation. No-GC deterministic work may continue. Backup/restore
fails until verified repair. A route move/restore keeps ObjectRefs path-free and unchanged, but the
route-switch CAS must prove every live root is present/authentic in the new route and match the same
privacy-root generation/digest; otherwise the old route remains active.

Taskless v0.1 network rows are structural `channel_unavailable` decisions only. Objectless agent-
projection rows may also be taskless when the owning control result has machine/workspace scope;
they retain the exact service-allocated audit request identity and control-RPC binding specified by
the domain/port contract and never invent a task. A future taskless content-bearing channel remains
unavailable until a separately reviewed installation-scoped encrypted audit-object owner, key,
storage, retention, and recovery contract exists.

A same-installation route move holds exclusive maintenance authority, verifies the complete current
root set in the target, invalidates every nonterminal approval/authorization under the new owner
generation, completes or repairs pending receipts from durable evidence, and only then CASes the
route identity plus unchanged privacy-root generation/digest. It cannot carry live dispatch authority
across the switch.

## Invariants

1. Catalog plaintext contains no candidate, preview, transmitted, response, or credential bytes.
2. Authorization consumption is atomic and at most once.
3. Every outbound dispatch identity reaches `attempt_completed` with exactly one terminal
   structural receipt; no `dispatched` receipt outcome exists.
4. Taskless audit never fabricates task identity.
5. Policy generation is monotonic and all expansions retain human-decision provenance.
6. Initial reservation failure is the sole no-receipt decision exception and is necessarily
   pre-dispatch; every committed terminal audit decision is receipted. Pending/approved proposal
   state is not a terminal receipt.
7. Content-bearing audit objects are bundle encrypted and task owned; taskless v0.1 audit contains
   no content object.
8. Catalog privacy ObjectRefs are live roots independent of task-ledger inventory and remain roots
   until an explicit owning privacy transaction clears them; none exists in v0.1.
9. Agent projections retain only keyed commitments/structural field decisions and never enter the
   privacy object root set; receipt inspection never dereferences proposal content.

## Tests

Memory/SQLite contract parity, all three audit-subject branches, crash points around every
object/transaction boundary, initial reservation failure, post-reservation `audit_failed`,
`receipt_pending` repair, generation races, expiry, duplicate dispatch, taskless structural receipt,
atomic agent projection/local-model consume and replay, receipt query pagination/filter isolation,
canary sweep, root-generation/GC races, backup/restore/route move, dangling-ref quarantine, and
corruption fail-closed behavior are covered.

## Open questions

None.
