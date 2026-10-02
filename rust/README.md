# Yoetz in Rust (experimental)

This workspace is an experimental Rust port of Yoetz, built for speed. It does not replace the
Python package: the Python package remains the authority for behavior (ADRs, `docs/INTERFACES.md`,
`schemas/`, `fixtures/`, and the Python test suite lock the contract), and every ported function
must match its Python reference exactly.

## Layout

| Crate | What it is |
|---|---|
| `crates/yoetz-core` | Pure Rust twins of pure Python modules. No Python dependency, so native tools can link it. |
| `crates/yoetz-native` | The `yoetz_native` Python extension module (PyO3). Bindings that walk live Python objects and call into `yoetz-core`. |
| `crates/yoetz-cli` | `yoetz-rs`, native command-line tools built on `yoetz-core`. |

## How the accelerator is used

The `yoetz` wheel stays pure Python (`py3-none-any`; the packaging tests require it) and never
depends on `yoetz_native`. `src/yoetz/_native.py` imports the accelerator when it is installed and
its `INTERFACE_VERSION` matches. A Python module that has native twins ends with a
`_bind_native()` call that resolves every twin it needs through `native_functions(...)` and, only
if all are present, rebinds its public names with `globals().update(...)`. Because the module
globals are rebound at import time, every importer, including `from module import name`, reaches
the twin. Without the accelerator the Python definitions stay in place unchanged.

| `YZ_NATIVE` | Effect |
|---|---|
| unset | Use the accelerator when it is installed and current. |
| `0` | Keep the pure-Python implementations. |
| `require` | Fail the import when the accelerator or any bound twin is missing (parity runs). |

The variable sits outside the `YOETZ_` namespace on purpose: strict configuration loading rejects
unknown `YOETZ_*` variables.

`require` is inherited by child processes. Tests that provision an independent runtime from a
freshly built wheel (`tests/subprocess/test_release_runtime_replacement.py`, `tests/packaging/`)
run a Python environment without the accelerator by design, so run those suites with `YZ_NATIVE`
unset: the checkout's own processes still auto-detect and use the accelerator.

Build and install into the checkout's virtual environment:

```text
rust/build-native.sh          # release build (fat LTO)
rust/build-native.sh --dev    # native-dev profile, for iteration
```

`uv sync` (exact) removes the accelerator again; `uv run` leaves it in place.

## Parity rules for ported functions

A twin is only correct when the Python test suite passes unchanged with `YZ_NATIVE=require` *and*
with `YZ_NATIVE=0`. The tests are never edited to fit the port.

1. **Same output bytes.** Canonical JSON, digests, rendered text, ordering, and float-free integer
   formatting must be byte-identical.
2. **Same refusal.** The same exception class with the same reason code, raised for the *first*
   offending input in the reference's evaluation order (for example: mapping keys in insertion
   order before member values in sorted-key order). Python exception classes are bound into the
   accelerator at import (`bind_protocol_value_error`, `bind_canonical_fragment`), never imported
   by Rust.
3. **Same type rules.** `type(x) is int` is not `isinstance(x, int)`; `bool`, `IntEnum`,
   `StrEnum`, and `str`/`dict`/`list` subclasses take the reference's path. Mappings that are not
   exact `dict`s (or an exact `JsonObject`) defer the whole call to the reference, which iterates
   them itself.
4. **Honor monkeypatch points.** Tests replace module attributes to observe or fault calls. A twin
   must not bypass a dependency or table the reference reads at call time: it defers to the Python
   reference whenever such a module global is not the original object (see `strict_json_parse` and
   `json.loads`). Consumer modules keep calling `canonical_encode`/`canonical_digest` through their
   own module globals, so patches on those modules still intercept.
5. **No native stack exhaustion.** Recursion over caller-controlled structures is bounded or
   iterative. A walker that hands a deep subtree to its Python reference marks the thread
   reference-only for that call, so CPython's recursion guard decides, never a native crash.
6. **No panics across the boundary.** Dictionaries are never iterated with PyO3's iterator while
   Python code can run inside the loop; mutation raises what CPython raises.
