# Yoetz v0.1 — file-level specification tree

**Status:** Working draft, written before any implementation code exists.
**Method:** Every future code, data, resource, test, workflow, script, and public-document file in
the public `yoetz` repository has one Markdown spec
here, at the mirrored path (`specs/src/yoetz/protocol/canonical.md` specifies the future
`src/yoetz/protocol/canonical.py`). Each spec is the file's code in natural language: what it
exports, what it imports, how every function behaves, how it fails, and which tests lock it.
`specs/FILE_MANIFEST.md` is the exhaustive future-path ↔ owning-spec ledger. Directory-family
indexes such as `specs/tests/unit.md` organize files but never substitute for the mirrored spec of
an actual future file.

## Drafting inputs and public self-containment

Ignored local architecture/strategy documents are drafting inputs only. They contain broader
private context, are intentionally excluded from the public repository, and are not an authority,
runtime input, test oracle, or publication dependency after this tree freezes.

The public authority order is:

1. `docs/adr/ADR-001` … `ADR-012` decisions at their recorded status, with unresolved
   ratification gates centralized in `specs/OPEN_QUESTIONS.md`;
2. `specs/INTERFACES.md` for shared/public/cross-adapter names and semantics;
3. the one owning file spec recorded in `specs/FILE_MANIFEST.md`;
4. family indexes and explanatory runbooks, which may summarize but never widen the above.

**Self-containment rule:** every normative fact needed to implement, test, package, operate, or explain
the public v0.1 product MUST be restated in the committed ADRs, `specs/INTERFACES.md`, or the owning
file/resource/test spec. Public specs do not cite ignored input files as required authority. Before
the tree is locked, a self-containment review removes the ignored inputs and must still be able to
implement the same system without guessing.

`specs/INTERFACES.md` is the shared vocabulary registry: canonical names for IDs, error codes,
event families, coverage enums, port signatures, and cross-module exports. Every spec file MUST
use those exact names. A spec that needs a name not in the registry adds it there in the same
change.

## Spec-file template

Every owning file spec follows this shape. All seven sections are present; an empty decision set is
written explicitly as `None.` under Open questions:

```markdown
# <future path> — <one-line role>

**Wave:** A–F | **ADRs:** ... | **Imports (spec-tree):** ... | **Imported by:** ...

## Purpose
Why this file exists and what would break without it.

## Public surface
Every exported name with a one-line signature-level description. This section must agree with
specs/INTERFACES.md.

## Behavior
The code in natural language, per class/function: inputs, validation, algorithm, outputs,
side effects, transaction/IO boundaries. Deep enough that two engineers implementing it
independently would produce behaviorally identical files.

## Errors and edge cases
Which failures are raised/returned, mapped to the public error codes where applicable; what is
never allowed to leak.

## Invariants
The binding rules this file enforces or must never violate.

## Tests
The test files/fixtures that lock this behavior (referencing specs/tests/).

## Open questions
Anything a founder must still decide. `None.` when no local choice remains; centralized founder or
release gates are referenced only as explanatory text after that marker.
```

## Wave map (build order)

| Wave | Content | Spec directories |
|---|---|---|
| A | Contract freeze: ADRs, JSON Schemas, canonical/privacy/service vectors, adversarial fixtures | `docs/adr/`, `specs/schemas/`, `specs/fixtures/` |
| B | Pure truth and policy engine (no SQLite/MCP/network) | `protocol/`, `domain/`, `kernel/`, `ports/` (definitions), in-memory adapters |
| C | Durable local bundle plus persistent trusted service/vault | `service/`, local-control adapters, `adapters/sqlite/`, `adapters/objects/`, `adapters/keys/`, `config/paths` |
| D | Application use cases and client surfaces | `application/`, `cli/`, `mcp/`, `adapters/mcp_stdio`, `adapters/importers/`, `specs/skills/` |
| E | Privacy-gated semantic evaluation and bounded outbound dispatch | `application/egress`, `application/privacy_policy`, privacy adapters, `adapters/providers/` |
| F | Packaging and public-alpha evidence | `version`, `specs/tests/packaging.md`, release workflows |

## Public layout additions and ownership decisions

The public file map includes these explicit modules because the behavior they own cannot remain an
implicit composition detail:

1. `src/yoetz/adapters/mcp_stdio.py` — the Yoetz-owned bounded stdio transport. The CLI/MCP
   contract already imports `yoetz.adapters.mcp_stdio.bounded_stdio_server`; the earlier
   manifest draft omitted the file. Added.
2. `src/yoetz/kernel/policies/` package with `work_integrity.py` and `research_evidence.py`.
   ADR-006 and the public policy inventory require two deterministic policy packs; giving each a file keeps
   `deterministic_checks.py` as the engine and the packs as versioned data+rules.
3. `src/yoetz/adapters/memory/` package (`ledger.py`, `start_catalog.py`, `objects.py`) —
   the in-memory reference adapters the conformance suite runs against SQLite.
4. `src/yoetz/ports/runtime.py` and its concrete runtime adapter — exact task routing,
   capability admission, process ownership, and task-scoped port lifetime.
5. `src/yoetz/ports/importer.py` plus parser and persistence adapters — bounded source capture,
   crash-safe import planning/publication, and immutable import-report evidence.
