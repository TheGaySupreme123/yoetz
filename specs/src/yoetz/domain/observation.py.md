# src/yoetz/domain/observation.py — live observation values, cursors, status, and advice

**Wave:** D | **ADRs:** ADR-005, ADR-009, ADR-010 | **Imports (spec-tree):** `protocol/coverage.md`,
`protocol/canonical.md`, `domain/values.md`, `domain/findings.md` | **Imported by:**
`ports/observation.py.md`, observation adapters, status projection, capability fixtures

## Purpose

Own the shared closed domain values for first-party Codex live observation: source identity,
envelopes, cursors, status, and advice snapshots. These types are registry names from
`INTERFACES.md`. They describe retained structural observation state and references to encrypted
evidence; they never embed hidden reasoning, complete transcript prose, or secret-like command
output.

## Public surface

- `enum ObservationSource` — exactly `codex_hook | codex_session_stream`.
- `ObservationEnvelope` — Codex session identity (commitment, never raw path), event kind (exact
  closed identifier from the capability cell, or an opaque unsupported token), stable source
  identity, `ObservationCursor`, receipt time, bounded allowlisted structural payload, content-
  object references (encrypted object IDs/commitments only), and gap codes.
- `ObservationCursor` — source generation, byte/event position, last source commitment, mapping
  version. Crash-stable and generation-fenced.
- `ObservationStatus` — lifecycle `active|degraded|stale|stopped`, source coverage, last
  observation, lag, gaps, unsupported events, and current `AdviceSnapshot` frontier identity.
- `AdviceSnapshot` — ranked findings, exact evidence basis, confidence/coverage, recommended next
  action, freshness, and suppression identity.
- Supporting closed values as needed by the port: ingest results, status queries, control/revoke
  commands, and gap/reason enums — all exact, bounded, and path-free.

## Behavior

Constructors validate left-to-right and reject raw paths, unbounded prose, secret-like command
output in structural fields, and unknown enum members. Cursors compare by generation then position;
a lower generation is stale. Gap codes are closed where known; unrecognized event semantics use an
opaque unsupported gap and never project success.

`AdviceSnapshot` is derived from recorded observation evidence plus ordinary finding/coverage
machinery. It is safe for nonblocking hook delivery and ordinary `status` projection under the
existing disclosure rules. It does not create a seventh MCP tool or a parallel finding ID space.

Coverage interaction: only real observation evidence under an active consented observation arm may
contribute `hook_observed` / justified `harness_observed`. Domain helpers must not raise those
classes from trigger-only or empty/degraded status alone.

## Errors and edge cases

- Invalid cursor, empty required identifiers, or oversize structural payload raise
  `ProtocolValueError` with a bounded reason.
- Unknown future event kinds remain representable as envelopes with opaque gaps; they are not
  silently dropped in a way that invents completeness.

## Invariants

1. No hidden reasoning or complete transcript prose in domain values.
2. Sensitive evidence is object-ref only.
3. Session identity is a commitment, never a raw path.
4. Advice and status stay free of secret-like command output.
5. Mapping vocabulary may align with import gaps without sharing import allocation state.

## Tests

- Unit codecs/round-trips for envelope, cursor, status, and advice.
- Property: unknown event opacity; coverage helpers never invent `hook_observed`.

## Open questions

None beyond E-013 cell evidence for concrete event identifiers.
