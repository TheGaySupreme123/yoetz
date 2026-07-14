# tests/subprocess/test_service_secret_boundary.py — cross-process secret canary suite

**Wave:** C | **ADRs:** ADR-004, ADR-008 | **Imports (spec-tree):** daemon/ingress/vault/observability specs | **Imported by:** test runner

## Purpose

Prove keys, credentials, passphrases and decrypted canaries never cross forbidden process/surface boundaries.

## Public surface

Canary harness scanning argv/env/process title/control/MCP frames/stdout/stderr/logs/traces/temp/
dumps/support files and child descriptors, plus monkeypatched exception-format and SDK default-
header retention hooks.

## Behavior

Run every secret purpose success/failure/cancel/crash/relock path; inspect clients and service
artifacts without printing canaries. Inject YZH1 previews and YZS1 frames/secrets through hostile
logger messages/args/`exc_info` in service and confidential-helper modes and prove only bounded
structural stderr identity survives. Exercise a provider attempt and retry with distinct scoped
credential handles; neither SDK client/default headers retains the real credential.

## Errors and edge cases

Injected exception/log/trace/crash/helper faults, provider SDK chatter, body/profile/deadline
mismatch, credential callback reuse/retention, and descriptor inheritance. Raw traceback capture or
an owner-only exception file is a failure, not an allowed diagnostic surface.

## Invariants

1. Only confidential frame and protected service memory contain canary transiently.
2. No perfect-zeroization claim is inferred from absence scans.
3. YZH1/YZS1 content is never a logger argument; v0.1 creates no raw traceback artifact.
4. Each provider dispatch uses one body/profile/deadline-bound credential callback and no
   long-lived SDK credential.

## Tests

This file is the executable owner.

## Open questions

None.
