# src/yoetz/adapters/importers/codex_jsonl.py — strict Codex JSONL parser and conservative event mapper

**Wave:** D | **ADRs:** ADR-002, ADR-004, ADR-005 | **Imports (spec-tree):**
`protocol/canonical.md`, `protocol/coverage.md`, `protocol/errors.md`, `domain/events.md`,
`domain/values.md`, `ports/importer.md` | **Imported by:** concrete memory/SQLite importer
adapters, importer unit/integration/capability tests

## Purpose

This module is the pure, version-pinned parser and mapper for the public
`codex exec --json` stream. It turns exact bounded source bytes into source-range-aware mapping
templates, then materializes those templates into `ImportLineOutcome`, `ImportEventCandidate`,
and `ImportGap` values after a persistence adapter supplies stable IDs.

It does **not** implement `ImporterPort`. It opens no file, encrypts or stores no object, allocates
no ID, owns no lease, appends no event, builds no `ImportReport`, reads no live repository, and
runs no comparative review. Capture, durable planning, batching, publication, report evidence,
resume, and review snapshots belong to `ports/importer.md` and its concrete persistence adapters.
Keeping this file pure lets the same exact parser/mapping corpus run against memory and SQLite
without making the parser an alternate state machine.

## Public surface

- `CODEX_JSONL_MAPPING_VERSION = "codex-jsonl/1.0.0"`.
- `CODEX_OPAQUE_SCHEMA = EventSchema("codex_jsonl_observation", "1.0.0")` — deliberately
  unregistered, therefore always persisted as `unknown_unprojected`; it is not a seventeenth
  known event family.
- `SUPPORTED_CODEX_PROFILES: Mapping[str, CodexCapabilityProfile]` — immutable map keyed by exact
  CLI version, never a range.
- Frozen adapter values:
  - `CodexCapabilityProfile` — exact CLI version, profile ID, contract/fixture digest, accepted
    wrapper/item shapes, and profile limits;
  - `CodexSourceLine` — one physical source line with 1-based ordinal and exact half-open range;
  - `CodexParsedRecord` — validated wrapper/item record whose representation is redacted;
  - `CodexParseResult` — ordered parsed records plus line classifications and stream-shape gaps;
  - `CodexMappingContext` — verified source object/commitment, capture observation time, profile,
    mapping version, and importer coverage baseline;
  - `CodexCandidateTemplate` — payload blueprint with local symbolic IDs/references and source
    ranges, but no allocated Yoetz ID;
  - `CodexMappingTemplate` — ordered line outcomes, candidate templates, gaps, and bounded report
    facts;
  - `CodexMaterializationIds` — exact caller-allocated IDs keyed by template-local key;
  - `CodexPreparedMapping` — materialized port values ready for deterministic batching;
  - `SanitizedCodexArgv` — bounded encrypted-audit argv representation plus omission codes.
- Pure functions:
  - `profile_for_codex_version(version: str) -> CodexCapabilityProfile`;
  - `parse_codex_jsonl(source: bytes, profile: CodexCapabilityProfile) -> CodexParseResult`;
  - `plan_codex_mapping(parsed: CodexParseResult,
    context: CodexMappingContext) -> CodexMappingTemplate`;
  - `materialize_codex_mapping(template: CodexMappingTemplate,
    ids: CodexMaterializationIds) -> CodexPreparedMapping`;
  - `sanitize_codex_argv(argv: Sequence[str]) -> SanitizedCodexArgv`.

The adapter values are implementation-facing and never public request/result models. Every value
that can contain source-derived text has constant redacted `repr`/`str`; diagnostic views expose
only profile IDs, counts, ordinals, ranges, categories, and allowlisted gap codes.

## Behavior

### Exact profile admission

`profile_for_codex_version` performs exact ASCII-string lookup. A profile is admitted only when an
installed-artifact capability run has frozen all of these facts:

