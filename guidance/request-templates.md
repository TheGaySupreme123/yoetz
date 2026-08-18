# Yoetz request templates

These are complete request bodies for the six Yoetz operations and all nine ordinary
`publish_work` event families. Use them when a host preserves resource text but drops schema
metadata. The operation input schema remains the field-shape authority; these templates are an
authoring fallback, not a second protocol.

## Replace the illustrative values first

Every `req_`, `evt_`, `ses_`, `wri_`, `tsk_`, `obl_`, `act_`, `res_`, `evd_`, `clm_`, and `fnd_`
value below is a shape-valid placeholder. Replace it with the real fresh or returned identifier
required by the operation. Also replace every illustrative task string, reference, timestamp,
frontier sequence, and digest. A frontier must come from the latest accepted result or `status`;
`head_digest: "genesis"` is valid only with `sequence: "0"`. Keep canonical integers such as
frontier sequences, limits, and `max_findings` as JSON strings.

Use the real client identity for your integration. Never copy a template timestamp as if it were
the event time; `occurred_at` is your assertion and should be the best real UTC millisecond time
available. Before a material publication, send the completed `publish_work` request once with
`dry_run: true`; after a successful preview, reuse the same `request_id` without `dry_run` (or with
`dry_run: false`).

## `start`

Use `create_or_attach` with a stable workspace/work-item pair when first opening or resuming the
same work. Alternatively, attach with a returned `session_id`; never use a bare `task_id` as an
attach selector.

```json
{
  "protocol_version": "0.1",
  "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000001",
  "mode": "create_or_attach",
  "task_title": "Replace with the bounded task title",
  "workspace_ref": "https://github.com/example/project",
  "external_ref": "issue-128",
  "requested_view": "compact",
  "actor": {
    "actor_id": "harness:mcp-template",
    "actor_type": "harness"
  },
  "client": {
    "kind": "cooperative_agent",
    "version": "0.1.0",
    "integration": "cooperative_mcp"
  }
}
```

## `publish_work`: plan plus obligation

This is the first material publication. Put the requested outcome and its acceptance evidence in
`description` and `acceptance_criteria`; do not turn routine file mechanics into obligations.

```json
{
  "protocol_version": "0.1",
  "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000002",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {
    "sequence": "0",
    "head_digest": "genesis"
  },
  "event_drafts": [
    {
      "event_id": "evt_00000000-0000-4000-8000-000000000001",
      "schema": {"name": "plan_published", "version": "1.0.0"},
      "occurred_at": "2026-01-01T00:00:00.000Z",
      "causal_parents": [],
      "payload": {
        "plan_version": 1,
        "summary": "Replace with the bounded implementation plan",
        "obligation_refs": ["obl_00000000-0000-4000-8000-000000000001"]
      },
      "artifact_refs": [],
      "evidence_refs": []
    },
    {
      "event_id": "evt_00000000-0000-4000-8000-000000000002",
      "schema": {"name": "obligation_published", "version": "1.0.0"},
      "occurred_at": "2026-01-01T00:00:00.000Z",
      "causal_parents": [],
      "payload": {
        "obligation_id": "obl_00000000-0000-4000-8000-000000000001",
        "description": "Replace with the outcome this work owes",
        "acceptance_criteria": "Replace with an observable acceptance criterion",
        "evidence_expectation": "Replace with the named test, reviewed diff, or other evidence",
        "status": "open",
        "requested_items": [{"item_kind": "command", "value": "pytest -q"}]
      },
      "artifact_refs": [],
      "evidence_refs": []
    }
  ],
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {
    "kind": "cooperative_agent",
    "version": "0.1.0",
    "integration": "cooperative_mcp"
  }
}
```

The two drafts above cover `plan_published` and `obligation_published`. The next seven requests
show the remaining ordinary families. Replace the frontier in each; the genesis values only keep
each standalone example schema-valid.

`requested_items` declares the concrete material items the obligation asks for; each entry is an
object whose `value` is the exact item string. `item_kind` admits exactly `change`, `command`,
`file`, `source`, or `url`: use `change` for a deliverable change, and the other values for the
corresponding concrete item. The requested outcome belongs in `description` and
`acceptance_criteria`; `outcome` is not an admitted `item_kind`.

When you later attempt an item, copy that exact `value` string into `attempted_items` on the
`action_recorded` event that attempted it — see the action template below. Matching is exact: do
not normalize, reorder words, or paraphrase.

### Alternate `plan_published`: explicitly no obligations

Use this shape only when the effective plan genuinely has no obligation refs. The typed reason
clears the readiness blocker but a later completion claim still receives
`completion_scope_declared_none`; it is a declaration, not clean-coverage evidence. Never send the
reason beside nonempty `obligation_refs`.

