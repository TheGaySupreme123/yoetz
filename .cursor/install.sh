#!/usr/bin/env bash
# Idempotent cloud-agent dependency setup for the yoetz repo.
set -euo pipefail

export PATH="${HOME}/.local/bin:${PATH}"

# Prefer the repo-pinned Node over any exec-daemon shim.
export NVM_DIR="${HOME}/.nvm"
# shellcheck disable=SC1091
[ -s "${NVM_DIR}/nvm.sh" ] && . "${NVM_DIR}/nvm.sh"
nvm install 26.5.0
nvm alias default 26.5.0
nvm use 26.5.0
export PATH="${NVM_DIR}/versions/node/v26.5.0/bin:${PATH}"

# package.json engines pin npm@12.0.1
npm install -g npm@12.0.1
hash -r
npm ci

# Python toolchain: uv + locked project deps (requires-python 3.14).
if ! command -v uv >/dev/null 2>&1 || [[ "$(uv --version 2>/dev/null || true)" != "uv 0.11.29"* ]]; then
  curl -LsSf https://astral.sh/uv/0.11.29/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

uv python install 3.14
uv sync --all-groups --locked

# Quick sanity so a bad snapshot fails install instead of the agent mid-task.
uv run python --version
uv run yoetz --version
npx --no-install pyright --version
