# Upgrade Yoetz

Ask your agent: **“Update Yoetz to the newest version and keep my current settings.”**
The agent can use the upgrade guide:

```text
yoetz upgrade
```

This command shows the package, data, host refresh, and verification stages without changing
anything. Select only your existing hosts with repeated `--host codex`, `--host claude`, or
`--host cursor`. Supply the existing target values shown by your host registration when the guide
requests them. It never guesses a home, installs another host, or chooses a new privacy recipe.

For an ordinary installation managed by `uv tool`, first stop the old hosts/hooks and the Yoetz
service through their supported lifecycle. Once those writers are stopped, the package step is:

```text
yoetz upgrade --accept --writers-stopped
```

This runs `uv tool upgrade yoetz`. It refuses source checkouts, pinned test instances, and isolated
runtimes so it cannot accidentally replace another installation. For a different package manager,
use that manager's upgrade procedure and then return to the guide. A failed or timed-out package
command is reported without claiming success; inspect the installed version before retrying.

Start the fresh launcher and run `yoetz upgrade` again, with the same host target options. Do not
repeat `--accept` just to continue. Package replacement does not itself refresh host files. When
the package and the existing data are a supported pair, the first controlled service startup
performs the backup-first data upgrade before the service becomes ready. It preserves existing
tasks, settings, permissions, host integrations, observation consent, and recorded history; there
is no per-task migration ceremony:

- **Codex:** refresh the existing skill and inspect the exact plugin activation and MCP target.
  Apply only the fresh preview supplied by those surfaces, preserving its route and home.
- **Claude Code:** use the native plugin update preview and its authorization procedure, apply
  with the same request and digest, then reload or start a fresh session.
- **Cursor:** use the native replacement preview and install procedure, then fully relaunch when
  runtime status requires it. Portable/development carriers use their original install procedure.
- **Existing data:** the service handles a compatible 0.2-to-0.3 bundle upgrade during startup and
  verifies the result before accepting new work. A recoverable interruption resumes the recorded
  operation at the next unlocked startup. A newer schema, an unrecognized older layout, or a
  failed integrity check stays unavailable and points to the supported recovery procedure.
  Do not edit the database or retry with a new migration identity.

The agent should verify the package version, service identity, selected host artifact/runtime
status, and the completed data-upgrade result before saying the upgrade is complete. Host trust
prompts and any explicit backup, restore, or manual migration remain separate reviewed actions. The
guide automates the package step and compatible startup migration; host refresh and activation are
still separate actions with their own exact target and evidence.

## Release notices and new settings

When update checks are allowed, a READY service checks the cached PyPI result on startup and hourly
thereafter. The network result is cached for 24 hours. A later eligible session can tell your agent
about a discovered release; this is not an operating-system notification or an immediate alert.
Offline operation, a locked/stopped service, disabled checks, or higher-priority task advice can
delay the notice. Hooks only read the cache and never perform a network check.

Use the exact release-specific accept/decline command in the notice. Declining a newly advertised
release skips that release; a later release can be offered. Older permanent declines remain
respected. Turning off update checks remains the global network opt-out.

Upgrading preserves your choices. Expanded review and other new features can be discussed afterward
and enabled only with their own approval. Accepting an update notice does not authorize package
execution, new observation consent, or broader disclosure.
