# ADR-020 — Typed evidence digest provenance

**Status:** Accepted (2026-08-09), acknowledged in
[issue #131](https://github.com/TheGaySupreme123/yoetz/issues/131).
**Implemented by:** `src/yoetz/domain/events.py`, `src/yoetz/application/publish_work.py`,
`src/yoetz/application/observation_coordinator.py`, `src/yoetz/kernel/deterministic_checks.py`,
`src/yoetz/application/semantic_case.py`, and the public event schemas and guidance.
**Relates to:** ADR-002 (canonical protocol), ADR-009 (data egress and privacy), ADR-010
(harness integration port), and ADR-011 (structural subject-state capture).

## Context

An evidence digest previously identified bytes without recording what those bytes represented. A
caller could hash a source diff, publish it as `test_result`, describe successful tests in prose,
and satisfy the structural evidence link. The digest was real, but its asserted meaning was not
checkable. A digest also does not establish that a command ran, succeeded, was inspected, or was
observed by Yoetz.

Historical `evidence_recorded/1.0.0` records must remain readable without inventing provenance.
Approved checks and trusted imports additionally need stronger, capability-owned provenance that
ordinary cooperative publication cannot claim.

## Decisions

1. **New digest evidence uses `evidence_recorded/1.1.0`.** Every 1.1 payload with
   `content_digest` carries a closed `digest_binding`: exact `subject`, `content_availability`,
   `byte_count`, and `provenance`. A binding without a digest is invalid.

2. **Subjects and compatibility are closed.** Subjects are `approved_check_receipt`,
   `artifact_bytes`, `bounded_excerpt`, `command_stdout`, `import_report`, `source_diff`,
   `static_analysis_report`, `test_report`, and `test_stdout`. Each `evidence_kind` admits a fixed
   subset. In particular, `test_result` does not admit `source_diff`; the same digest is valid as
   artifact/source-diff evidence.

3. **Availability is explicit.** `captured` requires the mirrored captured object. `digest_only`
   and `withheld` forbid it. The digest always identifies the named bytes; availability states
   whether those bytes were retained, not whether their meaning was verified.

4. **Provenance cannot be self-awarded.** Ordinary publication may use only `caller_asserted`.
   `approved_check` requires a service-authenticated Yoetz-engine author on the engine-derived
   channel and binds both the approved-policy commitment and result digest. `import_observed`
   requires the trusted importer on the import channel. Envelope validation repeats these checks
   so bypassing the public application path cannot widen authority.

5. **Approved checks publish bounded ledger evidence.** The background verifier materializes an
   idempotent action/evidence/result graph. Its captured canonical receipt binds the approval,
   result, output digest and byte count, encrypted output object identity, before/after subject
   state, freshness, and recorded time. It contains no command output or broad log plaintext.

6. **Legacy and unavailable provenance weaken only linked work.** A referenced v1.0 digest adds
   `evidence_digest_subject_legacy_unknown`; referenced 1.1 `digest_only` or `withheld` evidence adds
   its exact limitation. Legacy-unknown evidence cannot silently satisfy a new completion claim.
   Case construction follows only the current claim/obligation/response support graph, so unrelated
   historical evidence does not weaken another conclusion. Redaction remains an independent
   availability limitation.

7. **Semantic review never substitutes prose for digested bytes.** When selected, a typed digest
   contributes only bounded canonical provenance facts; unavailable legacy provenance becomes an
   explicit omission. Descriptions remain caller-authored narrative.

8. **No storage migration or projection-version bump is required.** The new binding lives inside
   the existing encrypted event payload and current projection payload handle. Replay already
   preserves the exact schema version. `evidence_recorded/1.0.0` is frozen byte-for-byte and remains
   supported; new producers use 1.1. The version manifest advertises 1.1 as the current evidence
   schema while the complete dispatch table accepts both versions.

## Consequences

Yoetz can reject a typed kind/subject contradiction at publication without pretending it inspected
the bytes. Receipts and deterministic findings retain exact limitations for digest-only, withheld,
redacted, and legacy evidence. Capability-owned approved-check/import provenance stays unavailable
to cooperative callers.

The subject taxonomy is intentionally finite. Adding a new byte class requires a reviewed protocol
change rather than a caller-defined string. Historical evidence may still be useful as weak context,
but its missing subject can never be repaired by inference from its description.

## Alternatives considered

**Infer the subject from `description` or `evidence_kind`.** Rejected: both are assertions and would
retroactively fabricate provenance.

**Recompute every digest by executing caller commands.** Rejected: arbitrary execution is outside
the evidence and consent boundary.

**Replace v1.0 in place.** Rejected: it would alter frozen wire bytes and make old ledgers ambiguous.

**Treat every digest as captured or inspected.** Rejected: identity, retention, inspection, and
authorization are separate facts.
