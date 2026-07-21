#!/usr/bin/env node
"use strict";

// Delegation-only launcher: `npx yoetz ...` runs the exact pinned Python
// distribution through uv. It never bundles Python, never downloads code
// itself, and never rewrites arguments — the Python CLI owns every behavior,
// including the first-run setup wizard on a bare interactive invocation.

const { spawnSync } = require("node:child_process");
const { version } = require("../package.json");

function uvAvailable() {
  const probe = spawnSync("uv", ["--version"], { stdio: "ignore", shell: false });
  return probe.status === 0;
}

if (!uvAvailable()) {
  process.stderr.write(
    [
      "yoetz: the 'uv' tool is required and was not found on PATH.",
      "Install uv first (see https://docs.astral.sh/uv/getting-started/installation/),",
      "then re-run this command. This launcher only delegates to the Python",
      `distribution 'yoetz==${version}' and never installs anything itself.`,
      "",
    ].join("\n"),
  );
  process.exit(1);
}

const result = spawnSync(
  "uvx",
  [`yoetz==${version}`, ...process.argv.slice(2)],
  { stdio: "inherit", shell: false },
);

if (result.error) {
  process.stderr.write("yoetz: failed to launch 'uvx'. Is uv installed correctly?\n");
  process.exit(1);
}
process.exit(result.status === null ? 1 : result.status);
