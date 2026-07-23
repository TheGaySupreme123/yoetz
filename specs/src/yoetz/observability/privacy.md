# src/yoetz/observability/privacy.py — privacy fences and diagnostic redaction

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`protocol/canonical.md`, `protocol/ids.md`, `ports/keys.md`, `config/models.md`,
`domain/privacy.md`, `domain/values.md` | **Imported by:**
`observability/logging.md`, storage/object adapters, import/review, CLI support-bundle paths, tests

## Purpose

This file owns the small, deterministic privacy primitives used before data reaches a log,
diagnostic artifact, or other plaintext support surface. It does not make encrypted objects safe,
decide retention policy, or claim that arbitrary secrets can always be detected. Its job is to make
the allowlist boundary explicit and testable, and to ensure that known canaries and common credential
forms cannot silently cross it.

The governing rule is structural: user-controlled payloads do not belong in SQLite structural
columns, logs, filenames, process arguments, or support bundles. Redaction is defense in depth after
that separation, not permission to put payloads there first.

## Public surface

- `SESSION_HASH_DOMAIN = b"yoetz/session-log-id/v1\x00"` — fixed domain separator.
- `PRIVACY_REQUEST_BODY_DOMAIN = b"yoetz/privacy-egress-request/v1\x00"` — fixed domain separator.
- `session_id_hash(session_id: str, log_mac: MacKeyHandle) -> str` — returns a stable,
  installation-local, non-reversible correlation label rendered `hmac-sha256:<64 lowercase hex>`.
- `class Sensitivity(Enum)` — `public_structural`, `local_identifier`, `user_content`, `secret`,
  `key_material`.
- `class ScanFinding` — frozen slots record with exact fields `kind: str`, `start_offset: int`,
  `end_offset: int` (exclusive), and `severity: Sensitivity`; `kind` is exactly
  `canary|credential_pattern|private_key_marker`, and it never retains the matched bytes.
- `scan_for_sensitive_content(data: bytes, *, canaries: tuple[bytes, ...] = ()) -> tuple[ScanFinding, ...]`.
- `redact_sensitive_content(data: bytes) -> tuple[bytes, bool]` — replaces detected secret spans
  with fixed ASCII redaction bytes before encrypted observation persistence.
- `assert_plaintext_safe(data: bytes, surface: str, *, canaries=()) -> None` — raises the typed
  internal `PrivacyFenceError` when a forbidden match is found.
- `redact_diagnostic_value(name: str, value: object) -> JsonValue` — allowlist conversion for a
  single diagnostic field. `None` (a `JsonValue`) is the omission sentinel for an unknown or
  forbidden name; an invalid value under a known name becomes the fixed string `"unavailable"`.
- `redact_diagnostic_record(record: Mapping[str, object]) -> dict[str, JsonValue]` — drops unknown
  keys and sanitizes the allowlisted remainder.
- `class DiagnosticRedactionProfile(Enum)` — `minimal`, `support`, `release_probe`.
- `build_diagnostic_manifest(profile, structural_inputs) -> dict[str, JsonValue]` — constructs the
  only plaintext manifest permitted in a diagnostic bundle.
- `PrivacyFenceError(reason_code: str, surface: str)` — internal exception with bounded fields and
  no echo of offending input.
- `privacy_request_commitment(final_request_body: bytes, audit_mac: MacKeyHandle) -> str` — keyed,
  installation-local body commitment for `EgressReceipt`; it never accepts raw/public/static key
  bytes or credential/authentication metadata.

These names are registered internal shared surfaces in `specs/INTERFACES.md`; no transport exposes
them directly.

## Behavior

### Session correlation hash

1. Validate `session_id` with `protocol.ids.validate_id(IdKind.SESSION, ...)`.
2. Require an opaque, current-generation `MacKeyHandle` minted by the ready vault with purpose
   `log_correlation`. No key bytes or locator are accepted.
3. Call `log_mac.mac(SESSION_HASH_DOMAIN, session_id ASCII bytes)`. The handle owns HMAC-SHA-256
   execution and rejects every domain other than its frozen log-correlation domain.
4. Return the standard commitment rendering. The raw session ID is never logged alongside it.

The hash is stable only within one installation because the handle uses `K_log` derived from that
installation's stable IVK. Two installations must not produce a linkable identifier for the same
imported session. v0.1 has no independent log-key rotation or raw key-slot surface.

### Egress request commitment

`privacy_request_commitment` requires an opaque, current-generation `MacKeyHandle` minted by the
ready vault with purpose `privacy_audit`. It calls
`audit_mac.mac(PRIVACY_REQUEST_BODY_DOMAIN, final_request_body)` and returns only the standard
commitment rendering.

