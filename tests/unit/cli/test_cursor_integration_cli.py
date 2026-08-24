from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from yoetz.adapters.integrations.portable_plugin import prepare_portable_artifact_review
from yoetz.cli.app import app
from yoetz.cli.cursor_integration import run_cursor_plugin_command
from yoetz.ports.plugin_artifacts import ArtifactAuthority
from yoetz.protocol.canonical import JsonValue, canonical_encode


class _Presence:
    """Stand in for the action-bound presence cell the packaged runtime does not ship."""

    def __init__(self) -> None:
        self.seen: list[ArtifactAuthority] = []

    def verify_artifact_review(self, authority: ArtifactAuthority) -> None:
        self.seen.append(authority)


def _mutate(
    command: str,
    config: Path,
    project: Path,
    *,
    request_value: str,
    preview_digest: str,
    state: Path | None,
    presence: _Presence | None,
    requested_action: str | None = None,
) -> int:
    return run_cursor_plugin_command(
        command,
        harness="cursor",
        cursor_config_root=config,
        project_root=project,
        format_name="native",
        ownership_name="plugin-managed",
        route_profile="strict",
        requested_action=requested_action,
        request_value=request_value,
        preview_digest=preview_digest,
        accept=True,
        json_output=True,
        _state=state,
        _presence=presence,
    )


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

    state = tmp_path / "private-state"
    presence = _Presence()
    prepare_portable_artifact_review(preview["preview_digest"], _state=state)
    assert (
        _mutate(
            "install",
            config,
            project,
            request_value=preview["request_id"],
            preview_digest=preview["preview_digest"],
            state=state,
            presence=presence,
        )
        == 0
    )
    assert len(presence.seen) == 1

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
    prepare_portable_artifact_review(remove_preview["preview_digest"], _state=state)
    assert (
        _mutate(
            "remove",
            config,
            project,
            request_value=remove_preview["request_id"],
            preview_digest=remove_preview["preview_digest"],
            state=state,
            presence=presence,
        )
        == 0
    )
    assert len(presence.seen) == 2
    assert not (config / "plugins" / "local" / "yoetz").exists()
    assert sentinel.read_text("utf-8") == "untouched\n"

    # Each review is single-shot: the pending is gone and a replay cannot reuse it.
    assert (
        _mutate(
            "install",
            config,
            project,
            request_value=preview["request_id"],
            preview_digest=preview["preview_digest"],
            state=state,
            presence=presence,
            requested_action="install",
        )
        == 1
    )
    assert len(presence.seen) == 2


def test_cursor_plugin_cli_rejects_unknown_action_with_bounded_reason(tmp_path: Path) -> None:
    runner = CliRunner()
    config = tmp_path / "cursor-testing-home" / ".cursor"
    project = tmp_path / "project"
    project.mkdir()

    result = runner.invoke(app, _args(config, project, "preview", "--action", "bogus"))

    assert result.exit_code == 1
    assert result.stderr == "cursor_plugin_action_invalid\n"


def test_accept_without_a_prepared_review_cannot_mutate_through_the_typer_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--accept`` is operator acceptance of an exact digest, never human authority."""

    monkeypatch.setattr(
        "yoetz.service.elevated_bootstrap.state_dir", lambda: tmp_path / "private-state"
    )
    runner = CliRunner()
    config = tmp_path / "cursor-testing-home" / ".cursor"
    project = tmp_path / "project"
    project.mkdir()

    preview_result = runner.invoke(app, _args(config, project, "preview"))
    assert preview_result.exit_code == 0, preview_result.output
    preview = json.loads(preview_result.stdout)

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

    assert installed.exit_code == 1
    assert installed.stderr == "authority_required\n"
    assert not (config / "plugins" / "local" / "yoetz").exists()


def test_prepared_review_without_a_presence_cell_fails_closed_before_any_mutation(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cursor-testing-home" / ".cursor"
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "private-state"
    runner = CliRunner()

    preview = json.loads(runner.invoke(app, _args(config, project, "preview")).stdout)
    prepare_portable_artifact_review(preview["preview_digest"], _state=state)

    # No presence override: this is the packaged wiring, which advertises no verified
    # action-bound UserPresencePort (ADR-016 decision 6, ADR-023 decision 11).
    assert (
        _mutate(
            "install",
            config,
            project,
            request_value=preview["request_id"],
            preview_digest=preview["preview_digest"],
            state=state,
            presence=None,
        )
        == 1
    )
    assert not (config / "plugins" / "local" / "yoetz").exists()


def test_wedged_install_replay_reconciles_through_the_cli_without_a_second_review(
    tmp_path: Path,
) -> None:
    config = tmp_path / "cursor-testing-home" / ".cursor"
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "private-state"
    runner = CliRunner()

    preview = json.loads(runner.invoke(app, _args(config, project, "preview")).stdout)
    prepare_portable_artifact_review(preview["preview_digest"], _state=state)
    assert (
        _mutate(
            "install",
            config,
            project,
            request_value=preview["request_id"],
            preview_digest=preview["preview_digest"],
            state=state,
            presence=_Presence(),
        )
        == 0
    )

    # The single-shot review is spent and the result was lost. Replaying the same request must
    # reconcile at the committed state rather than demand a second review.
    assert (
        _mutate(
            "install",
            config,
            project,
            request_value=preview["request_id"],
            preview_digest=preview["preview_digest"],
            state=state,
            presence=None,
            requested_action="install",
        )
        == 0
    )
