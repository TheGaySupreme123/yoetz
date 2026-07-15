# tests/subprocess/test_module_entrypoint_parity.py — console-script and module-entry equivalence

**Wave:** D/F | **ADRs:** ADR-007 | **Imports (spec-tree):** package `__main__`, CLI specs,
`specs/tests/subprocess/helpers/child.py.md` | **Imported by:** packaging release gate

## Purpose

Prove `yoetz` and the installed interpreter's `python -m yoetz` are two launch paths into
one CLI contract, not divergent parsers/configuration/startup systems.

## Public surface

One paired parameter matrix covers help/version, each six operation, representative support
commands, invalid input, application error, storage unavailable, JSON/human mode, SIGINT, and EOF.

## Behavior

Create a fresh equivalent fixture for each side with injected IDs/clock, invoke from the same
unrelated cwd and minimal environment, and compare exit/stdout/stderr and resulting canonical ledger
snapshot byte-for-byte. Normalize only the literal launcher token in help usage. `version --json`
must report identical package/resource/runtime identity and neither path may import checkout code.

Run both orders to expose ambient state and with no writable cwd. Measure startup buckets; the test
does not require identical timing, only the same startup gates and configured bound.

## Errors and edge cases

- Global PATH Python, wrong virtual environment, source-tree import, cwd resource lookup, or launcher-
  specific config is a harness/product failure.
- Platform signal representation may differ internally but public exit/output must agree.
- Each paired mutation uses separate bundles to avoid comparing different frontiers.

## Invariants

1. Launcher choice cannot change validation, effects, errors, privacy, or version identity.
2. Both paths work without checkout/cwd write access.
3. Only the documented help launcher text may normalize.

## Tests

Run from the built wheel in clean-install and offline-reinstall environments on each advertised
platform, under multiple cwd names containing canaries.

## Open questions

None.