7. **Refusals come from the reference.** A native exception has no frame in the owning module
   (diagnostics record the innermost `yoetz` frame as the origin) and none of the reference's
   `__cause__`/`__context__` chain. A twin bound over a Python function is therefore wrapped:
   the accepted path stays native, and on any exception the wrapper calls the Python reference
   *after* its `except` block (`yoetz._native_replay`), which returns or raises exactly what the
   reference does. A twin may raise to defer whatever only the reference may touch (a value whose
   Python code would run, a document nested past `DEFER_NESTING`, whose verdict depends on
   CPython's stack-dependent recursion guard). Native code that still raises builds its errors
   through `registry::protocol_error`/`protocol_error_from`, which chain `__context__` the way a
   Python `raise` does.
8. **No retained plaintext.** Caches never keep decrypted payloads, content or large strings alive
   beyond the reference's own lifetime semantics.
9. **No user content in errors.** Reason codes only, as in Python.

## What is ported

The port follows where the time goes. A profile of the suite in pure-Python mode put about 82% of
Yoetz-owned CPU in five modules (canonical JSON 32%, JSON Schema validity 29%, wire models 8%, JSON
freezing 7%, id validation 6%); all five are ported, along with every other measured hot loop.
Orchestration, I/O, locks, the SQLite and cryptography boundaries, pydantic models, and the frozen
dataclasses that make up the object model stay in Python: the tests construct, compare, patch and
`dataclasses.replace` them directly, so they are the contract.

| Area | Python module(s) | Native twins |
|---|---|---|
| Canonical JSON | `protocol/canonical.py` | strict parser, encoder, digests, fragments, single-pass round-trip check |
| Ids, timestamps, JSON values | `protocol/ids.py`, `domain/values.py` | id grammar, RFC 3339 millisecond parsing, `freeze_json` |
| JSON Schema | `protocol/schemas.py` | compiled draft 2020-12 validity checker; catalog freeze and reference resolution |
| Wire models and control frames | `protocol/models.py`, `service/control_protocol.py`, `application/service.py`, `application/status.py` | leaf walks, plain-wire conversion, fused frame validate/encode/decode |
| Privacy | `observability/privacy.py`, `adapters/privacy/local_enforcer.py`, `application/check_change.py` | sensitive-content scanner, redaction, never-send scan |
| Observation | `domain/observation*.py`, `adapters/integrations/observation_local.py`, `codex_session_stream.py`, `adapters/sqlite/observation.py`, `application/observation_*.py` | exact CPython `shlex`, command normalization and classification, envelope decode, store memo and dedup ring, stream mapping, identities |
| Hooks | `cli/hook_io.py`, `cli/observe_hooks.py`, `cli/hook_timing.py` | Cursor hook parse, patch sanitizing, timing fold |
| Kernel | `kernel/reducers.py`, `kernel/projections.py`, `kernel/deterministic_checks.py`, `kernel/policies/*.py`, `domain/events.py` | replay identity carry, secondary effects, case validation, advice scan, entry digests |
| AI-review cases | `application/semantic_case.py` | envelope bounding, packet assembly, clipping |
| Imports and MCP | `adapters/mcp_stdio.py`, `adapters/importers/*.py`, `mcp/descriptors.py`, `mcp/server.py` | `json.loads`-compatible frame and line parsers, batch partition, descriptor bundling |
| Storage | `adapters/objects/envelope.py`, `adapters/keys/secret_memory.py`, `adapters/sqlite/connection.py`, `service/bundle_upgrade.py` | envelope decode, `mlock`/zeroize through libc, SQL authorizers, row digests |
| Filesystem | `adapters/git_subject_state.py`, `git_change_capture.py`, integrations helpers | `openat`/`fstatat` tree and index walks, untracked hashing, `/proc` scan |

## Performance

`rust/bench/compare.py` on a shared 4-CPU Linux container (minimum of several runs, each mode in a
fresh interpreter over identical inputs; absolute times vary with load, the ratios are stable):

| Operation | Pure Python | Rust accelerator | Speedup |
|---|---:|---:|---:|
| canonical: strict_json_parse (781 KiB state) | 35.1 ms | 2.4 ms | 14.4x |
| canonical: canonical_encode (781 KiB state) | 35.3 ms | 1.1 ms | 30.9x |
| canonical: canonical_digest (781 KiB state) | 35.9 ms | 1.6 ms | 22.2x |
| privacy: scan_for_sensitive_content (400 KiB source) | 74.9 ms | 478 µs | 156.4x |
| privacy: redact_sensitive_content (400 KiB source) | 91.4 ms | 474 µs | 192.9x |
| schemas: cold catalog load (216 schemas) | 676.1 ms | 122.7 ms | 5.5x |
| status: cached 100-row evidence page, all stages (CPU) | 241.0 ms | 25.9 ms | 9.3x |
| status: closure-prepare of a ~1,000-event ledger (CPU) | 2695.9 ms | 322.2 ms | 8.4x |
| semantic case: bound a large review envelope to 40% | 391.2 ms | 1.3 ms | 295.3x |
| kernel: genesis replay of a 1007-record ledger | 296.3 ms | 120.9 ms | 2.5x |
| hooks: Cursor hook ingress (607 KiB body) | 59.8 ms | 2.5 ms | 24.2x |
| hooks: sanitize a 256 KiB apply_patch | 4.0 ms | 246 µs | 16.2x |
| process: import yoetz.mcp.server (wall) | 2032.0 ms | 1331.8 ms | 1.5x |
| process: one Claude Code PreToolUse hook (wall) | 172.8 ms | 148.8 ms | 1.2x |

The status-page and closure figures are the end-to-end paths of
`tests/integration/application/test_status_render_cost.py`, whose cost bounds are expressed in
units of `canonical_encode` and therefore only hold in native mode because the whole page path,
not just the encoder, got faster.

## Verification

```text
cargo fmt --manifest-path rust/Cargo.toml --all --check
cargo clippy --manifest-path rust/Cargo.toml --all-targets -- -D warnings
cargo test --manifest-path rust/Cargo.toml -p yoetz-core -p yoetz-cli
YZ_NATIVE=require uv run pytest tests/unit tests/property tests/conformance tests/integration
YZ_NATIVE=0 uv run pytest tests/unit tests/property tests/conformance tests/integration
uv run pytest tests/subprocess tests/packaging     # YZ_NATIVE unset, see above
uv run python rust/bench/compare.py                # pure Python vs accelerator, same inputs
```

`.github/workflows/rust-native.yml` runs the Rust gates and both `YZ_NATIVE` modes of the Python
suite.

## Limits

- The object model stays Python. Frozen dataclasses, pydantic models, enums and exception classes
  are the tested contract (`type(x) is`, `dataclasses.replace`, `__slots__`), so twins construct
  them through Python. A full Rust object model would need the tests themselves to move.
- A short-lived process (one hook invocation) is dominated by interpreter start-up and module
  import, which a Python extension cannot remove: it costs the same with or without the
  accelerator (the accelerator itself loads in about 1 ms). A native hook entry point in `yoetz-rs`
  that speaks the observation store and control protocol directly is the follow-up that would make
  hooks themselves fast.
- I/O-bound orchestration (asyncio service, SQLite through apsw, cryptography, git subprocesses,
  the terminal interface) stays Python; its cost is not in Python bytecode.
