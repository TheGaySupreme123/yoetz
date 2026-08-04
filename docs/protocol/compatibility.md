# Yoetz compatibility

"Compatible" is not one number. Yoetz tracks many independent version axes, and a claim on one axis
never implies a claim on another. This page names each axis, its authoritative discovery source,
and the exact rules that govern reading and writing across versions. Successfully importing one
transcript, or one successful `check`, is never grounds for a blanket compatibility claim.

## Independent axes

| Axis | Discovery source |
|---|---|
| Package / commit / release provenance | `version --json` |
| Public protocol version (`PROTOCOL_VERSION = "0.1"`) and per-operation request/result schema version (`1.0.0`) | `version --json`, `schemas/operations/` |
| Each durable event schema name and version (sixteen families, each `1.0.0`) | `schemas/events/`, `version --json` |
| Canonical encoding / digest domain version | `docs/adr/ADR-002-canonical-protocol.md`, `fixtures/canonical/` |
| Storage schema and its ordered migration set | `version --json` (catalog 2, bundle 4), `migrations/` |
| Object envelope / encryption / key / recovery artifact formats | `version --json` (object format `yoetz-object/1`) |
| Projection engine/generation and deterministic policy/config digest | `version --json` (projection `yoetz/0.1.0`) |
| Receipt schema and render version | `schemas/receipts/receipt-document-1.0.0.schema.json` |
| Installed resource set / skill / reference compatibility | `version --json --resources`, `skills/codex/yoetz/manifest.json` |
| CLI contract / MCP SDK / MCP protocol | `version --json` (MCP SDK `mcp==1.28.1`, protocol negotiated against the latest published revision) |
| Codex exact tested executable set and JSONL importer format profiles | `version --json`, `fixtures/imports/codex/` |
| Optional provider adapter / profile / model / SDK | `version --json`, provider capability fixtures |
| Python / OS / CPU / ABI / APSW / SQLite source and build options / filesystem / key backend | `version --json` (Python `>=3.14,<3.15`, APSW `3.53.3.1`, SQLite source ID pinned in ADR-003) |

`version --json`, the installed resource manifest, bundle metadata, and the release
capability/support matrices are the sole authorities. No prose table in this document is itself
normative if it drifts from those sources for an installed release.

## Version rule vocabulary

Every compatibility statement uses exactly one of four states:

- **`supported`** — complete required evidence exists for this exact cell.
- **`readable`** — safe to read or inspect, but no write claim is made.
- **`unsupported`** — known denied or failing for this exact cell.
- **`untested`** — no claim either way. `untested` is never collapsed into `unsupported`, and
  "available" or "installed" is never treated as a compatibility state by itself.

For package/protocol SemVer: a major version change may break readers or writers; a minor version
may add a backward-compatible optional capability, but only when an old reader can safely ignore or
preserve it; a patch version never changes canonical bytes or schema semantics. Actual release
behavior is governed by the exact schema/version manifests shipped with a release, not by SemVer
optimism about a range.

## Reader/writer matrix

- A client sends only operation schema versions the server currently advertises, and strictly
  parses the exact selected result schema — an unknown required field or variant is never silently
  ignored.
- The server may read a retained older event version only when a registered decoder/migration
  fixture exists for it. An unknown event schema is preserved as canonical accepted data and
  surfaced as an explicit coverage gap; it is never mapped to a guessed meaning.
- Writers never append under an unsupported protocol, storage, object, or canonical version.
- A newer, unknown storage schema allows at most tested structural read-only inspection and version
  guidance; every write and migration fails closed (`MIGRATION_REQUIRED` / `STORAGE_UNSAFE`).
- An old package reading a bundle changed by a newer package is supported only when the release
  support matrix says so for that exact pair — installing an old wheel is never rollback.

| Scenario | Outcome |
|---|---|
| Older client / current server | Server rejects an unsupported request schema version (`PROTOCOL_VERSION_UNSUPPORTED`); a supported older request version is served normally. |
| Current client / older server | Client must not send a schema version the server has not advertised as supported. |
| Current package / older bundle | Readable/writable only when the current package's registered migrations cover the bundle's schema version; otherwise migration is required first. |
| Older package / current bundle | Fails closed — an old package is not guaranteed to understand a newer bundle's schema, object format, or canonical rules. |
| Unknown event | Preserved verbatim as canonical data; reported as an explicit projection/coverage gap. |
| Unknown object format/version | Payload access and writes fail closed; no plaintext or empty fallback is substituted. |

## Event/schema evolution

