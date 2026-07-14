# scripts/generate_release_evidence.py — assemble deterministic release proof bundle

**Wave:** F | **ADRs:** ADR-003, ADR-004, ADR-005, ADR-006, ADR-007 | **Imports (spec-tree):**
`specs/tests/packaging.md`, `specs/tests/capability.md`,
`specs/scripts/generate_capability_matrix.py.md`,
`specs/scripts/scan_public_boundary.py.md` | **Imported by:** tagged-release workflow and public
release verification

## Purpose

Assemble the evidence that justifies a specific public-alpha artifact and its bounded support
claims: artifact checksums, SBOM identity, build provenance, test/conformance summaries, resource
identity, SQLite/runtime/platform support, capability matrix, known limitations, and verification
instructions. The output describes proved bytes; it never manufactures pass evidence or signs on
behalf of an unavailable signing system.

## Public surface

- `ReleaseInputs`, `ReleaseArtifact`, `GateResult`, and `ReleaseEvidence` script-local records;
- `load_and_validate_inputs(manifest) -> ReleaseInputs`;
- `hash_artifacts(paths) -> tuple[ReleaseArtifact, ...]`;
- `evaluate_release_gates(inputs) -> tuple[GateResult, ...]`;
- `render_evidence_json`, `render_checksums`, `render_support_matrix`, `render_limitations`;
- `write_evidence_bundle(output_dir, documents) -> None`;
- `verify_evidence_bundle(output_dir) -> None`;
- `main(argv=None) -> int`.

Commands:

```text
uv run --locked python scripts/generate_release_evidence.py \
  --input-manifest dist/release-inputs.json \
  --output-dir dist/release-evidence/v0.1.0 --write
uv run --locked python scripts/generate_release_evidence.py \
  --input-manifest dist/release-inputs.json \
  --output-dir dist/release-evidence/v0.1.0 --check
```

`--write` and `--check` are mutually exclusive. Exit `0` means complete, internally consistent,
and all required gates pass; `1` means missing/failed/inconsistent/unsafe evidence; `2` invocation
error.

## Behavior

### Input manifest

The explicit canonical input manifest lists candidate version/tag/commit, clean-source archive,
built sdist/wheels, lock/build-tool identities, SBOMs, provenance records, resource manifest,
platform/SQLite probe manifests, test summary files, capability matrix, public-boundary scan reports,
license/vulnerability reports, and reviewed known-limitation source. Every path is relative to a
fixed CI artifact root and paired with expected SHA-256 and schema version.

The script reads no ambient `dist/` discovery, Git state, network, package index, CI API,
environment secret, or wall clock. All inputs must be regular immutable non-symlink files within
the root and below count/size caps. It verifies expected digest before parsing.

### Required evidence

Require, for every advertised artifact/platform:

- clean signed/tagged source identity and reproducible/traceable build-tool/lock identity;
- artifact filename, size, SHA-256, distribution tags, metadata and reviewed contents;
- exact Python, OS/CPU/ABI, APSW and SQLite version/source-ID/compile-option verdict;
- resource-set manifest digest and installed resource parity;
- unit/property/conformance/integration/subprocess/packaging/security/privacy/capability gate results
  under named revisions and caps;
- clean install, strict-local vertical slice, backup/migration/upgrade/rollback/uninstall/offline
  reinstall outcomes;
- public-boundary and secret scan for source, each artifact, SBOM/provenance, and evidence outputs;
- dependency vulnerability/license disposition and CycloneDX SBOM identity;
- exact Codex/MCP/platform/key/provider capability cells supporting each public claim;
- bounded known limitations and unsupported/untested matrices.

Test summaries contain suite/case counts, pass/fail/skip categories with policy-approved skip IDs,
duration/resource bounds, artifact/revision/platform identity, and evidence digest. They never
contain stdout transcripts, prompts, payloads, local paths, or exception traces.

### Gate evaluation

Each required gate produces deterministic `pass|fail|incomplete|not_applicable` with a bounded
reason code and input digests. `not_applicable` is legal only where the release policy explicitly
names it and must narrow a claim. Any missing required file/cell, unapproved skip, failing scan,
candidate mismatch, stale external evidence, unsupported advertised target, digest mismatch, or
conflicting result makes the release ineligible.

The script cannot turn a failure into a warning. Changing support scope or accepting a risk requires
a reviewed source policy/limitation change and complete regeneration.

### Output bundle

Write exactly these public files in ASCII order:

- `release-evidence.json` using schema `yoetz.release-evidence/1`;
- `SHA256SUMS` for release artifacts, SBOM/provenance, capability matrix, and evidence documents
  (excluding itself until the enclosing release manifest records it);
- `support-matrix.md` derived from structured platform/capability cells;
- `known-limitations.md` derived from reviewed bounded entries;
- copies or digest-bound links for `capability-matrix.json`, CycloneDX SBOM, and provenance document
  when publication policy includes them;
- `VERIFY.md` containing literal offline checksum/artifact/resource/version verification commands.

`release-evidence.json` records candidate/tag/commit, artifact list, lock/build/runtime identities,
resource and migration versions/digests, protocol/engine/policy/projection/object-format versions,
per-platform SQLite identities, gate results, capability/limitation IDs, SBOM/provenance digests,
and an evidence-set digest. It clearly distinguishes checksum, provenance attestation, and signature;
missing signing means `signature_status="not_provided"`, never implied authenticity.

All JSON is canonical, all text UTF-8/LF/final-LF, all tables deterministically ordered. Generation
uses a staged directory, verifies all output, scans public boundary, fsyncs, then atomically replaces
the owned version directory only when absent or exactly regenerable. It never overwrites evidence
for a different digest under the same version.

### Verification and publication handoff

`--check` recomputes every input/output digest and expected document byte, validates internal links,
ensures `SHA256SUMS` covers the intended immutable files once, and reruns the boundary scanner. The
tagged workflow runs it before upload and again after downloading candidate artifacts into fresh
environments. This script does not upload, tag, publish, announce, or mutate a release.

## Errors and edge cases

- Missing/inconsistent/failed evidence leaves no final output replacement and exits `1`.
- An artifact changing during hash is rejected by stat-before/after and expected-digest checks.
- Duplicate filenames, path traversal/collision, unknown schemas/fields, floats, bad timestamps,
  unbounded text, or unsupported archive type fail.
- A signing/provenance service outage is represented honestly and blocks only when policy requires
  that claim; it is never simulated.
- Vulnerability feed freshness is an input policy fact; this offline assembler does not query it.
- Console/report errors reveal only public-relative labels, bounded reason codes, and digests.

## Invariants

1. Every public support/release claim traces to exact candidate evidence.
2. Evidence generation is deterministic, offline, and cannot alter source artifacts or test data.
3. Failed/incomplete/untested never renders as pass/supported.
4. Outputs contain no private evidence, credentials, user content, raw transcripts, or local paths.
5. Checksums are not described as signatures; provenance/signature status is explicit.
6. One version cannot be silently rebound to different artifact/evidence bytes.

## Tests

- `specs/tests/unit.md`: parsers, gate policy, checksums, rendering, self-digests.
- `specs/tests/property.md`: input order/path/collision/mutation and deterministic bundles.
- `specs/tests/integration.md`: synthetic complete/incomplete/conflicting release sets and atomicity.
- `specs/tests/packaging.md`: real candidate assembly, clean verification, boundary scan, and
  downloaded-artifact re-verification.
- Tagged-release workflow dry run proves generation itself cannot publish.

## Open questions

None.

E-008 is the sole central release-evidence gate; signing remains a v0.2 deferral.
