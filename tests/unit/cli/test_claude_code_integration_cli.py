from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from yoetz.cli.app import app


def _fake_claude(tmp_path: Path) -> Path:
    executable = tmp_path / "claude-testing-bin"
    executable.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then printf "2.1.241 (Claude Code)\\n"; exit 0; fi\n'
        'if [ "$1" = "plugin" ] && [ "$2" = "list" ]; then printf "[]\\n"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


def _args(tmp_path: Path, command: str, *extra: str) -> list[str]:
    project = tmp_path / "project"
    config = tmp_path / "claude-testing"
    project.mkdir(exist_ok=True)
    config.mkdir(exist_ok=True)
    executable = _fake_claude(tmp_path)
    return [
        "integrate",
        "claude",
        "plugin",
        command,
        "--claude-path",
        str(executable),
        "--claude-config-root",
        str(config),
        "--cache-root",
        str(config / "plugins" / "cache"),
        "--marketplace-root",
        str(tmp_path / "marketplace"),
        "--project-root",
        str(project),
        "--mcp-ownership",
        "plugin-managed",
        "--route-profile",
        "strict",
        "--json",
        *extra,
    ]


def test_claude_cli_status_and_preview_are_path_free_and_require_exact_roots(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    status = runner.invoke(app, _args(tmp_path, "status"))
    assert status.exit_code == 0, status.output
    body = json.loads(status.stdout)
    assert body["state"] == "absent"
    assert body["scope"] == "project"
    assert body["loaded_root_digest"] is None
    assert str(tmp_path) not in status.stdout

    preview = runner.invoke(
        app,
        _args(
            tmp_path,
            "preview",
            "--action",
            "install",
            "--request-id",
            "req_10000000-0000-4000-8000-000000000040",
        ),
    )
    assert preview.exit_code == 0, preview.output
    value = json.loads(preview.stdout)
    assert value["host"]["version"] == "2.1.241"
    assert value["mcp_ownership_state"] == "absent"
    assert value["authorization"]["operation"] == "plugin_artifact_apply"
    assert str(tmp_path) not in preview.stdout

    missing = runner.invoke(app, ["integrate", "claude", "plugin", "status"])
    assert missing.exit_code == 2
    assert "required for claude" in missing.output


def test_claude_cli_rejects_portable_format_and_unknown_preview_action(tmp_path: Path) -> None:
    runner = CliRunner()
    portable = runner.invoke(app, _args(tmp_path, "status", "--format", "portable"))
    assert portable.exit_code == 2
    assert portable.stderr == "claude_code_plugin_command_invalid\n"

    unknown = runner.invoke(app, _args(tmp_path, "preview", "--action", "replace"))
    assert unknown.exit_code == 1
    assert unknown.stderr == "claude_code_plugin_action_invalid\n"


def test_claude_cli_export_writes_a_plugin_dir_root_without_host_state(tmp_path: Path) -> None:
    output = tmp_path / "dev-plugin"
    result = CliRunner().invoke(
        app,
        [
            "integrate",
            "claude",
            "plugin",
            "export",
            "--output-root",
            str(output),
            "--mcp-ownership",
            "plugin-managed",
            "--route-profile",
            "strict",
            "--development-enabled",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["development"] is True
    assert body["default_enabled"] is True
    assert body["proof"] == "development_export_not_marketplace_activation"
    assert body["next_step"].startswith("claude --plugin-dir ")
    assert (output / ".claude-plugin" / "plugin.json").is_file()
    assert (output / ".mcp.json").is_file()
    assert (output / ".yoetz-claude-plugin-export.json").is_file()
    manifest = json.loads((output / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["defaultEnabled"] is True
    mcp = json.loads((output / ".mcp.json").read_text())["mcpServers"]["yoetz"]
    assert Path(mcp["command"]).is_absolute()
    assert body["yoetz_launcher"][0] == mcp["command"]
    # No Claude config, marketplace, or cache root was ever named, so none can have changed.
    assert sorted(item.name for item in tmp_path.iterdir()) == ["dev-plugin"]

    again = CliRunner().invoke(
        app,
        ["integrate", "claude", "plugin", "export", "--output-root", str(output), "--json"],
    )
    assert again.exit_code == 1
    assert "destination_conflict" in again.output

    missing = CliRunner().invoke(app, ["integrate", "claude", "plugin", "export", "--json"])
    assert missing.exit_code == 2


def test_a_strict_re_render_preview_discloses_the_host_admission_it_would_revoke(
    tmp_path: Path,
) -> None:
    """A strict route is a reverse transition for host admission (issue #467)."""

    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["mcp__plugin_yoetz_yoetz__check"]}}),
        encoding="utf-8",
    )
    runner = CliRunner()
    preview = runner.invoke(
        app,
        _args(
            tmp_path,
            "preview",
            "--action",
            "install",
            "--request-id",
            "req_10000000-0000-4000-8000-000000000041",
        ),
    )
    assert preview.exit_code == 0, preview.output
    value = json.loads(preview.stdout)
    assert value["mcp_route_profile"] == "strict"
    assert value["admission_cleanup"] == {
        "host": "claude",
        "state": "present",
        "surfaces": [".claude/settings.local.json"],
    }
    # Disclosure is read-only: the preview itself removes nothing.
    assert (project / ".claude" / "settings.local.json").exists()

    policy = runner.invoke(
        app,
        [
            *[
                argument if argument != "strict" else "policy"
                for argument in _args(tmp_path, "preview", "--action", "install")
            ]
        ],
    )
    assert policy.exit_code == 0, policy.output
    assert json.loads(policy.stdout)["admission_cleanup"] is None
