# docs/protocol/README.md — public protocol guide and trust-model landing page

**Wave:** A/F | **ADRs:** ADR-001, ADR-002, ADR-005, ADR-006, ADR-007 | **Imports (spec-tree):**
`specs/INTERFACES.md`, protocol/domain/application specs, public schemas and fixtures |
**Imported by:** public README, skill references, client implementers and release documentation

## Purpose

Specify the first public document a human or client implementer reads to understand Yoetz Core's
wire protocol: what it records, what it does not prove, the six operations, event/identity model,
canonical/idempotent behavior, coverage and findings, error handling, version discovery, and the
minimum safe client workflow.

The future document is explanatory but normative where it repeats frozen schemas/registry values.
It must be sufficient without any private/local planning material and must not include product
strategy, customer context, internal repository history, or unsupported roadmap claims.

## Public surface

The future Markdown file has these stable sections and anchors:

1. `# Yoetz Core protocol v0.1`
2. Status and normative sources
3. Trust model: ledger, observation, identity and coverage
4. Six public workflow operations
5. Event publication and immutable accepted envelopes
6. IDs, frontiers, writers and causal references
7. Canonical JSON, digests and idempotency
8. Checks, findings, responses and receipts
9. Coverage/freshness/unknown/redacted data
10. Errors, retry and cancellation
11. Minimal cooperative client workflow
12. Version and compatibility discovery
13. Security/privacy constraints
14. Schemas, fixtures and further reading

It links only to committed public ADRs/docs/schemas/fixtures with repository-relative links and no
unpinned remote page required to implement correct behavior.

## Behavior

### Status and normative boundary

Open with “v0.1 public alpha” and distinguish normative machine-readable schemas/canonical vectors/
ADRs from explanatory prose. State that conflicts are release defects; clients validate against the
installed/released schema set and `version --json` identities. Never call alpha compatibility
unconditional or silently treat prose examples as digest vectors.

### Trust model

Explain plainly:

- Yoetz is a local-first append-only work ledger and completion-integrity layer, not an executor,
  orchestrator, transcript recorder, identity provider, or proof that repository/world state matches
  a claim.
- Cooperative publications are assertions with explicit author type/assurance and observation
  channel. Imported records are bounded observations of a public format, not complete internal trace.
- Deterministic checks evaluate the published record and named policy at a frozen frontier. Optional
  semantic results are advisory, provenance-labeled and freshness fenced.
- Coverage is a vector; the weakest material dependency and explicit gaps bound receipt/final wording.
- A receipt records conclusions about captured work at a frontier; it is not a certificate or
  cryptographic proof of correctness/authorship.

Include a compact “You may say / Do not say” table: recorded/published/self-asserted, digest observed,
captured bytes checked, deterministic policy found no issue at coverage X versus “verified,” “proved,”
“authenticated,” or “complete” without exact sufficient coverage.

### Six operations

For `start`, `publish_work`, `check`, `respond`, `status`, and `receipt`, provide:

- intent and whether mutating;
- required request identity/idempotency behavior;
- important inputs (session/writer/frontier) and returned structural fields;
- durable acknowledgement boundary;
- representative conflict/retry behavior;
- what the operation cannot prove.

State that these are exactly six MCP tools/application workflows. Import/review/backup/restore/
migrate/version/integrate are CLI support surfaces, not extra tools.

`publish_work` accepts a bounded atomic batch of typed events: one invalid event rejects all. `check`
freezes a dependency/frontier case and may complete deterministic-only or with optional semantic
status. `respond` records disposition and never erases finding. `status` is bounded/paginated and
discloses projection frontier/lag. `receipt` uses durable current state, excludes its own publication
from subject frontier where specified, and never strengthens coverage.

### Events, identities and accepted envelopes

List the 16 event families and one-sentence purpose, generated/required IDs, key relationships, and
which payload fields are encrypted objects. Separate caller publication draft from accepted envelope:
server assigns ingestion/writer sequence, accepted time and chain digests under current generation.

