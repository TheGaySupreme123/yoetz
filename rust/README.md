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
   exact `dict`s go through their Python `.items()`.
4. **Honor monkeypatch points.** Tests replace module attributes to observe or fault calls. A twin
   must not bypass a dependency the tests patch: wrap it so it defers to the Python reference when
   the dependency is not the original (see `strict_json_parse` and `json.loads`), or keep that
   function in Python. Consumer modules keep calling `canonical_encode`/`canonical_digest` through
   their own module globals, so patches on those modules still intercept.
5. **No native stack exhaustion.** Recursion over caller-controlled structures is bounded or
   iterative; hostile nesting must produce the reference's refusal, never a crash.
6. **No user content in errors.** Reason codes only, as in Python.

## Verification

```text
cargo test --manifest-path rust/Cargo.toml
YZ_NATIVE=require uv run pytest <touched test paths>
YZ_NATIVE=0 uv run pytest <touched test paths>
```
