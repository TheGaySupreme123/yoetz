# scripts/generate_capability_matrix.py — aggregate redacted external capability evidence

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/tests/capability.md`, `specs/src/yoetz_core/version.md` | **Imported by:** capability and
tagged-release workflows, release-evidence generator

## Purpose

Turn per-case capability evidence from installed-artifact tests into one deterministic, honest
support matrix. The script aggregates observations; it never runs Codex, MCP, providers, keyrings,
or product operations and never upgrades untested versions into support claims.

## Public surface

- `load_evidence(paths) -> tuple[CapabilityEvidence, ...]`;
- `validate_evidence(record, candidate) -> None`;
- `aggregate_capabilities(records, policy) -> CapabilityMatrix`;
- `render_json(matrix) -> bytes` and `render_markdown(matrix) -> bytes`;
- `main(argv=None) -> int`.

Command:

```text
uv run --locked python scripts/generate_capability_matrix.py \
  --candidate-manifest dist/candidate.json \
  --policy release/capability-policy.json \
  --evidence-dir dist/capability-evidence \
  --json-out dist/release-evidence/capability-matrix.json \
  --markdown-out dist/release-evidence/capability-matrix.md
```

`--check` compares outputs without writing; `--write` writes atomically. Exit `0` complete/pass,
`1` invalid/incomplete/claim failure, `2` invocation error.

## Behavior

### Inputs

The candidate manifest fixes package/artifact/resource/commit/test revision and advertised platform
identities. Policy names every required capability case, exact external version/platform pairs,
allowed outcome for claim, evidence freshness window, and whether a bounded live case is required.
Both are canonical JSON, public-boundary scanned, and supplied explicitly.

Evidence files use the `CapabilityEvidence` shape from `tests/capability.md`. Enumerate regular
non-symlink `.json` files in ASCII path order under the explicit evidence directory with count/size
caps. Reject duplicate keys, floats, unknown schema fields, unsupported versions, duplicate evidence
identity, and paths outside the directory.

### Record validation

For every record verify schema/case/requirement IDs, artifact and resource digests, OS/CPU/ABI/
Python/APSW/SQLite identities, external tool/protocol/SDK identity, sanitized integration channel,
timestamps/duration, outcome, evidence locator digest, limitation codes, test revision, and record
self-digest. It must belong to the exact candidate and tested platform; stale or different-artifact
evidence cannot be mixed.

Allowed outcomes are `pass|fail|unsupported|inconclusive`. A pass must contain all required
observations and no contradictory limitation. A raw transcript, prompt, source payload, command
output, credential, absolute path, environment, user/repository/customer name, or arbitrary error
message is forbidden. Private encrypted evidence may be referenced only by opaque digest/locator ID.

### Aggregation

Group records by capability family, external version, platform, artifact digest, and required case.
For each cell:

- `supported` only when every policy-required case passes for this exact cell;
- `failed` when a required case fails;
- `unsupported` when observed unsupported or policy denies it;
- `inconclusive` for outage/timeout/missing trusted observation;
- `untested` when no valid record exists.

The ordering is conservative: supported is never inferred from neighboring versions, semantic
version ranges, documentation, newest/oldest endpoints, another OS/ABI, source-tree tests, or a
different artifact digest. If policy advertises an exact version set, list only passing exact cells.
If a future range policy exists, every required sampled/intermediate rule must be explicit and
matrix output must state the inference rule; v0.1 defaults to exact tested versions.

Summaries include tested/denied/untested Codex versions, MCP protocol/SDK, platform/filesystem/
key-backend cells, optional provider profile cells, observed limitations, test timestamp range,
candidate digests, and evidence completeness. Provider live outage can leave provider capability
inconclusive while strict-local remains supported only if policy explicitly separates those claims.

### Rendering

JSON uses schema `yoetz.capability-matrix/1`, canonical key order, ASCII-sorted rows, exact counts,
candidate identity, policy digest, input evidence-set digest, generated matrix digest, and no
generation timestamp beyond evidence observation bounds. Markdown is a deterministic human view of
the JSON: tables and bounded limitation codes only, no freeform copied messages.

The script writes JSON and Markdown into a staging directory, rescans them for public-boundary
violations, and atomically replaces both. It reads no network/clock/Git/env except explicit inputs.
Re-running with identical inputs yields identical bytes.

### Gate result

After rendering, compare candidate claims with cells. Missing required evidence, any advertised
failed/unsupported/inconclusive/untested cell, mixed candidate identities, or public-boundary hit
exits `1`. A narrower claim may pass after reviewed policy/manifest change; the tool cannot waive.

## Errors and edge cases

- Duplicate/conflicting pass and fail evidence for one identity makes the cell inconclusive and the
  release gate fails pending investigation.
- Evidence outside freshness policy is retained as historical but cannot support the candidate.
- Clock skew/negative duration/finish-before-start invalidates a record.
- Live provider costs/secrets are never consumed here; their harness owns the bounded run.
- Empty evidence directory and parser/scan failures are blocking, not empty-success matrices.
- Diagnostics contain IDs, outcome codes, paths relative to the evidence root, and digest prefixes
  only.

## Invariants

1. Support is tied to exact installed bytes, platform, and external version observations.
2. Untested or inconclusive never becomes supported through inference.
3. Aggregation is deterministic and read-only with respect to test/evidence inputs.
4. Public outputs contain no raw transcripts, prompts, payloads, credentials, or local paths.
5. Matrix claims and release candidate claims must agree exactly.

## Tests

- `specs/tests/unit.md`: record validation, cell aggregation, ordering, rendering and digests.
- `specs/tests/property.md`: shuffled/duplicated/conflicting inputs and deterministic output.
- `specs/tests/capability.md`: real redacted evidence shape and required-case completeness.
- `specs/tests/packaging.md`: public-output scan and candidate artifact identity binding.

## Open questions

None.

E-002 is the sole central Codex-version gate.