Explain one task bundle, sessions, distinct writer streams, actor assertions, global and writer
predecessor chains, parent/reference rules, and `Frontier(sequence, head_digest)`. Sequence alone is
not a complete frontier identity. Concurrent publication ordering may differ while causal constraints
and deterministic reducers remain binding.

### Canonicalization, digests and idempotency

Summarize strict JSON rules: UTF-8, duplicate-key/floats/unsafe-number/unknown-field rejection,
normalized strings/timestamps/IDs, stable key/set ordering and domain-separated canonical digests.
Link canonical vectors. Warn that normal JSON serializer output is not necessarily canonical bytes.

Every mutating request uses stable `request_id`/operation identity. Same identity + same canonical
request returns original result; different request digest is conflict. Timeout/cancellation/response
loss means outcome unknown; retry exactly, then inspect operation/status. Never generate a new ID to
“make retry work.”

### Findings, coverage and receipts

Describe deterministic versus semantic origin, sparse ranking/cap, status (`open`, response and
supersession semantics per registry), response types, waiver authority/scope/expiry, and mandatory
recheck after material change/response. A response is history, not deletion.

Enumerate all coverage dimensions/values exactly from registry and identify ordered vs set-valued
dimensions. Explain subject-state binding, freshness, import/unknown/redaction gaps and conservative
combination with worked examples. Receipt conclusions and Markdown are derived from canonical JSON;
show bounded examples without real repositories/users.

### Errors, retry and cancellation

Group public error codes into invalid/conflict/pending-busy/storage-provider/internal/cancel families,
with retryability and safe client action. Errors contain bounded codes/details and correlation IDs,
never payload/path/prompt/key/SQL/traceback. Transport success/error and check verdict/findings are
distinct; findings do not make a protocol call fail.

State client deadlines never establish storage outcome; the durable operation row does. Cancellation
before commit has no acknowledged effect; shielded commit resolves then response/retry communicates it.

### Minimal workflow

Give a ten-step concise flow aligned with canonical skill: materiality, start/attach, plan/obligations,
publish material work, multi-writer attribution, status after resume, current check, respond, recheck,
receipt-bound final wording. Include stable request-ID pseudo-JSON links, not full transcripts or
hidden reasoning.

### Versions/security

Explain `version --json`, protocol/schema/storage/migration/projection/policy/object/resource/skill/
MCP identities and exact-tested compatibility, linking `compatibility.md`. Unknown newer storage
blocks writes; unknown events are retained/gapped, never interpreted optimistically.

Security section states local path/key/plaintext model: structural SQLite/log metadata, encrypted
payload objects, no secret/payload in errors/logs, strict-local zero provider egress, optional semantic
data minimization, and limits against compromised active account/root/memory. Link public threat ADR
and runbooks without claiming independent review is complete until release evidence says so.

## Errors and edge cases

- Generated enum/schema tables must be checked against the registry; drift blocks docs build.
- Examples use clearly synthetic IDs and pass strict model/schema validation; no real session/path/
  provider/customer/repository value.
- Links are relative, case-correct, and included in packaged/source documentation tests.
- Avoid normative SDK code that could drift; show wire-shape fragments tied to versioned fixtures.
- Do not promise an external-version range, platform, provider, portability, or signing mechanism
  beyond the current public support/capability manifest.

## Invariants

1. A new implementer can identify all six operations, retry rules and trust limitations from this
   document plus linked public schemas.
2. Wording never equates recorded/check/receipt with verification beyond coverage.
3. Every normative enum/field/example matches frozen public sources.
4. No private/local input or unsupported release claim appears.
5. The document distinguishes workflow operations from support commands.

## Tests

- Documentation lint checks headings/anchors/relative links, public-boundary vocabulary and forbidden
  overclaim wording.
- Generated operation/event/error/coverage tables compare to `INTERFACES.md` and schemas.
- Every JSON fragment validates; every digest example comes from canonical fixtures.
- Capability evaluation asks a clean implementer to explain timeout retry, unknown event, semantic
  limitation and receipt wording using only public docs.
- Packaging tests ensure source/public artifact includes the reviewed bytes when documentation is
  shipped.

## Open questions

None.
