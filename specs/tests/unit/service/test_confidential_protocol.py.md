# tests/unit/service/test_confidential_protocol.py — frozen YZH1/YZS1 wire vectors

**Wave:** C/D | **ADRs:** ADR-004, ADR-008, ADR-009 | **Imports (spec-tree):**
`src/yoetz_core/service/confidential_protocol.md`, reviewed golden fixtures | **Imported by:** unit
suite and confidential protocol release gate

## Purpose

Freeze every confidential frame byte, tagged union, cap, correlation rule, secret purpose, and
shared byte validator without sockets, TTY, vault, keyring, randomness, or wall clock.

## Public surface

Parameterized golden tests for all eight opens/previews, four action branches, two phases, five
result branches, all bounded errors/close outcomes, and all six YZS1 purpose codes; malformed and
passphrase/provider-credential boundary matrices.

## Behavior

Load reviewed literal bytes and assert encode/decode equality, 64-lowercase-hex correlation, exact
step progression, closed targets/previews/actions/results, terminal close, and YZS1 header/binding
bytes. Exercise passphrase 15/16/1,024/1,025, strict UTF-8, NUL/CR/LF, composed/decomposed
distinction/no normalization; generic credentials 0/1/8,192/8,193 and NUL/CR/LF.

## Errors and edge cases

Wrong magic/version/direction/type, duplicate/unknown fields, float/BOM/NUL JSON, over-cap declared
length before allocation, crossed ceremony/step/purpose, result without close, zero/partial/extra
YZS1, and arbitrary metadata all fail with the bounded protocol reason and no input echo.

## Invariants

1. Golden expected bytes are fixtures, never recomputed by the implementation under test.
2. YZH1 cannot carry secret bytes and YZS1 cannot carry an action/result.
3. Validator failure never logs or returns the tested bytes.

## Tests

This file is the executable owner and runs offline with the unit suite.

## Open questions

None.