`final_request_body` means the exact bytes produced by deterministic provider/application request
rendering after classification, policy, minimization, redaction, secret scan, final schema/cap
validation, and approval binding. It excludes HTTP/TLS framing, transport-generated metadata,
headers, cookies, and credential-bearing authentication fields. An absent body is the exact empty
byte string. The separately bound `ProviderCredentialHandle` may inject authentication only at the
transport boundary and may not change/add user-content body fields. This commitment therefore
proves equality of the final request body, not all on-wire bytes. Neither body bytes, key material,
nor authentication metadata enter structural audit, logs, CLI/MCP, config, environment, or
provider provenance.

### Sensitive-content scanner

The scanner accepts bytes so callers inspect the exact representation they are about to persist.
It performs bounded linear passes and reports locations, never matched text. It detects:

- exact caller-provided canaries, including UTF-8 and binary canaries;
- PEM private-key headers and common OpenSSH private-key framing;
- high-confidence token prefixes maintained as versioned data (for example provider/API and Git
  access-token prefixes), followed by conservative ASCII length checks;
- URI user-info containing a non-empty password;
- environment/assignment forms whose key matches a bounded secret-name vocabulary and whose value
  is non-empty;
- raw BMK/DEK/key-slot material supplied explicitly by the caller as canaries in tests.

It must not attempt entropy-only secret guessing in production: random IDs, hashes, ciphertext,
and source IDs would create noise and encourage unsafe bypasses. Pattern additions require positive
and false-positive fixtures. Inputs larger than the configured diagnostic scan cap are streamed in
overlapping chunks, with overlap at least the maximum pattern length; the scanner never truncates
and then reports success.

One call accepts at most 64 explicit canaries of 1..4096 bytes each. Inputs larger than 65,536
bytes are scanned in 65,536-byte chunks with 4,096 bytes of overlap (the maximum accepted canary
and detector-pattern length). A call returns at most 128 findings, sorted by
`(start_offset, end_offset, kind)`. Exceeding the canary-input limits fails
closed with `scanner_input_invalid`; reaching the finding cap still means the surface is unsafe and
never turns into a successful scan. Exact overlap detects a marker across any producer or scanner
chunk boundary; no prefix or suffix is discarded before a successful verdict.

`assert_plaintext_safe` maps findings to bounded reasons such as `plaintext_canary_detected`,
`credential_pattern_detected`, or `private_key_marker_detected`. It does not expose offset, source
bytes, path, URL, command, or field value in its exception string.

### Diagnostic allowlist

`redact_diagnostic_value` accepts values only for the exact structural fields documented by
`observability/logging.md`: timestamps, bounded enums, correlation/request IDs, hashed session IDs,
durations, version identities, SQLite source-ID hash, counts, booleans, and bounded reason codes.

Rules:

1. Unknown field name: omit it and increment an in-memory omitted-field counter.
2. Known ID field: validate the typed ID; never stringify an arbitrary object.
3. Enum/reason field: require membership in its frozen allowlist.
4. Numeric field: accept only bounded integers; floats are forbidden.
5. Exception/traceback/message/path/URL/command/prompt/payload fields: always omit, regardless of
   apparent content.
6. Nested maps/lists: reject unless the field's schema explicitly defines a bounded structural
   shape. No recursive generic sanitizer exists.
7. Conversion failure: replace the field with the fixed token `"unavailable"`; never call an
   untrusted `__str__` or `repr`.

### Diagnostic bundle profiles

All bundles are owner-only and opt-in. The manifest is canonical JSON and contains only:

- package/protocol/engine/policy/projection/schema versions;
- supported-platform and runtime identities;
- hashed SQLite source identity and compile-option verdict, not database paths;
- bounded startup-check outcomes and reason codes;
- operation counts, duration buckets, and terminal outcome counts;
- hashed session correlation only in `support`, never in `minimal`;
- release capability probe identifiers in `release_probe`.

No profile contains environment dumps, argv, config contents, filesystem paths, repository names,
tool input/output, event payloads, object headers, provider prompts/responses, approval previews,
privacy authorization material, tracebacks, SQL text,
database pages, WAL/SHM files, or key-backend labels that reveal user/account names.

v0.1 never creates a raw traceback artifact, even under an owner-only permission or diagnostic
profile. A future encrypted diagnostic artifact requires a separate reviewed content schema,
privacy authorization, minimization/never-send enforcement, retention, encrypted-object path, and
release evidence; it cannot reuse this structural manifest or appear as a logging flag.

Before finalization, the exact manifest bytes and every plaintext member pass
`assert_plaintext_safe`. Encrypted user objects are never copied into a generic support bundle.
The manifest records its redaction profile and schema version so support cannot infer omitted data.

