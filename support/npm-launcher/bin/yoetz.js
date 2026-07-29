#!/usr/bin/env node
"use strict";

// Delegation-only launcher: `npx yoetz ...` runs the exact pinned Python
// distribution through uv. It never bundles Python, never downloads code
// itself, and never rewrites arguments — the Python CLI owns every behavior,
// including the first-run experience on a bare interactive invocation.
//
// Because stdio is inherited, the child sees the *real* terminal: the same
// stdin/stdout TTY checks that gate the full-screen interface, and the same
// controlling terminal the confidential credential ceremony requires. Anything
// that captured or replaced these streams here would silently downgrade both.

const { spawnSync } = require("node:child_process");
const { constants } = require("node:os");
const { version } = require("../package.json");

const INSTALL_DOCS = "https://docs.astral.sh/uv/getting-started/installation/";

function probeUv() {
  return spawnSync("uv", ["--version"], { stdio: "ignore", shell: false });
}

function fail(lines) {
  process.stderr.write(lines.concat("").join("\n"));
  process.exitCode = 1;
}

function main() {
  const uvProbe = probeUv();
  if (uvProbe.error?.code === "ENOENT") {
    // The packaging contract forbids bootstrapping a runtime from here, so the
    // only useful thing this launcher can do is say exactly what is missing and
    // exactly how to get it.
    fail([
      "yoetz: the 'uv' tool is required and was not found on PATH.",
      "",
      "Yoetz runs on Python 3.14 and this launcher only delegates to the",
      `Python distribution 'yoetz==${version}'. It never installs anything itself,`,
      "so uv has to be present first.",
      "",
      "  macOS / Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh",
      "  Windows:        powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"",
      `  Other options:  ${INSTALL_DOCS}`,
      "",
      "Then re-run this command.",
    ]);
    return;
  }
  if (uvProbe.error || uvProbe.status !== 0) {
    const reported = uvProbe.error
      ? uvProbe.error.message
      : `'uv --version' exited with status ${uvProbe.status}`;
    fail([
      "yoetz: could not run 'uv --version'.",
      "",
      `Reported: ${reported}`,
      "",
      "Check that your uv installation is executable and complete,",
      `then re-run this command. See ${INSTALL_DOCS}`,
    ]);
    return;
  }

  const result = spawnSync(
    "uvx",
    [`yoetz==${version}`, ...process.argv.slice(2)],
    { stdio: "inherit", shell: false },
  );

  if (result.error) {
    fail([
      "yoetz: found 'uv' but could not launch 'uvx'.",
      "",
      `Reported: ${result.error.message}`,
      "",
      "Check that your uv installation is complete and that 'uvx' is on PATH,",
      `then re-run this command. See ${INSTALL_DOCS}`,
    ]);
    return;
  }

  // A child killed by a signal has no exit status. Reporting the conventional
  // 128+n keeps `npx yoetz` interchangeable with the Python entry point in a
  // script that inspects exit codes — including 130 for an interrupted session.
  if (result.status === null) {
    const SIGNAL_EXIT_BASE = 128;
    const offset =
      typeof result.signal === "number"
        ? result.signal
        : constants.signals[result.signal];
    process.exitCode = offset === undefined ? 1 : SIGNAL_EXIT_BASE + offset;
    return;
  }

  process.exitCode = result.status;
}

main();
