"""CLI coverage for Cursor's explicit project-scoped MCP route."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from yoetz.cli.app import app

_RUNNER = CliRunner()
_INVOCATION = ("/smoke/bin/yoetz", "--from-test")
_LAUNCHER = ("/pinned/venv/bin/yoetz", "--entrypoint")


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    config = tmp_path / "cursor-config"
    isolated = tmp_path / "isolated-root"
    for root in (project, config, isolated):
        root.mkdir(mode=0o700)
    return project, config, isolated


def _args(
    project: Path, config: Path, action: str, *extra: str, route_profile: str | None = None
) -> list[str]:
    args = [
        "integrate",
        "cursor",
        "project-mcp",
        action,
        "--project-root",
        str(project),
        "--cursor-config-root",
        str(config),
    ]
    if route_profile is not None:
        args.extend(("--route-profile", route_profile))
    return [*args, "--json", *extra]


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    isolated: Path,
    *,
    launcher: tuple[str, ...] = _LAUNCHER,
) -> list[object]:
    import yoetz.cli.cursor_project_mcp as module

    calls: list[object] = []
    monkeypatch.setattr(module, "invoking_launcher", lambda: _INVOCATION)

    def resolve(candidate: object) -> tuple[str, ...]:
        calls.append(candidate)
        return launcher

    monkeypatch.setattr(module, "resolve_yoetz_launcher", resolve)
    monkeypatch.setattr(module, "isolated_root", lambda: isolated)
    return calls


def _json(result: object) -> dict[str, object]:
    # CliRunner's Result has stdout, but keep this helper typed around the
    # parse boundary so assertions do not depend on Typer internals.
    stdout = getattr(result, "stdout")
    assert isinstance(stdout, str)
    body: object = json.loads(stdout)
    assert isinstance(body, dict)
    return cast(dict[str, object], body)


def test_cursor_project_mcp_route_surface_exposes_all_actions_and_bound_roots() -> None:
    route = _RUNNER.invoke(
        app,
        ["integrate", "cursor", "project-mcp", "--help"],
        env={"COLUMNS": "300", "TERM": "dumb"},
    )
    assert route.exit_code == 0, route.output
    for action in ("preview", "preview-remove", "install", "status", "remove"):
        assert action in route.output

    action_help = _RUNNER.invoke(
        app,
        ["integrate", "cursor", "project-mcp", "preview", "--help"],
        env={"COLUMNS": "300", "TERM": "dumb"},
    )
    assert action_help.exit_code == 0, action_help.output
    for option in (
        "--project-root",
        "--cursor-config-root",
        "--route-profile",
        "--accept",
        "--preview-digest",
        "--json",
    ):
        assert option in action_help.output


def test_status_is_local_read_only_and_forwards_pinned_launcher_and_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, config, isolated = _roots(tmp_path)
    calls = _patch_runtime(monkeypatch, isolated)

    import yoetz.cli.app as app_module

    def no_service(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("project MCP status must not connect to the service")

    monkeypatch.setattr(app_module, "build_service_client", no_service)
    result = _RUNNER.invoke(app, _args(project, config, "status"))

    assert result.exit_code == 0, result.output
    body = _json(result)
    assert body == {
        "host_trust": "unknown",
        "ok": True,
        "route_profile": None,
        "runtime_binding": "unobserved",
        "source": "none",
        "state": "absent",
        "target_identity": body["target_identity"],
    }
    assert calls == [_INVOCATION]
    assert not (project / ".cursor").exists()


def test_preview_and_mutation_require_exact_preview_and_bind_launcher_route_and_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, config, isolated = _roots(tmp_path)
    calls = _patch_runtime(monkeypatch, isolated)

    preview_result = _RUNNER.invoke(app, _args(project, config, "preview", route_profile="strict"))
    assert preview_result.exit_code == 0, preview_result.output
    preview = _json(preview_result)
    assert preview["action"] == "register"
    assert preview["operation"] == "install"
    assert preview["state_before"] == "absent"
    assert preview["source"] == "none"
    assert preview["route_profile"] == "strict"
    assert preview["launcher"] == list(_LAUNCHER)
    assert preview["isolated_root"] == str(isolated)
    assert not (project / ".cursor" / "mcp.json").exists()

    missing = _RUNNER.invoke(
        app,
        _args(project, config, "install", "--accept", route_profile="strict"),
    )
    assert missing.exit_code == 2, missing.output
    assert _json(missing) == {
        "ok": False,
        "reason_code": "cursor_project_mcp_preview_required",
    }
    assert not (project / ".cursor" / "mcp.json").exists()

    stale = _RUNNER.invoke(
        app,
        _args(
            project,
            config,
            "install",
            "--accept",
            "--preview-digest",
            "sha256:" + "f" * 64,
            route_profile="strict",
        ),
    )
    assert stale.exit_code == 1, stale.output
    assert _json(stale) == {
        "ok": False,
        "reason_code": "cursor_project_mcp_preview_stale",
    }
    assert not (project / ".cursor" / "mcp.json").exists()

    installed = _RUNNER.invoke(
        app,
        _args(
            project,
            config,
            "install",
            "--accept",
            "--preview-digest",
            str(preview["preview_digest"]),
            route_profile="strict",
        ),
    )
    assert installed.exit_code == 0, installed.output
    result = _json(installed)
    assert result["action"] == "register"
    assert result["state_after"] == "yoetz_owned"
    entry = json.loads((project / ".cursor" / "mcp.json").read_text(encoding="utf-8"))[
        "mcpServers"
    ]["yoetz"]
    assert entry == {
        "args": [
            "--entrypoint",
            "mcp",
            "serve",
            "--host",
            "cursor",
            "--semantic",
            "off",
        ],
        "command": _LAUNCHER[0],
        "env": {"YOETZ_ISOLATED_ROOT": str(isolated)},
        "type": "stdio",
    }
    assert calls == [_INVOCATION, _INVOCATION, _INVOCATION, _INVOCATION]


def test_wrong_harness_is_closed_and_does_not_resolve_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, config, isolated = _roots(tmp_path)
    calls = _patch_runtime(monkeypatch, isolated)
    result = _RUNNER.invoke(
        app,
        [
            "integrate",
            "codex",
            "project-mcp",
            "status",
            "--project-root",
            str(project),
            "--cursor-config-root",
            str(config),
            "--json",
        ],
    )

    assert result.exit_code == 2, result.output
    assert _json(result) == {
        "ok": False,
        "reason_code": "cursor_project_mcp_command_invalid",
    }
    assert calls == []


def test_remove_strict_sweeps_admission_and_second_remove_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, config, isolated = _roots(tmp_path)
    _patch_runtime(monkeypatch, isolated)

    preview = _json(_RUNNER.invoke(app, _args(project, config, "preview", route_profile="strict")))
    installed = _RUNNER.invoke(
        app,
        _args(
            project,
            config,
            "install",
            "--accept",
            "--preview-digest",
            str(preview["preview_digest"]),
            route_profile="strict",
        ),
    )
    assert installed.exit_code == 0, installed.output

    # A strict install performs its reverse admission sweep. Add the exact
    # entries afterwards to exercise the corresponding remove sweep.
    cursor = project / ".cursor"
    (cursor / "permissions.json").write_text(
        json.dumps({"mcpAllowlist": ["yoetz:check", "other:read"]}), encoding="utf-8"
    )
    (cursor / "cli.json").write_text(
        json.dumps(
            {"permissions": {"allow": ["Mcp(plugin-yoetz-yoetz:check)", "Mcp(other:read)"]}}
        ),
        encoding="utf-8",
    )

    remove_preview_result = _RUNNER.invoke(app, _args(project, config, "preview-remove"))
    assert remove_preview_result.exit_code == 0, remove_preview_result.output
    remove_preview = _json(remove_preview_result)
    assert remove_preview["action"] == "unregister"
    assert remove_preview["operation"] == "remove"
    assert remove_preview["route_profile"] == "strict"
    assert remove_preview["admission_cleanup"] == {
        "host": "cursor",
        "state": "present",
        "surfaces": [".cursor/cli.json", ".cursor/permissions.json"],
    }

    removed = _RUNNER.invoke(
        app,
        _args(
            project,
            config,
            "remove",
            "--accept",
            "--preview-digest",
            str(remove_preview["preview_digest"]),
        ),
    )
    assert removed.exit_code == 0, removed.output
    removed_body = _json(removed)
    assert removed_body["action"] == "unregister"
    assert removed_body["state_after"] == "absent"
    assert removed_body["admission_cleanup"] == {
        "host": "cursor",
        "outcome": "removed",
        "surfaces_changed": [".cursor/cli.json", ".cursor/permissions.json"],
    }
    assert json.loads((cursor / "permissions.json").read_text()) == {"mcpAllowlist": ["other:read"]}
    assert json.loads((cursor / "cli.json").read_text()) == {
        "permissions": {"allow": ["Mcp(other:read)"]}
    }

    noop_preview_result = _RUNNER.invoke(app, _args(project, config, "preview-remove"))
    assert noop_preview_result.exit_code == 0, noop_preview_result.output
    noop_preview = _json(noop_preview_result)
    assert noop_preview["action"] == "noop"
    assert noop_preview["state_before"] == "absent"

    noop = _RUNNER.invoke(
        app,
        _args(
            project,
            config,
            "remove",
            "--accept",
            "--preview-digest",
            str(noop_preview["preview_digest"]),
        ),
    )
    assert noop.exit_code == 0, noop.output
    assert _json(noop)["action"] == "noop"
    assert not json.loads((cursor / "mcp.json").read_text())["mcpServers"].get("yoetz")