6. maintenance and integration modules — backup/restore/migrate and explicit Codex skill lifecycle
   remain support commands, not hidden branches of the six public workflow operations.
7. `src/yoetz/service/`, local control/secret-ingress adapters, and the service schemas — one
   persistent per-user authority owns vault keys, decrypted local state, writers, application
   composition, privacy policy, and outbound dispatch. CLI, MCP, and future ordinary UI are clients.
8. privacy domain/application/port/adapter modules, `PRIVACY.md`, ADR-009, the technical protocol,
   policy/setup schemas, and fixtures — all external requests and model/agent disclosures traverse
   one classifier → effective policy → local minimization/redaction/scanning → optional exact-case
   human approval → gateway → receipt path. Composition passes reviewed bundled provider adapters
   no repository/storage/environment/transcript handles; this is not OS/process sandboxing from a
   malicious adapter already inside the trusted service.
9. orthogonal review-context profiles and structured semantic review packets — the safe installation
   seed remains `local_only + structural`, while the CLI may recommend an editable standing
   `trusted_provider + assisted` recipe for an eligible exact endpoint. Deterministic checks supply
   machine-readable bases; selected problem-local recorded excerpts may accompany them; accepted
   model challenges return through the existing finding/respond/publish/check loop rather than a
   new operation or provider-driven fetch channel.
10. `src/yoetz/ports/subject_state.py` plus `src/yoetz/adapters/git_subject_state.py` — one narrow
    client-local owner for structural Git state capture under ADR-011. It fills the existing
    `SubjectStateRef` freshness anchor without returning repository content, adding an MCP tool, or
    creating general live artifact inspection.
11. ADR-012 first-run setup surfaces — `src/yoetz/ports/harness_mcp.py` (MCP registration is a
    sibling port, not an `IntegrationsPort` overload), `src/yoetz/application/harness_mcp.py`
    (digest-bound confirmation service), `src/yoetz/adapters/integrations/codex_discovery.py`
    (pure PATH observation, no capability claim) and `codex_mcp.py` (the runbook's check-then-add
    sequence, verify-by-reread, no force path), `src/yoetz/cli/setup.py` (wizard orchestration
    kept out of `app.py`), and `support/npm-launcher/` (`package.json`, `bin/yoetz.js`,
    `README.md` — a dependency-free, deliberately unpublished delegation launcher whose behavior
    cannot be an implicit detail of the dev-only root `package.json`).

## Status board

The manifest is the canonical inventory. At this draft checkpoint it classifies 569 spec files:
555 unique future-file owners, 10 directory indexes, and 4 coordination files. Every future owner
has all seven required sections; all local Open questions are closed or routed to the central
decision ledger.

| Future repository scope | Exact file owners | Additional spec coordination |
|---|---:|---|
| Repository root | 15 | — |
| `.github/` workflows | 9 | — |
| `docs/` public protocol and runbooks | 11 | Already-authored ADRs remain current authorities outside the future-file universe. |
| `schemas/` | 53 | 1 directory index |
| `fixtures/` | 49 | 1 directory index |
| `migrations/` | 2 | — |
| `guidance/` harness-neutral agent guidance | 4 | 1 directory index; owned once and shipped byte-identically to every harness and to MCP (ADR-010) |
| `skills/` | 2 | Per-harness header and manifest only; v0.1 ships exactly one harness, `codex` |
| `support/` | 4 | Includes the ADR-012 npm launcher (`support/npm-launcher/`), publish-ready but deliberately unpublished. |
| `src/yoetz/` Python/code files | 141 | — |
| `src/yoetz/resources/` | 72 | The resource manifest plus exactly 71 installed entries. |
| `scripts/` | 6 | — |
| `tests/` | 187 | 7 suite indexes |
| **Total future files** | **555** | **10 indexes + 4 coordination files = 569 spec files** |

All rows remain `draft` until the founder freeze; “present” is not the same as “reviewed” or
“locked.” Empirical release cells and independent threat review remain later evidence gates even
after the natural-language implementation contract is accepted.

## Honesty rules that bind every spec

- Verification language is coverage-bounded: "no issue detected at coverage X" is never rendered
  as "verified".
- Every retryable write has an idempotency identity; timeout never proves failure.
- Nothing user-controlled (payloads, titles, paths, prompts, model output) appears in SQLite
  structural tables, logs, errors, or MCP text summaries.
- Deterministic behavior depends only on canonical recorded inputs plus versioned policy/engine.
- Semantic output is advisory, provenance-labeled, and deterministically fenced.
- Missing or undisclosed source is never represented as unchanged source, and recommendation
  evidence never becomes disclosure authority.
- The trusted service is the sole holder of keys and decrypted state. Secret material never uses
  ordinary CLI/MCP arguments, environment variables, configuration, logs, traces, transcripts, or
  LLM context as an ingress or storage channel.
- `semantic_required` never erases a completed deterministic result: semantic unavailability or
  failure returns that result as `incomplete_check`, with no semantic findings and an exact gap.
- Every network channel is independently denied or authorized. No profile can override the
  never-send set, and only a reauthenticated local human can loosen effective policy.
