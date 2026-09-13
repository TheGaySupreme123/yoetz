# Code review: PR 706

**PR:** [feat(workflows): add compatible multi-agent tasks and projects](https://github.com/TheGaySupreme123/yoetz/pull/706)
**Branch:** `codex/refresh-0.3-review`
**Head:** `7f875b46e3b6b91ac5a8997763a8dd1ffd931b19`
**Base:** `main` (`b2bef2f4`)
**Reviewer:** Cursor agent
**Date:** 2026-09-13

Reviewed against current `main`. Scope is the load-bearing 0.3 paths: upgrade, consent, admission, lineage receipts, and observation recovery. Generated schema mirrors and skill copies were not line-reviewed. Required CI is green. CodeRabbit and Greptile skipped on size.

No live Yoetz ledger or receipt was available for this review.

**Verdict:** the identity model and most fences look sound. Do not merge until the console project-grant ceremony is fixed. The parent-completion honesty gap is the next item to treat as merge-blocking if agents are expected to close parent tasks from compact `status`.

---

## What holds

- Bundle v12→v13 is backup-first, byte-preserving, and interrupt-safe. `0013.sql` copies `canonical_entry`; a restart after committed DDL does not rebuild twice.
- Privacy lattice stays four-kind. Project membership is catalog admission, not `AuthorizationScope.contains()`. Caller `workspace_ref` cannot select privacy authority.
- Same-workspace implicit coordination still requires catalog-proven provenance plus per-workspace observation consent. General and cross-repository work require an explicit generation grant.
- `workspace_task_exists` is gone from automatic `create_or_attach`. Replacement is pair uniqueness on root tasks, plus `selector_conflict` for drifted selectors. Host `SubagentStart` / Task-tool events stay observations (`host_observed` / `pending`) and do not mint children. Claude `observation_events` stays empty.
- Receipt rollup is one-level. Actionable vs informational child findings cannot be swapped on the wire. Clients do not open child bundles except the service rollup coordinator, and only for accepted children.
- Observation pairing fix (`88d70363`) looks real: PreToolUse-only pairing, canonical DUPLICATE envelopes, ended-session recovery still single-task and same-workspace.

---

## Findings

### 1. Console `consent review` grants, then fails

**Severity:** should-fix (treat as merge-blocking)

`yoetz consent review` is advertised for `project_coordination_grant` (`review_command` is `yoetz consent review`). Approve records the grant first, then builds a review result with default `authority_channel=trusted_console_presence`. Schema v7 and the validator admit only `agent_attested_chat_instruction` for that operation.

```python
# src/yoetz/cli/elevated.py
result = await _complete_approved(console, pending)
# Validate the projected result before the durable approval record exists: a
# result the schema does not admit must consume this review as failed.
payload = _review_result(pending, outcome="completed", result=result)
```

```python
# src/yoetz/cli/elevated.py
if pending.operation == "project_coordination_grant":
    authorization = record_project_coordination_authorization(pending)
    return {
        "project_id": authorization.project_id,
        "membership_generation": str(authorization.membership_generation),
        "audit_record_id": authorization.audit_record_id,
        "outcome": "granted",
    }
```

```python
# src/yoetz/protocol/consent.py
if (
    self.operation == "project_coordination_grant"
    and self.authority_channel != "agent_attested_chat_instruction"
):
    raise ValueError("review_authority_channel_mismatch")
```

The comment is already false: authorization is durable before validation. The exception path then marks the review failed. Result: a live grant plus a failed ceremony, or a human who thinks the grant did not land.

Repository privacy avoids this by refusing console review before mutation (`repository_privacy_grant_requires_yoetz_privacy`). Project grant does not.

**Fix:** allow `trusted_console_presence` on a completed console review, or record the grant only after the result validates. Add a `yoetz consent review` integration test for this operation. Chat authorize already passes the matching channel.

### 2. Parent compact status / receipt can miss live children

**Severity:** should-fix

Receipt and check lineage are replay-only over `child_dependencies_recorded`. `status view=lineage` can show `lineage_manifest_not_recorded` for an accepted catalog child. Compact `closure_readiness` never consults lineage.

```python
# src/yoetz/application/status.py
blocking: list[str] = []
if open_obligations:
    blocking.append("obligations_open")
# ...
if declared_gaps:
    blocking.append("coverage_gaps_declared")
```

`_sweep_lineage` swallows sweep failures and keeps the observation append:

```python
# src/yoetz/application/observation_coordinator.py
async def _sweep_lineage(self, runtime: TaskRuntime) -> None:
    ...
    except Exception as exc:
        # Lineage is an advisory/reconciliation sidecar. The observation ledger remains
        # authoritative...
        record_unexpected_exception_without_raising(...)
```

An agent that follows compact `status` → publish completion → `receipt` can get a clean parent conclusion while `view=lineage` would show an open or unswept child.

Pending children are annotation-only by ADR. That is consistent if documented, but it is easy to misread as “no live child.”

**Fix:** fold catalog-vs-manifest lineage blockers into `closure_readiness` (and/or weaken parent receipt when accepted catalog children have no current manifest). Do not let a failed sweep be invisible on the parent compact path.

### 3. Catalog upgrade is weaker than bundle upgrade

**Severity:** should-fix

Bundle 0013 has a machine-bound backup, phase journal, holder fence, and quarantine. Catalog 0004/0005 — the routing, lineage, and project authority — is a single transactional DDL pass with `maintenance=None` and no pre-migration snapshot.

```python
# src/yoetz/service/ready_composition.py
migration_db = _open_catalog_migration_writer(path)
try:
    run_migrations(migration_db, CATALOG_MIGRATIONS, maintenance=None)
```

0004 also backfills every legacy session as `contact_lost`. Conservative, but it changes recovery and coordination at first READY after upgrade.

**Fix:** backup-first catalog upgrade, or an explicit fail-closed “no other catalog writer” gate plus a documented health reconciliation pass.

### 4. Source policy denial is labeled as missing consent

**Severity:** should-fix

```python
# src/yoetz/application/projects.py
own_consent = await self._source_workspace_consent(task, workspace)
if own_consent is not True:
    raise ProjectCommandError(CoordinationErrorCode.CONSENT_REQUIRED)
if not await self._coordination_source_allowed(task, workspace, descriptor.project_id):
    raise ProjectCommandError(CoordinationErrorCode.CONSENT_REQUIRED)
```

Missing observation consent and an ADR-009 source-policy refusal share one code. That blurs F-021 workspace observation consent and `prj_` coordination.

**Fix:** split the error (for example `coordination_source_policy_denied` vs `coordination_consent_required`).

### 5. Quarantined pair can still be selected

**Severity:** should-fix

`_resolve_requested_route` looks up `(workspace_ref_commitment, external_ref_commitment)` with no `state != 'quarantined'` filter. Automatic attach / `create_or_attach` on a quarantined pair may resume that route instead of a typed refusal or an explicit sibling hatch.

**Fix:** treat a quarantined pair match like an absent route for attach / `create_or_attach` (typed refusal), or document and test the attach failure path.

---

## Smaller notes

- `LineageCoordinator.decide_admission` encodes the #497 table but is unused. Production lives in the catalog and hooks. Drift risk; wire it or lock conformance against the table.
- Memory catalog pair collision omits `reason_code=workspace_task_exists` that SQLite emits on explicit `mode=create`.
- `project_operations` has no SQL phase-monotonicity trigger. Adapter only rejects backward moves; `complete()` accepts any non-completed phase.
- Check child preview drops rollups with no snapshot; receipt keeps the row. Align them.
- Observation `structural_json` stores full envelope wire. Bounded metadata, not file bodies, but broader than digest-only structural tables.

---

## Suggested merge bar

1. Fix the console project-grant review/schema mismatch and add a test.
2. Make compact parent readiness honest about unswept or live accepted children.
3. Either add catalog upgrade backup parity or write the accepted residual risk into `docs/storage-ownership.md` / the upgrade runbook.