- exact `codex-cli` version and executable provenance;
- the closed top-level/item union and field/status shapes observed for that version;
- public synthetic fixture bytes and expected parse/mapping outcomes;
- a digest over the profile declaration and fixture manifest;
- the tested platform/config feature set that produced the stream.

The baseline declaration is `codex-cli 0.139.0`, profile ID
`codex-exec-jsonl/0.139.0/v1`, matching that release's public `exec_events` union. Target
`0.144.5`, or any later version, receives its own profile only after the same installed-artifact
corpus passes; it is never inferred from 0.139.0, a minimum/maximum interval, SemVer compatibility,
or successful parsing of one sample. The release build freezes `SUPPORTED_CODEX_PROFILES` and its
digest in `VersionManifest`; absence is an internal `unsupported_codex_profile` result that the
`ImporterPort` adapter maps to `INVALID_REQUEST` before planning.

For profile `codex-exec-jsonl/0.139.0/v1`, the exact top-level tagged union is:

| `type` | Required body after `type` |
|---|---|
| `thread.started` | `thread_id: string` |
| `turn.started` | no fields |
| `turn.completed` | `usage` with signed-64-bit integer `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_output_tokens` |
| `turn.failed` | `error: {message: string}` |
| `item.started` | `item: Codex item` |
| `item.updated` | `item: Codex item` |
| `item.completed` | `item: Codex item` |
| `error` | `message: string` |

The exact item union has an outer `id: string`, `type`, and these bodies:

| Item `type` | Required body after outer `id`/`type` |
|---|---|
| `agent_message` | `text: string` |
| `reasoning` | `text: string` |
| `command_execution` | `command: string`, `aggregated_output: string`, `exit_code: int|null`, `status: in_progress|completed|failed|declined` |
| `file_change` | `changes: [{path: string, kind: add|delete|update}]`, `status: in_progress|completed|failed` |
| `mcp_tool_call` | `server: string`, `tool: string`, `arguments: JSON`, `result: result|null`, `error: {message}|null`, `status: in_progress|completed|failed` |
| `collab_tool_call` | `tool: spawn_agent|send_input|wait|close_agent`, `sender_thread_id: string`, `receiver_thread_ids: [string]`, `prompt: string|null`, `agents_states: object`, `status: in_progress|completed|failed` |
| `web_search` | inner search `id: string`, `query: string`, profile-closed `action: JSON` |
| `todo_list` | `items: [{text: string, completed: bool}]` |
| `error` | `message: string` |

For MCP results the profile closes `content` to a bounded JSON array, optional `_meta`, and
optional `structured_content`. The web-search action and collaboration state unions are validated
against the exact fixture-backed 0.139.0 declarations, but this mapper does not interpret their
provider-specific bodies. Field order is irrelevant. An extra field is accepted only when the
profile explicitly marks it inert; otherwise the record is `unsupported` and becomes opaque plus
a gap. Thus a source format extension cannot silently affect an old mapping.

### Physical-line splitting and strict JSON

`parse_codex_jsonl` is deterministic over `source` bytes and does not mutate or retain the caller's
buffer after returning:

1. Defense-in-depth reject a source above the port's exact 4 MiB source cap. The persistence
   adapter normally enforces this before calling the parser.
2. Split only on byte `0x0A`. Each `CodexSourceLine` range starts at the first byte of the physical
   line and ends after its LF, or at EOF for the final unterminated line. CRLF is recognized by
   removing one terminal CR for JSON decoding while the range still covers both terminator bytes.
   Bare CR is ordinary JSON whitespace/data and is never normalized.
3. Enforce the profile's aggregate line-count and per-line byte caps before JSON decode. An
   oversized physical line is classified `ImportLineStatus.oversized`, retained only by source
   object/range, and parsing continues at the next LF. Aggregate cap failure aborts plan creation
   with `LIMIT_EXCEEDED`; silently dropping the tail is forbidden.
