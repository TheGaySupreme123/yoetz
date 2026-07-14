# tests/capability/test_codex_jsonl_import.py — observed Codex JSONL compatibility corpus

**Wave:** D/F | **ADRs:** ADR-002, ADR-005, ADR-006 | **Imports (spec-tree):** capability evidence,
Codex importer/fixture specs | **Imported by:** importer capability matrix

## Purpose

Capture and test exact public `codex exec --json` line families for each supported Codex version,
then prove conservative import, assurance, encrypted source retention, and gap reporting.

## Public surface

Fixture scenarios cover lifecycle, command, file change, MCP call/result, model message, plan, web
search, malformed/truncated line, unknown/new type, reordered/extra fields, and omitted material
event. Cooperative-vs-import review is the end-to-end oracle.

## Behavior

Generate synthetic nonprivate activity with real exact-version Codex and capture raw JSONL bytes.
Record executable/version/argv-profile/platform and source digest; store raw corpus only in encrypted
private evidence, while reviewed synthetic fixtures contain no path/secret. Feed exact bytes to the
installed importer with fixed chunk boundaries, including split UTF-8/line endings.

Assert only justified public categories map to Yoetz events, observed actor assurance never upgrades,
raw source object/digest is retained, unknown/malformed/truncated input is quarantined/gapped, and
import report counts/offsets/digests are deterministic. Compare a cooperative run with imported run;
the intentionally omitted event must weaken coverage/be visible.

## Errors and edge cases

- Format change is unsupported/incomplete evidence, never guessed mapping.
- Source paths/commands/messages cannot enter structural DB/log/public report.
- Import never executes commands, opens referenced files, or contacts network.
- Fixture refresh requires explicit version-bound review.

## Invariants

1. Exact source bytes/digest are retained encrypted.
2. Mapping is conservative and version-scoped.
3. Unknown/missing material weakens coverage.
4. Harness-observed identity is not verified authorship.

## Tests

Run all fixture types under every tested Codex version and multiple input chunkings. Evidence names
fixture/source digest and mapping/gap counts only.

## Open questions

None.