```json
{
  "protocol_version": "0.1",
  "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000015",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "event_drafts": [{
    "event_id": "evt_00000000-0000-4000-8000-000000000015",
    "schema": {"name": "plan_published", "version": "1.0.0"},
    "occurred_at": "2026-01-01T00:00:00.000Z",
    "causal_parents": [],
    "payload": {
      "plan_version": 1,
      "summary": "Replace with the bounded obligation-free plan",
      "obligation_refs": [],
      "no_obligations_reason": "single_atomic_change"
    },
    "artifact_refs": [],
    "evidence_refs": []
  }],
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {
    "kind": "cooperative_agent",
    "version": "0.1.0",
    "integration": "cooperative_mcp"
  }
}
```

## `publish_work`: assignment

```json
{
  "protocol_version": "0.1", "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000003",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "event_drafts": [{
    "event_id": "evt_00000000-0000-4000-8000-000000000003",
    "schema": {"name": "assignment_recorded", "version": "1.0.0"},
    "occurred_at": "2026-01-01T00:00:00.000Z", "causal_parents": [],
    "payload": {
      "assignee_actor_id": "harness:mcp-template",
      "obligation_ids": ["obl_00000000-0000-4000-8000-000000000001"],
      "scope_description": "Replace with one independently reviewable work package"
    },
    "artifact_refs": [], "evidence_refs": []
  }],
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

## `publish_work`: decision

`authority` is a structural actor identifier matching `^[A-Za-z0-9._:-]{1,128}$` — it names the
actor who exercised the authority (for example `user:shay` or `harness:cli`), exactly as an
`actor_id` does. It is never a sentence describing the approval; put the approval story in
`rationale` or `statement`. A value that merely satisfies the pattern but names no real actor is
still wrong on the record.

```json
{
  "protocol_version": "0.1", "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000004",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "event_drafts": [{
    "event_id": "evt_00000000-0000-4000-8000-000000000004",
    "schema": {"name": "decision_recorded", "version": "1.0.0"},
    "occurred_at": "2026-01-01T00:00:00.000Z", "causal_parents": [],
    "payload": {
      "statement": "Replace with the material decision",
      "rationale": "Replace with the bounded rationale summary",
      "authority": "harness:mcp-template"
    },
    "artifact_refs": [], "evidence_refs": []
  }],
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

## `publish_work`: action

`action_kind` is a closed enum: `command`, `edit`, `research`, `review`, or `other`. A source or
file modification is `edit` — there is no `code_change` value — and `command` additionally
requires the `command` field.

`attempted_items` belongs to `action_recorded.payload` only; no other family admits it, and the
claim payload in particular stays closed. Each entry copies one attempted obligation
`requested_items` entry's exact `value` string, as in the pairing below.

```json
{
  "protocol_version": "0.1", "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000005",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "event_drafts": [{
    "event_id": "evt_00000000-0000-4000-8000-000000000005",
    "schema": {"name": "action_recorded", "version": "1.0.0"},
    "occurred_at": "2026-01-01T00:00:00.000Z", "causal_parents": [],
    "payload": {
      "action_id": "act_00000000-0000-4000-8000-000000000001",
      "action_kind": "command", "command": "pytest -q",
      "description": "Replace with the material action summary",
      "attempted_items": ["pytest -q"]
    },
    "artifact_refs": [], "evidence_refs": []
  }],
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

The `attempted_items` entry above repeats the obligation template's
`requested_items[0].value` byte for byte. Publish it on the action that attempted the item —
including a failed attempt — so the receipt can account for every requested item.

## `publish_work`: result

```json
{
  "protocol_version": "0.1", "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000006",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "event_drafts": [{
    "event_id": "evt_00000000-0000-4000-8000-000000000006",
    "schema": {"name": "result_recorded", "version": "1.0.0"},
    "occurred_at": "2026-01-01T00:00:00.000Z", "causal_parents": [],
    "payload": {
      "result_id": "res_00000000-0000-4000-8000-000000000001",
      "action_id": "act_00000000-0000-4000-8000-000000000001",
      "outcome": "success", "summary": "Replace with the independently useful result"
    },
    "artifact_refs": [], "evidence_refs": []
  }],
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

## `publish_work`: evidence

```json
{
  "protocol_version": "0.1", "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000007",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "event_drafts": [{
    "event_id": "evt_00000000-0000-4000-8000-000000000007",
    "schema": {"name": "evidence_recorded", "version": "1.1.0"},
    "occurred_at": "2026-01-01T00:00:00.000Z", "causal_parents": [],
    "payload": {
      "evidence_id": "evd_00000000-0000-4000-8000-000000000001",
      "evidence_kind": "test_result", "strength": "content_digest",
      "content_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
      "digest_binding": {
        "subject": "test_stdout",
        "content_availability": "digest_only",
        "byte_count": 4096,
        "provenance": "caller_asserted"
      },
      "observed_at": "2026-01-01T00:00:00.000Z",
      "description": "Caller-authored summary; not the bytes identified by content_digest"
    },
    "artifact_refs": [], "evidence_refs": []
  }],
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

## `publish_work`: claim

The claim payload is closed: it admits `claim_id`, `claim_kind`, `disputes_refs`,
`obligation_refs`, `statement`, `subject_state`, and `supporting_refs` — never `attempted_items`,
which lives on `action_recorded`. Link the obligations a claim answers with `obligation_refs` and
its evidence with `supporting_refs`.

```json
{
  "protocol_version": "0.1", "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000008",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "event_drafts": [{
    "event_id": "evt_00000000-0000-4000-8000-000000000008",
    "schema": {"name": "claim_recorded", "version": "1.0.0"},
    "occurred_at": "2026-01-01T00:00:00.000Z", "causal_parents": [],
    "payload": {
      "claim_id": "clm_00000000-0000-4000-8000-000000000001",
      "claim_kind": "completion", "statement": "Replace with the bounded claim",
      "supporting_refs": ["evd_00000000-0000-4000-8000-000000000001"]
    },
    "artifact_refs": [], "evidence_refs": []
  }],
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

## `publish_work`: revised plan

This example carries an obligation and therefore omits `no_obligations_reason`; omission clears any
earlier empty-scope reason. For a revised effective plan with zero obligation refs, include one
current closed reason. A revision never inherits an earlier reason by omission.

```json
{
  "protocol_version": "0.1", "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000009",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "event_drafts": [{
    "event_id": "evt_00000000-0000-4000-8000-000000000009",
    "schema": {"name": "plan_revised", "version": "1.0.0"},
    "occurred_at": "2026-01-01T00:00:00.000Z", "causal_parents": [],
    "payload": {
      "plan_version": 2, "supersedes_plan_version": 1,
      "reason": "Replace with the material fact that changed the plan",
      "summary": "Replace with the revised bounded plan",
      "obligation_changes": [{
        "obligation_id": "obl_00000000-0000-4000-8000-000000000001",
        "change": "carried"
      }]
    },
    "artifact_refs": [], "evidence_refs": []
  }],
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

## `status`

```json
{
  "protocol_version": "0.1",
  "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000010",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "view": "compact",
  "limit": "10",
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

## `check`: whole case

Omit `scope` for the whole case. Two empty arrays are also whole-case semantics, but omission is
clearer.

```json
{
  "protocol_version": "0.1",
  "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000011",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "mode": "semantic_if_configured",
  "max_findings": "3",
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

## `check`: scoped

If `scope` is present, send both arrays. Either may be empty; two empty arrays mean whole case.

```json
{
  "protocol_version": "0.1",
  "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000012",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "scope": {
    "claim_ids": ["clm_00000000-0000-4000-8000-000000000001"],
    "obligation_ids": ["obl_00000000-0000-4000-8000-000000000001"]
  },
  "mode": "semantic_if_configured",
  "max_findings": "3",
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

## `respond`

Use `finding_frontier` = the result frontier of the `check` that returned the finding, which is the
frontier that carries the finding's own record. The finding's `subject_frontier` names the state the
check tested and precedes that record, so it is rejected. A response records a disposition; it never
erases the finding.

```json
{
  "protocol_version": "0.1",
  "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000013",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "11", "head_digest": "sha256:0b77cea7992de93fe83a6748fbd6b4557b53d965e3fe0d2d8a1f47023d5edb72"},
  "finding_id": "fnd_00000000-0000-4000-8000-000000000001",
  "finding_frontier": {"sequence": "11", "head_digest": "sha256:0b77cea7992de93fe83a6748fbd6b4557b53d965e3fe0d2d8a1f47023d5edb72"},
  "disposition": "acknowledged",
  "reason": "Replace with the bounded disposition reason",
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```

## `receipt`

Read `status.closure_readiness` before requesting a receipt. Respond while
`findings_unanswered` is present. A remaining `receipt_findings_unresolved` condition is a permanent
conclusion bound, not a reason to respond again; request the receipt and keep the final claim no
stronger than its conclusion, coverage, freshness, receipt-blocking findings, and limitations.

```json
{
  "protocol_version": "0.1",
  "schema_version": "1.0.0",
  "request_id": "req_00000000-0000-4000-8000-000000000014",
  "task_id": "tsk_00000000-0000-4000-8000-000000000001",
  "session_id": "ses_00000000-0000-4000-8000-000000000001",
  "writer_id": "wri_00000000-0000-4000-8000-000000000001",
  "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
  "format": "markdown",
  "include": "standard",
  "redaction_profile": "default_local_export",
  "actor": {"actor_id": "harness:mcp-template", "actor_type": "harness"},
  "client": {"kind": "cooperative_agent", "version": "0.1.0", "integration": "cooperative_mcp"}
}
```