4. Decode each non-oversized content slice as strict UTF-8 and strict JSON: no BOM, NUL, surrogate,
   duplicate object key, NaN/infinity, or trailing token; top level must be an object. Blank,
   invalid-UTF-8, duplicate-key, or syntactically incomplete lines are `malformed`, not exceptions
   containing source text.
5. A syntactically valid final JSON object without LF is valid but adds
   `final_newline_absent`. A syntactically incomplete final line is `malformed` with
   `truncated_final_line`; missing LF alone is never called truncation.
6. Validate the top-level tag and exact profile shape. Unknown tag is `unknown`; known tag/item
   with a non-profile shape, invalid enum, wrong scalar type, overflow, or undeclared semantic
   field is `unsupported`. JSON integers stay integers; booleans are not integers and floats are
   never accepted into known mapped fields.

Every physical line receives exactly one preliminary classification and range. Safe parser reason
codes come from a closed table; decoder exception strings, object keys not in the profile, string
values, and source fragments never enter a result/log.

### Stream and item folding

`plan_codex_mapping` validates cross-line structure before selecting known event families:

- `thread.started` is normally first and unique; a missing, late, or conflicting thread marker
  adds a gap but does not invalidate independently observed items.
- A turn begins with `turn.started` and ends once with `turn.completed` or `turn.failed`.
  Concatenated streams, a second terminal marker, and items outside the observed turn are opaque
  with explicit gaps. Token usage is bounded report metadata only.
- Item state is keyed by the source's outer item ID only inside this one captured stream. Allowed
  progression is profile-specific `started -> updated* -> completed`; completed-only items are
  allowed where the Codex profile emits them. Exact duplicate snapshots are coalesced for semantic
  mapping but remain separate line outcomes. Type changes, terminal regression, command/tool/path
  identity changes, or incompatible result/error combinations make the affected item opaque.
- Item IDs, thread IDs, sender/receiver IDs, and model labels are observed source values, never
  Yoetz IDs or authenticated actor identities.
- Candidate order is first source ordinal, then `action_recorded` before its
  `result_recorded`. A result template locally references its action template. No causal edge is
  inferred between unrelated items merely because they share a turn.

Templates use local keys such as `line-000042/action` rather than IDs. This makes planning pure.
The persistence adapter allocates fresh UUIDv4-backed event/action/result IDs once, builds
`CodexMaterializationIds`, and calls `materialize_codex_mapping`. Materialization requires exactly
one correctly prefixed ID for every declared local key, rejects extras/missing/duplicate IDs, then
resolves payload refs and result causal parents. It performs no randomness, IO, encryption, or
persistence. A retry after durable `publish_plan` reopens the already materialized encrypted plan;
it never reruns this function with new IDs.

### Conservative semantic mapping

Every candidate is importer-authored, uses `publication_channel=codex_jsonl_import`, begins from
`coverage_for_channel(codex_jsonl_import)`, has authorship no stronger than
`harness_observed`, artifact observation no stronger than `import_observed`, and references the
verified `import_source` object. Public Codex JSONL has no source occurrence timestamp, so
`EventDraft.occurred_at` is the capture observation time from `CodexMappingContext` and every
candidate carries `source_timestamp_unavailable`; it is never described as execution time.

The closed v1 mapping is:

| Folded Codex item | Known Yoetz candidates | Binding limitations |
|---|---|---|
| `command_execution` | one `action_recorded(action_kind=command)`; one `result_recorded` only for a terminal source status | command is exact encrypted payload text; result is `success` only for coherent `completed` + exit 0, `failure` for coherent failed/nonzero, otherwise `unknown` + gap; output may be bounded summary but is not evidence |
| `file_change` | one `action_recorded(action_kind=edit)` and terminal `result_recorded` | ordered `{kind,path}` changes describe reported intent/effect only; no `SubjectStateRef`, content digest, or evidence is invented; always `file_content_not_captured` |
| `mcp_tool_call` | one `action_recorded(action_kind=other)` and terminal `result_recorded` | server/tool are descriptive only; arguments/result remain source-linked, are not admissible evidence, and cannot authenticate the MCP server |
| `collab_tool_call` | one `action_recorded(action_kind=other)` and terminal `result_recorded` | source thread/agent labels are unverified; no `assignment_recorded` is created because obligation IDs and authority are absent |
| `web_search` | one `action_recorded(action_kind=research)` and, on `item.completed`, one execution `result_recorded` | query is observed, but returned sources/support are not captured as Yoetz evidence; always `web_results_not_captured` |

