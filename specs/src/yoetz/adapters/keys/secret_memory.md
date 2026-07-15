# src/yoetz/adapters/keys/secret_memory.py — in-process SecretMemoryPort implementation

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** `ports/secret_memory.md` |
**Imported by:** `service/daemon.md`

## Purpose

Implements bounded mutable secret allocations with measured OS hardening while honestly preserving
Python's copy/zeroization limits and a future native-vault substitution seam.

## Public surface

- `class LocalSecretMemory(SecretMemoryPort)` and private one-shot handle implementation.
- Startup probes for page lock/core-dump suppression and closed capability report.

## Behavior

Allocate fixed-size mutable anonymous memory, attempt/test `mlock` on macOS/Linux, set process no-
core-dump protections, restrict consumer/purpose, overwrite full buffers in `finally`, unlock/free,
and reject forked/stale/closed use. Source bytearrays are overwritten immediately after capture.
No raw bytes/str return or content-based repr/equality/hash/copy/pickle.

## Errors and edge cases

Resource-limit/page-lock failure is measured unavailable and can fail profiles requiring it; no
false active claim. Crypto/HTTP/keyring libraries may copy input beyond this adapter's control.

## Invariants

1. Bounds, purpose, consumer, generation, and one-shot state precede every access.
2. Best-effort overwrite always runs; perfect zeroization is never claimed.
3. Child/fork/stale handles fail closed.

## Tests

- `tests/unit/service/test_secret_memory.py` covers semantics/faults.
- `tests/capability/test_service_keyring_unlock.py` records real OS hardening capability.

## Open questions

None.
