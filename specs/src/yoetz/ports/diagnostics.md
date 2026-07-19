# src/yoetz/ports/diagnostics.py — safe startup evidence sink and write-gate result vocabulary

**Wave:** B (definition) / C–F (producers) | **ADRs:** ADR-001, ADR-003, ADR-004, ADR-005,
ADR-006, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):** `protocol/errors.md` | **Imported by:**
`application/service.md`, `application/maintenance.py.md`, `config/paths.md`, `adapters/sqlite/connection.md`,
`adapters/sqlite/recovery.md`, `adapters/keys/*`, `version.md`

## Purpose

Yoetz must prove its runtime, path, SQLite, ownership, schema, key, object, projection, and optional
provider posture before accepting writes. `DiagnosticsPort` records those bounded structural
results without turning them into a public v0.1 `doctor` command. The vocabulary lets
the service daemon's ready-composition factory make one mandatory capability gate while keeping
secrets, raw paths, payloads,
and exception strings out of startup evidence.

Maintenance support diagnostics use a separate sink and value so operational observations can
never be passed to the startup capability gate.

## Public surface

- `class DiagnosticsPort(Protocol)` with
  `def record(self, result: StartupCheckResult) -> None` (INTERFACES §10).
- `@dataclass(frozen=True, slots=True) class StartupCheckResult`.
- `enum StartupCheckArea` — `runtime`, `package`, `resources`, `path`, `service_control`,
  `service_lifecycle`, `sqlite_build`, `sqlite_schema`, `ownership`, `ledger`, `objects`, `keys`,
  `vault`, `secret_memory`, `projection`, `privacy_policy`, `egress_gateway`, `provider`.
- `enum StartupCheckOutcome` — `ok`, `degraded`, `blocked`.
- `enum RuntimeCapability` — `structural_read`, `payload_read`, `write`, `semantic`.
- `@dataclass(frozen=True, slots=True) class StartupGateReport`.
- `evaluate_startup_gate(results) -> StartupGateReport`.
- `@dataclass(frozen=True, slots=True) class MaintenanceDiagnostic` — one bounded path-free
  `backup|restore|migration` preview/execute observation.
- `class MaintenanceDiagnosticSink(Protocol)` with
  `record_maintenance(diagnostic: MaintenanceDiagnostic) -> None`.

These shared types are registered in `specs/INTERFACES.md`. They are internal structural types,
not a promise to expose a `doctor` command in v0.1.

`MaintenanceDiagnostic` contains only operation, phase, `success|failed|cancelled`, request ID,
optional task ID, optional plan/result digest, optional migration versions, optional count,
nonnegative bounded duration milliseconds, optional bounded reason code, and observed time.
Location, object ID, manifest body, secret/key locator, SQL, and exception text cannot be
represented. `MaintenanceDiagnosticSink` is orthogonal to `DiagnosticsPort`: its values are never
inputs to `evaluate_startup_gate` and cannot prove or remove a runtime capability.

## Behavior

### `StartupCheckResult`

Fields:

- `check_id: str` — closed stable token such as `sqlite.source_id` or `keys.backend`; never a
  message.
- `area: StartupCheckArea`.
- `outcome: StartupCheckOutcome`.
- `reason_code: str | None` — required for `degraded`/`blocked`, forbidden for `ok`; drawn from a
  reviewed per-check allowlist.
- `capabilities: frozenset[RuntimeCapability]` — capabilities this result proves available when
  `ok`, or removes when degraded/blocked.
- `safe_details: Mapping[str, JsonScalar]` — bounded allowlisted keys and only booleans,
  nonnegative bounded integers, safe enum/version strings, or hashes; canonical key order.
- `observed_at: datetime` — diagnostic metadata, not a truth/order input.

Construction rejects unknown check IDs/details, free-form reason text, raw paths, URLs, provider
payloads, key locators, SQL, environment values, or values above the per-field size cap.

### `record`

1. Accept an already validated immutable result and append it to the runtime's in-memory/startup
   evidence sink or safe structured diagnostic log.