An in-progress item at EOF may yield its supported action plus `item_terminal_missing`, but never a
result. A declined command yields an action and an `unknown` result with
`command_declined_not_executed`. An output/error string is copied into a result summary only when
it fits the registered payload bound; otherwise it remains recoverable from the encrypted source
range and adds `source_text_not_represented`. No truncation is performed silently.

The following are deliberately **not** coerced into known families:

- thread/turn lifecycle does not become `session_opened`/`session_resumed` because task/session,
  profile, writer, and frontier facts are absent;
- `agent_message` and `reasoning` do not become `claim_recorded` because the public stream does
  not classify completion/material claims or their support;
- `todo_list` does not become `plan_published`/`plan_revised` because it lacks Yoetz plan version,
  obligation identity, authority, and supersession semantics;
- top-level/item errors do not become action results without a supported action identity;
- token usage and provider/web/MCP bodies do not become evidence.

Each such valid record, plus every unknown/malformed/oversized/unsupported record, produces one
`CODEX_OPAQUE_SCHEMA` template and at least one `ImportGap`. The opaque payload contains only
profile ID, mapping version, source object ID, ordinal/range, line classification, allowlisted
known source category when available, and gap codes; `artifact_refs` contains the source object.
It contains no raw line, message, prompt, command, path, arguments, output, agent label, or ordinary
source digest. Because the schema is intentionally outside `EVENT_FAMILIES`, the ledger stores it
as `unknown_unprojected`; reducers cannot mistake it for a known fact. This is opaque preservation,
not a new known event family.

Known candidates and opaque candidates are mutually exclusive per semantic source record, except
that multiple physical snapshots of one known item may point to the same folded candidates. Every
line outcome lists exactly the candidate indexes it supports. Every candidate and gap has at least
one source range, and the union of line outcomes covers the exact physical source bytes.

### Local retention and disclosure scanning

Import does not run a content secret scanner or destructively redact the encrypted source/payload.
That is deliberate: a command or model message is local retained evidence at this stage, and its
future disclosure purpose, sink, scope, and policy do not yet exist. Exact source text remains only
in encrypted source/payload objects; structural line outcomes, candidates, reports, diagnostics,
and argv metadata remain allowlisted and plaintext-free as specified above.

This is not an egress exemption. Every imported item has `DisclosureProvenance.imported`, and any
later candidate for external `llm_inference`, `local_model`, `agent_context`, or `local_human_view`
disclosure must traverse
the same `PrivacyClassifierPort` source rules and `scan_exact_bytes` fence as native content. An
external provider body also receives the gateway's second exact-body scan. The versioned scanner
fixture set includes Codex-shaped shell assignments, inline authorization/header flags, bearer/API
key forms, credential-bearing URLs, and values split across JSON/UTF-8/chunk boundaries. A match
blocks the disclosure; import never promotes successful local encryption into permission to reveal
the bytes.

### Argv sanitization

`sanitize_codex_argv` is a pure allowlist, not secret-pattern guessing:

