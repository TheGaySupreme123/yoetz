# skills/codex/yoetz-core/manifest.json — canonical skill compatibility manifest

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007 | **Imports (spec-tree):**
`specs/skills/codex/yoetz-core/SKILL.md`, `specs/skills/codex/yoetz-core/references.md` |
**Imported by:** skill installation, packaging, and capability validation

## Purpose

Define the canonical compatibility manifest shipped beside the reviewed Yoetz skill. It binds the
skill source to the exact installed references, version bounds, and supported Codex capability
profile IDs without exposing local paths or mutable environment state.

## Public surface

The future file is canonical JSON with an exact reviewed shape. Its top-level content records:

- `schema` — the manifest schema identity;
- `skill` — `yoetz-core`;
- `skill_version` — the reviewed skill version;
- `protocol_version` — the frozen Yoetz protocol version;
- `codex_version_bounds` — minimum supported and maximum tested Codex versions plus any denied
  versions;
- `capability_profile_ids` — the exact supported Codex capability-profile IDs frozen by the skill
  contract;
- `managed_members` — the exact managed skill files with logical member name, byte size, SHA-256,
  and role;
- `member_digest` — SHA-256 over the canonical manifest content excluding the digest field.

Managed members are limited to the installed skill file, the two installed reference files, and
this compatibility manifest itself as a tracked resource. No local source checkout path, home
directory, or repository-relative absolute path appears in the manifest.

## Behavior

The manifest is a review-time compatibility ledger, not a runtime bootstrap input. Source and
packaged copies are byte-identical. Packaging and install checks verify the exact member list,
member digests, version bounds, and supported capability-profile IDs before trusting the skill.

The manifest does not invent compatibility beyond the frozen skill frontmatter. If the reviewed
skill or any managed member changes, the manifest changes in lockstep or packaging fails.

## Errors and edge cases

- A missing, extra, duplicated, or digest-mismatched managed member fails packaging and
  installation checks.
- Any local path leakage or capability-profile drift fails closed.
- A manifest that is not canonical JSON or whose self-digest is wrong is invalid.

## Invariants

1. Source and packaged manifests are byte-identical.
2. Managed members are explicit and complete.
3. No local paths or environment-derived values appear.
4. Capability-profile IDs match the reviewed skill contract exactly.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/packaging/test_resource_byte_parity.py`
- `specs/tests/capability/test_codex_skill_discovery.py`

## Open questions

None.
