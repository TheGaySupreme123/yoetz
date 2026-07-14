# tests/packaging/test_clean_install.py — fresh advertised-platform installation vertical slice

**Wave:** F | **ADRs:** ADR-003, ADR-004, ADR-005, ADR-007 | **Imports (spec-tree):** candidate
artifact, install docs, CLI/MCP/resource/platform specs | **Imported by:** release platform gate

## Purpose

Prove a new user can install exact candidate bytes into a clean advertised environment and run the
strict-local product without checkout, ambient config, global executable, provider secret, or
runtime network.

## Public surface

Parameterized platform cells cover documented managed-Python/tool installation, base package and
each optional extra, import/help/version, six-operation CLI slice, MCP initialize/list, deterministic
receipt, resource/runtime/SQLite identity, and unsupported-environment refusal.

## Behavior

Provision empty HOME/XDG/app-data and no source tree. Install candidate by filename+hash using exact
documented managed Python (initially 3.14.6 policy) and captured dependencies; resolve executable
inside new environment. Assert metadata/resources, startup probes and exact APSW/SQLite source/
options. With network denied and no provider env, create a private bundle and execute start,
publish/check/respond/status/receipt, then raw MCP initialize/tools list.

Install each optional extra separately and exercise only its named adapter; base behavior remains
unchanged. Record filesystem/key backend state and owner permissions. On wrong Python patch/
distribution/OS/ABI/SQLite, structural version/read-only behavior may work but writes fail with
bounded honest limitation.

## Errors and edge cases

- PATH/global module/source checkout contamination fails setup.
- No interactive prompt or real user keychain/repository is used.
- Installer registry outage is irrelevant in offline subcase; missing captured artifact fails.
- Test destroys only its isolated app-data/key namespace.

## Invariants

1. Installed artifact alone supplies code/resources/entry points.
2. Strict-local vertical slice requires no network/provider secret.
3. Support is exact platform/runtime/SQLite evidence.
4. Optional extras do not silently enable or change base semantics.

## Tests

Run for every advertised artifact/platform and representative denied cells. Evidence contains
candidate/runtime/result digests, not bundle/user content.

## Open questions

None.
