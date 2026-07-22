# tests/capability/evidence.py — canonical, redacted capability evidence builder

**Wave:** D–F | **ADRs:** ADR-003, ADR-004, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/tests/capability.md`, protocol canonical/privacy/version specs | **Imported by:** every
capability test and capability-matrix generator

## Purpose

Provide one strict evidence shape and safe writer for empirical external capability observations.
It binds each outcome to exact installed bytes, platform, external version, fixture/test revision,
and private-source digest without leaking transcripts, prompts, payloads, secrets, or local paths.

## Public surface

- `CapabilityCase`: stable case/requirement/claim IDs and required observations.
- `CapabilityContext`: candidate/resource/fixture/test and platform/external identities.
- `EvidenceOutcome`: `pass|fail|unsupported|inconclusive`.
- `Observation`: bounded observation code plus canonical structural fields.
- `CapabilityEvidence`: immutable complete record with self-digest.
- `EvidenceRecorder.begin(case, context)`, `.observe(...)`, `.finish(outcome, reasons)`.
- `validate_evidence(record)`, `canonical_evidence_bytes(record)`,
  `write_evidence_atomic(record, output_root)`.
- `capability_evidence_output_root(tmp_path)` — durable root when `YOETZ_CAPABILITY_EVIDENCE_DIR`
  is set; otherwise an isolated temp directory.
- `runtime_capability_context(...)` — binds digests from the version manifest, or from
  `YOETZ_CANDIDATE_ARTIFACT_DIGEST` when the capability workflow supplies the prepared candidate.

## Behavior

Context is captured from verified manifests/probes, never arbitrary `--version` text alone. Require
artifact/resource/fixture/test digests; OS/CPU/ABI/Python/APSW/SQLite; exact external executable,
protocol/SDK/provider/key-backend identity; sanitized integration channel/config profile; monotonic
duration and canonical UTC bounds.

Observations use a closed per-case vocabulary and bounded booleans/integers/digests/enums. Reject
freeform messages, argv strings with values, environment, absolute paths, repository/user names,
prompts/source/tool output/provider response, credential/key, SQL, traceback, or raw transcript.
Private evidence is encrypted outside this module; record only opaque locator ID and SHA-256.

On finish, validate required observations, outcome/reason compatibility, limits, candidate equality,
and scan canonical bytes. Compute evidence digest over the record without its digest field, render
canonical JSON, and atomically create an ASCII case-ID/digest filename without overwrite. Existing
identical bytes are idempotent; different bytes at same identity fail.

## Errors and edge cases

- Recorder exception/unfinished case becomes no evidence, never implicit inconclusive/pass.
- Clock skew or negative duration invalidates; monotonic duration remains primary.
- Sanitization failure drops public evidence and fails the test, rather than redacting freeform
  content heuristically.
- Evidence files are capped, non-symlink, owner-only while staging, and public-boundary scanned.

## Invariants

1. A pass has every required structural observation.
2. Evidence names exact candidate/platform/external identities.
3. No user/private/secret/raw transcript data enters public evidence.
4. Canonical bytes and self-digest are deterministic.
5. Records are immutable and conflict-detecting.

## Tests

Unit/property cases cover every missing/invalid field, hostile freeform/object values, canaries,
timestamp skew, order/hash seed, duplicate identity, atomic interruption, and self-digest mutation.

## Open questions

None.
