# tests/unit/adapters/test_codex_jsonl.py — exact Codex JSONL parser and mapper

**Wave:** D | **ADRs:** ADR-002, ADR-004, ADR-005 | **Imports (spec-tree):**
`src/yoetz/adapters/importers/codex_jsonl.md`, `src/yoetz/ports/importer.md` |
**Imported by:** unit suite

## Purpose

Freeze exact-version admission, bounded physical-line parsing, conservative source-linked mapping,
deterministic ID materialization, argv sanitization, and redacted diagnostic representations.

## Public surface

Tests call the public profile, parse, plan, materialize, and argv-sanitization functions and inspect
only their frozen adapter/port values.

## Behavior

Cover LF, CRLF, final-no-LF ranges, the exact 0.139.0 profile, command action/result mapping,
unknown/unsupported/malformed preservation, stable causal references, conservative import coverage,
source caps, and allowlisted argv metadata.

## Errors and edge cases

Unsupported versions, source cap breaches, duplicate-key/truncated input, unknown tags, extra
semantic fields, and source-bearing argv values fail closed or become explicit bounded outcomes.

## Invariants

1. Source bytes and argv canaries never appear in structural representations.
2. Every physical source line receives exactly one range and outcome.
3. Only exact supported shapes enter known event families.
4. Materialization accepts exactly the caller-allocated ID set.
5. Parsing, mapping, and sanitization perform no IO or provider work.

## Tests

This file is the executable unit owner. Installed exact-Codex corpus evidence remains in the
separate capability suite.

## Open questions

None.
