# docs/runbooks/codex-integration.md — safe trusted-project Codex skill integration

**Wave:** D/F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):** integration port/application/
adapter, skill/reference, capability and compatibility specs | **Imported by:** CLI integration help,
public install guide and support

## Purpose

Guide a user through previewing, consenting to, installing, checking, replacing, and removing the
canonical Yoetz Codex skill in one explicitly trusted project while preserving modified/unmanaged
files. It also separates skill installation from MCP registration/availability and tested Codex
capability.

## Public surface

Future headings:

1. What integration installs—and does not
2. Prerequisites and exact supported scope
3. Status and preview
4. First install
5. Upgrade/replace a modified or partial copy
6. Verify Codex discovery and Yoetz availability
7. Optional versus required MCP behavior
8. Remove
9. Troubleshooting and recovery
10. Security/privacy and prohibited actions

Examples use `yoetz integrate codex skill install|status|remove` with an explicit synthetic
project root, JSON/preview/confirm-plan equivalents matching implemented CLI help.

## Behavior

### Scope and boundaries

State v0.1 installs only canonical `SKILL.md`, named references/compatibility data and nonsecret
managed marker at:

```text
<explicit-trusted-project>/.agents/skills/yoetz/
```

It does not edit Codex global/project config, register/start MCP, install/update Yoetz/Codex, touch
Git index/ignore/remote/branch, scan repository contents, install globally, manage other skills, or
make Yoetz mandatory. The project root must be explicitly supplied and trusted; no cwd/parent/fuzzy
discovery or symlink target.

Skill/reference source, wheel resource and installed managed files are byte-identical. Marker only
records versions/sorted digests and no path/user/time/repository data.

### Prerequisites/status

Verify installed `yoetz version --json`, resource set, current compatibility/capability matrix,
owner permissions, trusted repository and expected Codex version. Run `status` first. Explain states:
absent, installed exact, modified/unmanaged, partial, incompatible, unsafe. Status is read-only and
does not repair/update/register anything.

An identical directory without valid managed marker is unmanaged/modified and protected from removal.
Installed exact does not prove Codex has discovered it or MCP is available.

### Preview/install

1. Run install preview with explicit project. Review fixed destination/scope, source skill/protocol/
   resource/Codex tested identities, state, create/replace/no-op file digest/size changes, warnings and
   preview digest. A local bounded diff may show current modified text; it is not logged/uploaded.
2. For absent target, explicitly confirm exact preview digest. Non-TTY requires acceptance plus plan
   digest; no implicit prompt/hang. Adapter stages complete directory, verifies/fsyncs and atomically
   publishes; success status is exact.
3. Existing exact returns no-op. Incompatible/unsafe stops without force.
4. Timeout/cancellation around swap: run status with same target/request; do not rerun against a new
   preview until old/new/partial state is known.

### Modified/partial replacement

Default install never overwrites. Inspect/retain a copy using the user's normal source control/manual
process. If user deliberately wants packaged source to replace it, request `replace_modified` before
preview, review exact current digest/diff and confirm that exact preview. A generic `--yes` is not
enough. Any concurrent edit makes preview stale and preserves files.

Replacement stages/swap-recovers whole directory rather than merging files. Interrupted ambiguous
old/new/rollback content remains preserved and requires status/new preview/manual review; never delete
staging/rollback blindly.

### Discovery and MCP availability

After exact install, launch the exact tested Codex in this trusted project and explicitly invoke
`$yoetz` for alpha unless implicit discovery is advertised by current capability matrix. Verify
skill recognized/version compatible and workflow guidance. Installation alone is not evidence.

MCP registration is separate. If configured optional and Yoetz unavailable, Codex work continues and
skill must disclose no live ledger/check/receipt. If host/user configured required, server failure
blocks run as that policy specifies. Never claim skill install changed config or produced a receipt.

### Remove

Run remove preview/status, confirm exact preview digest. Adapter removes only a valid managed marker +
byte-exact inventory. Modified/partial/unmanaged content is refused/preserved; there is no force-remove
in v0.1. Removal does not uninstall package, delete MCP config, ledger/key/data or other skills. Verify
status absent and manually manage any separate MCP config if desired.

### Troubleshooting/security

Decision table: target untrusted/unsafe → correct explicit root/permissions, no force; resource invalid
→ reinstall verified artifact; preview stale → status/new preview; modified/partial → preserve/review;
incompatible → use supported package/Codex pair; write/swap interrupted → status, preserve staging;
skill not discovered → check exact scope/trust/version/capability and Codex reload; MCP unavailable →
separate config/startup diagnostics.

Never paste modified skill/repository content, paths, Codex config, transcript, prompts, keys, env or
raw exceptions into public support. Share versions, state, source/installed/preview digests, bounded
reason and file-state names only.

## Errors and edge cases

- Repository may intentionally track `.agents`; adapter never stages/commits and user reviews Git
  diff separately.
- Multiple worktrees are distinct explicit roots; no propagation/global assumption.
- Source update changes preview digest and requires fresh consent even if installed old copy exact.
- A user may choose manual management; such copy remains unmanaged and removal-safe.
- Global/user skill scope and implicit trigger are not advertised absent exact release evidence.

## Invariants

1. One explicit trusted-project scope and fixed destination.
2. Preview + exact consent precedes every mutation.
3. Modified/unmanaged content is never silently overwritten/removed.
4. Installed files equal manifest-verified packaged source.
5. Skill install, MCP config, Yoetz availability and Codex capability remain separate facts.

## Tests

- Execute all examples against installed CLI with absent/exact/modified/partial/incompatible/unsafe
  fixtures and non-TTY mode.
- Capability tests prove explicit discovery, optional/required behavior and exact tested versions.
- Kill/swap tests validate troubleshooting branch and modified preservation.
- Docs lint rejects global/fuzzy scope, forced overwrite/remove, MCP-registration claim, secret/path/
  transcript examples and unsupported version ranges.

## Open questions

None.
