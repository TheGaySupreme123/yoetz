# 06 — Obligation resolution must be authorable without reducer archaeology

**Severity:** medium  
**PR boundary:** obligation resolution invariant, typed rejection, and packaged guidance

## The defect

An obligation is resolved by publishing another `obligation_published` event for the same
`obligation_id`. The reducer admits the transition only when:

- prior status is `open`;
- next status is `resolved`;
- every meaning-bearing field is identical after normalizing status to `open` and clearing
  `resolution_evidence_refs`.

Therefore `description`, `acceptance_criteria`, `evidence_expectation`, `requested_items`, and
`source_refs` must repeat exactly. The dogfood omitted `acceptance_criteria` and shortened
`evidence_expectation`.

The dry-run response said `invalid_event_value_type`, with no draft index or field. The durable
in-memory reproduction was worse: raw `event_invalid` with no details. Packaged guidance contains
no resolution example or repetition rule.

## Design

### 1. Keep the existing meaning invariant

For v0.1, resolution is a state transition, not an edit. Only `status` and
`resolution_evidence_refs` may differ. Meaning changes require a new obligation or an explicitly
designed revision contract, not silent mutation during resolution.

### 2. Raise a typed domain rejection

Replace generic `projection_corrupt` / `invalid_event_value_type` collapse with a typed bounded
reason such as `obligation_resolution_mismatch`.

Carry:

- draft index;
- safe pointer `/event_drafts/{i}/payload`;
- fixed list of differing schema field names;
- invariant code `meaning_fields_must_repeat`.

The list contains only allowlisted schema-owned names, never submitted values.

Dry-run, in-memory durable append, and SQLite durable append must project the same public error.

### 3. Add canonical guidance and example

`publication-policy.md` and the publish tool presentation schema must show:

1. the original open obligation;
2. the resolution event repeating meaning fields byte-for-byte;
3. `status: resolved`;
4. bounded `resolution_evidence_refs`.

The error points to that packaged guidance URI and example family.

### 4. Pin evidence-ref semantics

Resolution is a one-way `open → resolved` transition. The resolving event supplies the final
bounded `resolution_evidence_refs`; no later resolved-to-resolved mutation is admitted. Tests lock
this behavior so “cleared for comparison” cannot be mistaken for freely mutable history.

## Files

- `src/yoetz/kernel/reducers.py`
- `src/yoetz/application/publish_work.py`
- domain error/value type owner
- `src/yoetz/resources/guidance/publication-policy.md`
- publish presentation schema/examples and resource manifest
- unit, integration, adapter-conformance, and MCP tests

## Tests

- Exact repeat plus `resolved` and evidence refs is accepted.
- Omitting or changing each meaning field independently returns
  `obligation_resolution_mismatch` and the exact safe field name.
- Changing only `status` and `resolution_evidence_refs` is accepted.
- Open-to-open duplicate, resolved-to-resolved mutation, and resolved-to-open remain rejected.
- Dry-run, memory, and SQLite paths produce the same bounded error contract.
- The worked example validates against the published schema and packaged manifest.
- No original field value appears in error, logs, or structural tables.

## Done

An agent can correct an invalid obligation resolution from the public error and packaged example on
the next attempt.

## Dogfood observable

The acceptance run intentionally omits one repeated meaning field, receives the exact fixed-field
diagnostic, corrects it, and closes the obligation without reading repository source.

## Out of scope

Editable obligation revisions, partial resolution, reopening, or append-only accumulation of
resolution evidence across multiple transitions.

