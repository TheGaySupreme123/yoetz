# tests/subprocess/test_service_lock_and_confidential_unlock.py — locked service and secret-channel process boundary

**Wave:** C/E | **ADRs:** ADR-004, ADR-008, ADR-009 | **Imports (spec-tree):**
local service/control/confidential-ingress specs, CLI/MCP, setup contract | **Imported by:** subprocess
suite and privacy/security release gate

## Purpose

Prove service startup/lock/unlock and privacy setup preserve the process trust boundary: ordinary
CLI/MCP/agent channels never carry unlock secrets, while successful one-time confidential unlock
makes normal operations available without further secret forwarding.

## Public surface

Process scenarios cover pristine keyring ready only with matching presence evidence, usable
keyring plus `human_authority_unavailable` setup status and no artifacts, existing-keyring
ready-local without presence, keyring locked/unavailable, confidential no-echo
local unlock, wrong/expired/cancelled unlock, relock, restart, MCP bridge before/after unlock, setup
widening decision, inherited headless descriptor rejection (v0.1 has no acceptance path),
and stdout/stderr/transcript canaries.

## Behavior

Start the service locked and assert safe status works while decrypted operations return
`vault_locked`. Ordinary CLI args/stdin, MCP frames, agent messages, env and config containing the
synthetic unlock canary are rejected and never forwarded. The dedicated bounded binary confidential
channel over authenticated local control consumes secret bytes once, produces no JSON echo, then
normal CLI/MCP work without secrets. Lock/restart invalidates prior process-memory authority.

Privacy widening uses an inert ordinary proposal followed by foreground exact-preview and measured
OS presence or established-passphrase reauthentication; the proof is consumed inside the service
and never returned. Confirm-every-request approval for an already-policy-authorized exact case uses
foreground digest-bound TTY consent with no strong reauth and cannot widen policy; MCP/agent cannot
complete it. A decision may survive only crash/resume before its one authorized attempt is consumed.
Every later physical retry requires a fresh proposal, preview, and decision.
AF_UNIX service control is allowed; AF_INET/AF_INET6, DNS, redirect, telemetry, crash, update,
capability and external-provider probes remain governed independently.

## Errors and edge cases

Fault-inject partial binary frames, inherited FD wrong type/owner, timeout, peer mismatch, duplicate
consume, service generation change, suspend/relock, signal, crash, malformed JSON-control attempts,
and hostile diagnostics. Canary sweep covers argv, proc/env where available, logs, traces, terminal,
shell history fixture, MCP output and agent context.

## Invariants

1. Unlock material exists only in keyring/protected service memory/confidential ingress.
2. Locked is a valid service state, never silent reset/fallback.
3. CLI/MCP resume normal operation after service unlock without seeing the secret.
4. Policy confirmation and vault unlock are separate authorities.
5. Local control transport is not external egress.
6. Setup-required status never starts a passphrase prompt; only the separate explicit command does.

## Tests

Run `uv run --locked pytest tests/subprocess/test_service_lock_and_confidential_unlock.py -q
--timeout=180`; platform capability cells own keyring/headless-specific claims.

## Open questions

None.