2. Never raise into the startup decision path. A sink failure is recorded by the factory through
   its no-throw fallback counter and cannot turn an unsafe check into `ok`.
3. Never mutate, merge, or reinterpret a producer's result. Gate policy lives in
   `evaluate_startup_gate`.
4. Results are session/runtime structural evidence only. They are not appended to the task ledger
   unless a future versioned event explicitly defines that behavior.

### Mandatory startup gate

the ready service startup gathers the closed required check set in this order: minimal config;
private path;
package/Python/resources; SQLite source/options and schema; recovery/ownership; ledger/object
identity; key backend; projection frontier; optional provider profile. It records every result and
then calls `evaluate_startup_gate` exactly once before exposing `Application`.

The gate returns `StartupGateReport(results_digest, capabilities, blocked_reasons,
degraded_reasons)`, with sorted unique reason codes. Policy:

- Unknown/unsupported Python package route, unsafe path, uncertified SQLite for writes, failed
  generation ownership, newer/unknown write schema, canonical corruption, or unsafe object format
  removes `write`; the mapped public startup failure is respectively `STORAGE_UNSAFE`,
  `BUNDLE_BUSY`, `MIGRATION_REQUIRED`, or `STORAGE_CORRUPT`.
- Unknown SQLite builds may retain `structural_read` only through the explicitly tolerant read-only
  path; they never retain `write` merely because the version is newer.
- Locked/missing keys remove `payload_read` and every operation that creates/reads encrypted
  content. Structural inspection may remain available, but no empty payload is fabricated.
- Projection corruption removes no canonical-ledger capability when rebuild succeeds; an
  incomplete/failed rebuild is reported as lag/gap and cannot support current coverage.
- External-provider absence under `local_only` is expected and leaves deterministic `write`
  capability; a separately configured exact AF_UNIX local model is evaluated independently. An
  invalid/unauthorized provider profile removes `semantic`. A later `semantic_required` check still
  returns its deterministic result as `incomplete_check` with the exact semantic/egress reason; it
  does not throw a provider error or claim semantic success.
- Missing any required check is itself `blocked` with `startup_check_missing`; absence never means
  success.

The exact safe public/control error is produced by the service ready-composition factory from the
highest-priority blocked
reason. The complete structural report remains internal/release evidence in v0.1.

## Errors and edge cases

- Duplicate `check_id` results must be byte-identical; disagreement blocks startup with
  `startup_check_conflict`.
- A diagnostics sink exception is swallowed only after a constant safe fallback record/counter is
  attempted; exception text is never logged or returned.
- A check that times out is `blocked` or `degraded` according to its declared required capability;
  timeout never becomes `ok`.
- Safe details may contain an approved path-risk category or hash, never the resolved filesystem
  path itself.
- Public `doctor` and support-bundle serialization are deferred to v0.2; adding them requires a
  separate schema and canary suite.

## Invariants

1. No write-capable `Application` is exposed before the complete mandatory gate passes.
2. Every capability is positively proven; unknown and missing checks fail closed.
3. Diagnostics contain structural allowlisted data only and are safe under plaintext-canary tests.
4. Provider degradation never disguises itself as semantic success or disables strict-local
  deterministic usefulness.
5. Recording evidence and deciding capability are separate operations.
6. Maintenance observations cannot enter or alter the startup capability gate.

## Tests

- `specs/tests/unit.md`: result validation, allowlists, missing/duplicate/conflicting checks,
  deterministic result digest and reason ordering.
- `specs/tests/integration.md`: each startup check forced to fail; exact capability/error mapping;
  tolerant read-only unknown-build path; projection rebuild recovery; key locked/missing.
- `specs/tests/subprocess.md`: no traceback/path/secret on startup failure; MCP accepts no frame
  before gate success; strict-local starts with semantic unavailable.
- `specs/tests/packaging.md`: installed artifact records exact package/Python/SQLite/resource
  identities used by the release manifest.

## Open questions

None.