The closed v0.1 manifest-field registry is exactly `schema_version`, `redaction_profile`,
`package_version`, `protocol_version`, `control_protocol_version`, `engine_version`,
`policy_version`, `projection_version`, `privacy_policy_schema_version`,
`egress_receipt_schema_version`, `platform_identity`, `runtime_identity`, `sqlite_version`,
`sqlite_source_id_hash`, `sqlite_compile_options_ok`, `startup_check_outcome`,
`startup_reason_code`, `operation_count`, `duration_bucket_ms`, `terminal_outcome_count`,
`session_id_hash`, and `capability_probe_id`. The builder always supplies
`schema_version="yoetz-diagnostic-manifest/1"` and the selected `redaction_profile`. `minimal`
omits both session and capability-probe identity; `support` may include only the hashed session
identity; `release_probe` may include only the bounded capability-probe identity. Unknown inputs
are dropped before canonical encoding and the final canonical bytes pass the plaintext fence.

### Pre-persistence hooks

Callers use the scanner at plaintext surfaces that are supposed to be structural: structured log
encoding, catalog/bundle structural-row construction in test/release-probe modes, migration and
backup manifests, import metadata, diagnostic manifests, and generated error summaries. Event
payloads and captured artifacts are expected to contain user data and go directly to the encrypted
object path; scanning them does not make them suitable for plaintext storage.

Observation capture applies a stricter ingress rule: hidden reasoning and system/platform/developer
fields are never selected; visible task content is scanned and secret spans are redacted in memory
before object staging. Raw matched bytes may not reach SQLite, local outbox/quarantine, logs,
diagnostics, crash text, hook context, semantic packets, or backup manifests. If redaction or
authenticated encryption cannot complete, only structural metadata plus
`content_capture_unavailable|content_redacted` survives. This protects copied at-rest files without
vault keys; it does not protect a compromised unlocked same-user process or root/kernel control.

In normal production, structural fences (typed models and allowlists) are mandatory. Expensive
full-file canary scans are mandatory in conformance/release-probe profiles and before diagnostic
bundle export, and may be sampled elsewhere only if the release privacy gate still performs the
complete sweep.

## Errors and edge cases

- Missing log-correlation key disables session correlation fields; it never falls back to raw IDs
  or a constant/public hash key.
- Missing/stale/wrong-purpose privacy-audit handle fails the dispatch audit closed before transport
  I/O; it never falls back to a raw key, unkeyed digest, bundle key, or log/lookup handle.
- Invalid UTF-8 is scanned as bytes and cannot bypass binary patterns.
- Matches spanning stream chunks are found through deterministic overlap.
- A scanner internal failure is fail-closed for an about-to-be-written plaintext support surface.
  For ordinary application work, the related diagnostic record is dropped; the user operation is
  not failed unless the unsafe surface is required for its durability contract.
- False positives are handled by narrowing/versioning the detector and fixtures, never by a
  call-site `ignore=True` bypass.
- Redacted output must itself be rescanned; replacement strings are fixed ASCII constants.
- An arbitrary or sensitive `surface` label on `PrivacyFenceError` is replaced by the fixed
  `unsafe_surface` token; paths and filenames never become exception text.
- This module does not promise zeroization of immutable Python bytes. Key-owning adapters minimize
  lifetime and the threat model states that live-process memory is out of scope.

## Invariants

1. Raw user content and raw session IDs never leave this module in diagnostic output.
2. No function logs, prints, or includes matched content in an error.
3. Unknown fields are omitted, never generically stringified.
4. The scanner is not used to justify plaintext payload persistence.
5. The same input, detector version, and canary set produce the same findings and redacted bytes.
6. Privacy failure weakens or removes diagnostics; it never weakens ledger truth or encryption.
7. Egress receipts commit to the exact final provider/application request body without storing it
   or enabling a public equality oracle; they do not claim to commit HTTP/TLS framing or
   credential-bearing authentication metadata.

## Tests

- `specs/tests/unit.md`: session-hash and request-body commitment purpose/domain separation,
  raw-key rejection, all detector positives/negatives,
  chunk-boundary matches, invalid bytes, object-with-hostile-`__str__`, and allowlist behavior.
- `specs/tests/property.md`: arbitrary bytes never cause unbounded output or exceptions containing
  source substrings; chunked and one-shot scans agree.
- `specs/tests/integration.md`: owner-only diagnostic manifest construction and fail-closed export.
- `specs/tests/conformance.md`: seed unique canaries into payload, prompt, URL, path, filename,
  command output, provider response, and secret fields; sweep DB/WAL/SHM/temp/log/export/crash/debug/
  support surfaces and process arguments. Any application-controlled plaintext occurrence fails.
- `specs/tests/packaging.md`: packaged detector data equals the reviewed source bytes.

## Open questions

None.

The initial credential-prefix detector/exception set freezes with E-008 public-boundary
fixtures; later additions are reviewed security updates, never network-fed runtime drift. v0.1 has
no public support-bundle command; the format is release-probe/internal evidence only.
