# src/yoetz/application/codex_plugin.py — owning Codex plugin install/inspect service

**Wave:** D | **ADRs:** ADR-010, ADR-012 | **Imports (spec-tree):**
`adapters/integrations/codex_plugin.py.md`, `ports/integrations.py.md` | **Imported by:**
`cli/setup.py.md`

## Purpose

Owns the application boundary for trusted-project Codex plugin preview/install/inspect so the setup
wizard never duplicates filesystem writes around the adapter installer.

## Public surface

- `CodexPluginPreview` — presence before apply, planned file count, trust observability, digest.
- `CodexPluginService.preview|inspect|install` — thin ownership over `install_plugin` /
  `inspect_plugin` / `render_plugin_tree`.

## Behavior

`install(..., allow_untested=False)` forwards to the adapter. Observation setup passes
`allow_untested=True` so hooks can install when the packaged Codex tested set is still empty, while
status/reporting continue to say automatic activation is untested. Modified-copy and unsafe-target
protections remain adapter-enforced.

## Errors and edge cases

`IntegrationError` reasons from the adapter propagate unchanged.

## Invariants

1. No filesystem plugin writes outside `install_plugin`.
2. Trust is never inferred from installation state alone.

## Tests

Covered by setup wizard subprocess tests (faked service) and `tests/unit/adapters/test_codex_plugin.py`.

## Open questions

None.