An event family/version pair is immutable after release. Fixing semantics requires a new version;
canonical bytes and digests for the old version remain readable forever. A new optional field is
permitted on an existing version only with an explicit default that does not alter old canonical
bytes; otherwise it requires a new schema version. A new event family requires a coordinated
registry, schema, reducer, unknown-gap handling, fixture, skill, and documentation update — it never
ships as a silent addition.

The declared-completion-scope change is an explicit pre-release 0.1 correction under that optional
field rule. `plan_published` and `plan_revised` remain event schema `1.0.0`, and status remains
request/result schema `1.0.0`. Existing plan events omit `no_obligations_reason` and retain their
byte-identical canonical encoding. Omission means no typed empty-scope declaration; on a revision it
also clears any earlier declaration. A present value is one of the three closed reasons and is
admitted only when the effective current plan has zero obligation refs. Older readers that preserve
the exact event bytes remain safe; no storage migration or reinterpretation of old bytes is needed.
Because `start` reuses the compact projection on attach, its existing `open_obligation_count` also
admits `null` when current plan scope is unreadable; this prevents a redacted plan from being
reported as zero or making the task unattachable. The start result schema remains `1.0.0` under the
same pre-release correction.

Golden vectors are retained for every released version under `fixtures/`. Reducers, checks, and
receipts always name the engine/policy version that produced them and never reinterpret old bytes
under a newer policy silently. Unknown or redacted data always weakens coverage and receipt
wording; it is never treated as equivalent to current, fully observed data.

## Storage, object and recovery

Storage migrates forward only, through a contiguous, immutable, numbered migration set, under an
exclusive generation and a verified backup taken first. Migration never rewrites canonical event
bytes. There is no automatic downgrade or reverse SQL. "Rollback" means restoring a verified
pre-migration backup into a new quarantined target, replaying it, and then performing an atomic
catalog switch — see [`../runbooks/migration-rollback.md`](../runbooks/migration-rollback.md).

An unknown object, recovery, or key format, or a key-slot mismatch, fails payload access or write
outright; there is no plaintext or empty fallback. Machine-bound and portable-recovery backup
compatibility are explicit and separate claims — see
[`../runbooks/backup-restore.md`](../runbooks/backup-restore.md) and
[`../runbooks/key-recovery.md`](../runbooks/key-recovery.md). A portable backup's format version
being current is not by itself proof that its recovery secret or artifact is available.

Projection caches may be discarded and rebuilt whenever a registered projection version supports all
events currently in the ledger; projection compatibility never substitutes for ledger compatibility.
Policy and receipt wording changes are themselves versioned, and an old receipt's JSON remains
addressable by its own schema version.

## External integrations

MCP compatibility names the exact SDK and protocol-revision pair tested against an installed
artifact (`mcp==1.28.1`, protocol negotiated). Codex support is an explicit tested executable set
per platform (target/maximum-tested `0.144.5`) — a version newer than maximum-tested is
`untested`, never inferred `supported`. Skill compatibility combines the skill schema version, the
Yoetz protocol range it declares, and exact-tested Codex evidence; installing the skill files alone
is not itself a support claim. The Codex JSONL importer records exact format observations per
tested profile; unknown lines are quarantined as an explicit import gap, never silently dropped.

Provider compatibility (for example the OpenAI Responses adapter) names an exact
adapter/SDK/endpoint/model profile and is optional and advisory; the strict-local base support
Yoetz ships with is independent of any provider profile. Platform support is stated as an exact
artifact/runtime/SQLite/filesystem/key-backend cell (for example "macOS 11.0+ arm64, APSW
`3.53.3.1`, SQLite source ID `2026-06-26 ...`"), never a generic "Python" or "macOS/Linux" claim.

## Change and deprecation process

Every change class (protocol, event, storage, object, projection, provider, platform) names its
required schema, ADR, fixture, conformance, migration, capability, and documentation updates before
it can ship. A breaking change requires a new version axis value, a stated upgrade/read strategy, a
retained fixture for the old shape, and an explicit known-limitation entry. A deprecation states the
version it was first deprecated in, the last version it remains supported in, and its removal
target; alpha status does not by itself promise any particular support duration absent a published
policy.

To diagnose a mismatch: capture `version --json`, the exact error reason code, the bundle/manifest
version identities, and the public digests involved — never upload the bundle, database, object
file, key material, configuration, path, or a transcript. Compare the captured identities against
the release support matrix and choose one of: upgrade, run a supported migration, use a compatible
client/skill version, or restore from a verified backup. Never force-edit version metadata to make
an identity mismatch go away.

## Current support manifest

The live, exact per-release support/capability cells (tested Codex versions, provider profiles,
platform matrix, and the trigger-hook capability cell) are published with each release as part of
`version --json --resources` and the release evidence bundle, not hard-coded in this page.
