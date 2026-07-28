# 04 — `provider endpoint --grok` must not fall into the generic picker

**Severity:** moderate **PR boundary:** CLI predicate and selector test coverage

## The defect

On an interactive TTY, `yoetz provider endpoint --grok` can enter the generic provider picker
instead of treating `--grok` as the chosen provider. The interactive-picker predicate excludes
`official` and `fireworks` but not `grok`:

`src/yoetz/cli/app.py:1095-1102`

```python
if (
    interactive
    and not official
    and not fireworks
    and provider_name is None
    and https_origin is None
    and model is None
):
```

Every other branch in the same function already handles `grok` correctly — the mutual-exclusion
block at `app.py:1112-1120`, the alias map at `1125-1138`, and the dispatch at `1151-1152`. This one
predicate was missed.

Neither observer agent in run 3 found it, and the tested path (`--grok --model grok-4.5`) works, so
it is invisible to the existing tests.

## Scope note

This is the only one of the four that is a product defect rather than a Yoetz runtime defect. It
lives in code introduced by **PR #40**, which merged at `2026-07-27T17:39:36Z` as `f3d1e62`, with
`a4eed0e "Repair Grok dogfood artifact boundaries"` resolving the committed-symlink CI failure. PR
CI on `main` is green after that merge. So this is a standalone PR against `main`; there is no
prerequisite left.

Confirmed still present on `origin/main`: the predicate at `src/yoetz/cli/app.py:1095-1102` has no
`and not grok`.

## Design

Add `and not grok` to the interactive-picker predicate, so `--grok` with no model on a TTY prompts
for the Grok model rather than re-asking which provider the operator already named.

Confirm the resulting behaviour matches `--fireworks` exactly. The two selectors should be
indistinguishable in shape; any difference is a second bug.

## Files

- `src/yoetz/cli/app.py` — the predicate at 1095-1102
- `tests/subprocess/test_setup_wizard_cli.py` and the CLI test module covering
  `provider endpoint`

## Tests

The postmortem names three gaps; close all of them.

- `--grok` with no model on an interactive TTY: prompts for the Grok model, does not enter the
  generic picker. Mirror the existing `--fireworks` test.
- Interactive menu choice `6` (`grok`, `src/yoetz/cli/provider_binding.py:125`) is exercised
  directly and writes the expected preset.
- Selector mutual exclusion across the full matrix: `--official`/`--fireworks`, `--official`/`--grok`,
  `--fireworks`/`--grok`, each of the three combined with `--provider`, and each combined with an
  https origin. The predicate at `app.py:1112-1120` already implements these; nothing pins them.
- The alias set (`grok`, `xai`, `x-ai`) resolves to the same preset.
- Non-TTY behaviour is unchanged.

## Done

Green CI, and `--grok` on a TTY behaves exactly like `--fireworks`.

## Dogfood observable

None required — this is deterministic CLI behaviour fully covered by tests. It does not need an
agent run to prove.

## Out of scope

Live xAI credential binding, egress, or any claim of proven Grok interoperability. The preset stays
structurally implemented and live-unproven until an independently authorized xAI chain runs.
