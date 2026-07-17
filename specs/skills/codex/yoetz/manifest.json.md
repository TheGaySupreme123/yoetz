# skills/codex/yoetz/manifest.json — canonical Codex skill compatibility manifest

**Wave:** D | **ADRs:** ADR-002, ADR-005, ADR-007, ADR-010 | **Imports (spec-tree):**
`specs/skills/codex/yoetz/SKILL.md`, `specs/guidance/README.md` |
**Imported by:** skill installation, packaging, and capability validation

## Purpose

Define the canonical compatibility manifest shipped beside the reviewed Yoetz skill. It binds the
skill source to the exact installed references, version bounds, and supported Codex capability
profile IDs without exposing local paths or mutable environment state.

## Public surface

The future file is canonical JSON with an exact reviewed shape. Its top-level content records:

- `schema` — the manifest schema identity;
- `skill` — `yoetz`;
- `harness` — `codex`, the exact `HarnessId` this manifest belongs to;
- `skill_version` — the reviewed skill version;
- `protocol_version` — the frozen Yoetz protocol version;
- `guidance_version` — the reviewed version of the shared `guidance/` set this skill installs;
- `codex_version_bounds` — minimum supported and maximum tested Codex versions plus any denied
  versions;
- `capability_profile_ids` — the exact supported Codex capability-profile IDs frozen by the skill
  contract;
- `hooks_by_capability_profile` — exact same-key map whose values are tagged absent or the
  E-013-proven trigger-only descriptor; every v0.1 observation arm is absent;
- `managed_members` — the exact managed files with logical member name, byte size, SHA-256, role,
  and `origin` (`harness_owned` or `shared_guidance`);
- `member_digest` — SHA-256 over the canonical manifest content excluding the digest field.

Managed members are limited to the installed Codex skill file, this compatibility manifest as a
tracked resource, and the four shared guidance members installed under `references/`. Each guidance
member records `origin: shared_guidance` and the digest of the `guidance/<name>` resource it was
copied from, so a reviewer can prove the installed bytes are the shared bytes and not a Codex
variant. No local source checkout path, home directory, or repository-relative absolute path appears
in the manifest.

`harness` and `origin` are what make this manifest reusable: another harness's manifest differs only
in its harness-owned members and version bounds, and its `shared_guidance` digests must equal these.

## Behavior

The manifest is a review-time compatibility ledger, not a runtime bootstrap input. Source and
packaged copies are byte-identical. Packaging and install checks verify the exact member list,
member digests, version bounds, supported capability-profile IDs, and the exact same-key hook map
before trusting the skill.

The manifest does not invent compatibility beyond installed-artifact capability evidence. Codex
does not read it as skill frontmatter; it is Yoetz-owned compatibility and integrity data. If the
reviewed skill or any managed member changes, the manifest changes in lockstep or packaging fails.

## Errors and edge cases

- A missing, extra, duplicated, or digest-mismatched managed member fails packaging and
  installation checks.
- Any local path leakage or capability-profile drift fails closed.
- Missing/extra/inferred hook-map keys, a trigger without E-013 case IDs, or any v0.1 observation
  arm makes the manifest invalid.
- A manifest that is not canonical JSON or whose self-digest is wrong is invalid.
- A `shared_guidance` member whose digest differs from the `guidance/<name>` resource it names fails
  packaging: that is a Codex-local fork of shared content, which the layering forbids.
- A `harness` value other than `codex` in this manifest is invalid.

## Invariants

1. Source and packaged manifests are byte-identical.
2. Managed members are explicit and complete.
3. No local paths or environment-derived values appear.
4. Capability-profile IDs match the reviewed skill contract exactly.
5. Every `shared_guidance` member digest equals its `guidance/` resource digest.
6. The manifest shape is harness-neutral; only its values are Codex-specific.
7. Hook presence is exact-profile evidence and cannot be inferred from version bounds.

## Tests

- `specs/tests/packaging/test_wheel_and_sdist_contents.py`
- `specs/tests/packaging/test_resource_byte_parity.py`
- `specs/tests/capability/test_codex_skill_discovery.py`

## Open questions

None.