- preserve only command-category tokens (`exec`, `resume`, `review`), boolean flags such as
  `--json`, `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, and
  `--skip-git-repo-check`, plus validated enum values for `--sandbox` and `--color`;
- retain option names but replace values for prompt/positional input, `--config/-c`, `--cd/-C`,
  `--add-dir`, `--image`, `--output-schema`, `--output-last-message`, `--profile`, `--model`,
  resume/session selectors, feature names, and unknown options with a constant redaction token;
- never inspect files, expand shell syntax, read environment variables, or retain removed values;
- reject NUL/surrogate input, cap argument count and aggregate UTF-8 bytes, and return only
  allowlisted omission codes when material was removed.

The returned value is suitable only for an encrypted capture-metadata object. Structural import
state records at most its keyed commitment and safe category/count fields, never sanitized argv
text itself.

## Errors and edge cases

- Unsupported exact version/profile or profile-digest mismatch is whole-source
  `unsupported_codex_profile`; the persistence adapter maps it to `INVALID_REQUEST` before plan
  publication. There is no closest-profile fallback.
- A source or aggregate line/candidate cap breach is `LIMIT_EXCEEDED`. One range-identifiable
  oversized line remains an opaque observation/gap when aggregate limits still permit a plan.
- Malformed, unknown, unsupported, unterminated, or contradictory lines are data outcomes, not
  adapter crashes and not job quarantine.
- A valid top-level error or non-fatal `item.type=error` remains opaque; this module never infers
  process success/failure from error text. Final turn status, source-process exit status, and
  stderr metadata remain distinct facts in the import report.
- A terminal item with missing/inconsistent status/result fields maps only the facts still
  supported; it never upgrades the outcome. If the action identity itself is unstable, the whole
  item becomes opaque.
- Unknown JSON may contain floats or arbitrary nested values because exact bytes remain encrypted;
  the opaque payload never copies them into Yoetz canonical JSON.
- No exception/error/repr includes source bytes, decoded values, paths, argv, prompts, tool output,
  source IDs from Codex, or a raw SHA-256 digest.

## Invariants

1. The same exact source bytes, capability profile, mapping version, context, and materialization
   IDs produce byte-equivalent port values under every Python hash seed and chunking history.
2. Every source byte belongs to exactly one physical line range; every mapped/opaque fact is
   traceable to one or more of those ranges.
3. Only command, file-change, MCP, collaboration, and web-search items enter known event families,
   and only with the exact limitations above.
4. Everything not fully supported is opaque plus an explicit gap; no nearby-family coercion or
   silent line loss exists.
5. Source actor/item/thread IDs never become Yoetz IDs or authenticated authorship.
6. The module performs no IO, persistence, encryption, ID allocation, event append, model/network
   call, repository inspection, or review orchestration.
7. Raw source/argv text and ordinary source digests never enter structural output or diagnostics.
8. Import-time retention performs no content secret scan by design; every later disclosure of
   imported content is independently classified and scanned at the single sink boundary.

## Tests

- `specs/tests/unit.md`: byte-range splitting for LF/CRLF/final-no-LF, strict UTF-8/JSON,
  duplicate keys, aggregate/per-line caps, exact 0.139.0 union validation, transition folding,
  mapping/materialization, ID-ref resolution, argv allowlist, and redacted reprs.
- `specs/tests/capability/test_codex_jsonl_import.py.md`: installed exact-version corpus proves every
  wrapper/item/status shape and freezes each profile/fixture digest before support is advertised.
- `specs/tests/integration/application/test_import_review.py.md`: parser output flows through the
  real importer publication path and remains source-linked/import-bounded.
- `specs/fixtures/imports/codex/supported-version.case.json.md`: all 0.139.0 top-level/item variants,
  coherent/contradictory transitions, expected templates, candidates, gaps, ranges, and coverage.
- `specs/fixtures/imports/codex/unknown-events.case.json.md` and
  `malformed-lines.case.json.md`: forward-unknown, extra-field, duplicate-key, invalid-UTF-8,
  oversized, and opaque-preservation vectors.
- `specs/fixtures/imports/codex/truncated-stream.case.json.md`: missing terminal/final LF behavior.
- `specs/fixtures/imports/codex/secret-redaction.case.json.md`: encrypted exact retention,
  structural plaintext-canary absence, and disclosure-time rejection of Codex-shaped secret forms.

## Open questions

None.
