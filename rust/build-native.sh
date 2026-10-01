#!/usr/bin/env bash
# Build the optional yoetz_native accelerator and install it into a Python environment.
#
#   rust/build-native.sh                 # release build into the checkout's .venv
#   YOETZ_NATIVE_PYTHON=/path/bin/python rust/build-native.sh
#
# The accelerator is installed outside uv.lock on purpose: the yoetz wheel stays pure Python, and
# `uv sync` (exact) removes the accelerator again, restoring the pure-Python implementations.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/.." && pwd)"
python="${YOETZ_NATIVE_PYTHON:-${repo}/.venv/bin/python}"
out="${here}/target/wheels"
rm -f "${out}"/yoetz_native-*.whl
if command -v maturin >/dev/null 2>&1; then
  maturin=(maturin)
else
  maturin=(uvx --from "maturin==1.15.0" maturin)
fi
"${maturin[@]}" build --release --manifest-path "${here}/crates/yoetz-native/Cargo.toml" \
  --interpreter "${python}" --out "${out}"
uv pip install --python "${python}" --reinstall --no-deps "${out}"/yoetz_native-*.whl
"${python}" -c "import yoetz_native, sys; sys.stdout.write(f'yoetz_native {yoetz_native.__version__} interface {yoetz_native.INTERFACE_VERSION}\n')"
