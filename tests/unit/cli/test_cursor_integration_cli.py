from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from typer.testing import CliRunner

from yoetz.cli.app import app
from yoetz.protocol.canonical import JsonValue, canonical_encode


def _args(config: Path, project: Path, command: str, *extra: str) -> list[str]:
    return [
        "integrate",
        "cursor",
        "plugin",
        command,
        "--cursor-config-root",
        str(config),
        "--project-root",
        str(project),
        "--format",
        "native",
        "--mcp-ownership",
        "plugin-managed",
        "--route-profile",
        "strict",
        "--json",
        *extra,
    ]


def test_cursor_plugin_cli_binds_preview_install_status_and_remove(tmp_path: Path) -> None:
    runner = CliRunner()
    config = tmp_path / "cursor-testing-home" / ".cursor"
    project = tmp_path / "project"
    project.mkdir()
    regular = tmp_path / "regular-cursor"
    regular.mkdir()
    sentinel = regular / "sentinel"
    sentinel.write_text("untouched\n", encoding="utf-8")

    preview_result = runner.invoke(app, _args(config, project, "preview"))
    assert preview_result.exit_code == 0, preview_result.output
    preview = json.loads(preview_result.stdout)
    assert (
        preview_result.stdout.encode("utf-8") == canonical_encode(cast(JsonValue, preview)) + b"\n"
    )
    assert preview["state_before"] == "absent"

    installed = runner.invoke(
        app,
        [
            *_args(config, project, "install"),
            "--request-id",
            preview["request_id"],
            "--preview-digest",
            preview["preview_digest"],
            "--accept",
        ],
    )
    assert installed.exit_code == 0, installed.output
    assert json.loads(installed.stdout)["state_after"] == "native_managed"

    status = runner.invoke(app, _args(config, project, "status"))
    assert status.exit_code == 0, status.output
    status_body = json.loads(status.stdout)
    assert status_body["state"] == "native_managed"
    assert status_body["mcp"]["ownership_state"] == "plugin"

    remove_preview_result = runner.invoke(
        app,
        _args(
            config,
            project,
            "preview",
            "--action",
            "remove",
            "--request-id",
            "req_10000000-0000-4000-8000-000000000010",
        ),
    )
    assert remove_preview_result.exit_code == 0
    remove_preview = json.loads(remove_preview_result.stdout)
    removed = runner.invoke(
        app,
        _args(
            config,
            project,
            "remove",
            "--request-id",
            remove_preview["request_id"],
            "--preview-digest",
            remove_preview["preview_digest"],
            "--accept",
        ),
    )
    assert removed.exit_code == 0, removed.output
    assert json.loads(removed.stdout)["state_after"] == "absent"
    assert sentinel.read_text("utf-8") == "untouched\n"


def test_cursor_plugin_cli_rejects_unknown_action_with_bounded_reason(tmp_path: Path) -> None:
    runner = CliRunner()
    config = tmp_path / "cursor-testing-home" / ".cursor"
    project = tmp_path / "project"
    project.mkdir()

    result = runner.invoke(app, _args(config, project, "preview", "--action", "bogus"))

    assert result.exit_code == 1
    assert result.stderr == "cursor_plugin_action_invalid\n"
