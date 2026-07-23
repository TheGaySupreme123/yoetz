# src/yoetz/adapters/integrations/codex_plugin.py — Codex plugin bundle renderer/installer

**Wave:** D | **ADRs:** ADR-010 | **Imports (spec-tree):** `codex_skill.py.md`,
`ports/integrations.py.md`, `ports/harness_mcp.py.md` | **Imported by:** setup/status surfaces and
plugin unit tests

## Purpose

Render and optionally install a versioned Codex plugin tree that bundles the Yoetz skill members,
`.mcp.json`, and lifecycle `hooks/hooks.json`, without claiming supported automatic activation while
the Codex tested set remains empty.

## Public surface

- `PLUGIN_ROOT` — trusted-project relative root `.agents/plugins/yoetz`.
- `render_plugin_tree(resource_source=None) -> dict[str, bytes]` — in-memory path→bytes mapping.
- `install_plugin(target, *, replace_modified=False, resource_source=None) -> PluginInspection`.
- `inspect_plugin(target, *, resource_source=None) -> PluginInspection`.
- `PluginHookPresence` — `absent` | `installed_untrusted_unknown` | `installed`.
- `PluginInspection` — presence, `trust_observable` (always false from files alone), digest, notes.

## Behavior

Rendered tree includes `.codex-plugin/plugin.json` (name `yoetz`, version = package `__version__`),
`hooks/hooks.json` wiring `UserPromptSubmit`, `PostToolUse` matcher `^mcp__yoetz__start$`, and
`SessionStart` matcher `resume|compact` to `yoetz hooks ...`, `.mcp.json` for `yoetz mcp serve`, and
`skills/yoetz/**` members from `load_packaged_skill_members` (no duplicated SKILL bytes).

Installer follows skill conventions: by default refuse when `harness_tested_set` is empty
(`version_incompatible`); observation setup may pass `allow_untested=True` to install hooks while
still reporting that automatic activation is untested. Refuses modified managed files unless
`replace_modified`, write a
nonsecret marker, reject symlinked or unsafe `.agents/plugins` ancestors, and stage/swap atomically.
If the staged-tree swap fails after moving an existing managed installation aside, restore that
installation before returning `write_failed`. Inspection never infers Codex trust from file presence; the
note `codex_hook_trust_not_observable_from_installation_state` is always attached.

## Errors and edge cases

- Empty tested set → install refused; machinery exists but support is not claimed.
- Modified local files → `modified_copy`.
- Unsafe/untrusted project roots → `target_untrusted` / `target_unsafe`.
- Symlinked or unsafe managed-path ancestors → `target_unsafe` without writing through them.

## Invariants

1. Default posture is fail-closed for unprofiled Codex.
2. Trust is not observable from installation state alone.
3. Hook wiring references only the three Issue-3 handlers; Stop is absent.

## Tests

- `tests/unit/adapters/test_codex_plugin.py`

## Open questions

Exact marketplace install path vs trusted-project `.agents/plugins/yoetz` may be refined when a
supported Codex profile exists.
