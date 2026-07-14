# tests/capability/test_codex_config_and_startup.py — pinned Codex registration/startup evidence

**Wave:** D/F | **ADRs:** ADR-001, ADR-005, ADR-007 | **Imports (spec-tree):** capability evidence,
MCP/config/startup specs | **Imported by:** capability support matrix

## Purpose

Observe whether each exact Codex version accepts the supported user/trusted-project MCP
configuration, starts the installed server within bounds, and degrades/fails honestly.

## Public surface

Parameterized cells cover each reviewed Codex version × advertised platform × user/project config ×
optional/required server. Cases include cold/warm start, untrusted project, malformed command,
missing executable, slow startup, incompatible protocol, stdout noise, and failed diagnostic gate.

## Behavior

Install exact candidate and Codex binary by verified digest in isolated HOME/repository. Write only
the version-specific public config shape, then invoke real Codex and capture the public interaction
boundary. Assert configuration discovery, command/env/cwd resolution, trust behavior, negotiated
identity, and no mutation/overwrite of pre-existing config.

Measure multiple cold/warm starts with monotonic clock; record percentiles and bound, not raw paths.
The schema/resource/key/SQLite startup gate finishes before stdin/tool availability. Optional failure
allows Codex to continue with explicit Yoetz unavailable state; required failure blocks run startup.

## Errors and edge cases

- Documentation-only compatibility, wrong reported binary version, timeout, or runner drift is not
  pass.
- Config/command diagnostics cannot echo local paths, environment, or user values.
- Tests never touch real HOME, trusted repositories, or user Codex configuration.
- Unsupported cells emit explicit evidence and narrow claims.

## Invariants

1. Evidence binds exact Codex/candidate/platform identities.
2. Trust/required policy behaves as configured, without silent fallback.
3. Startup completes all safety gates before tool service.
4. Test leaves no persistent config/process/data outside its root.

## Tests

Each cell emits canonical evidence with start samples, result codes, protocol identity, and private
transcript digest. Negative controls prove config overwrite/path leak/noise are detected.

## Open questions

None.
