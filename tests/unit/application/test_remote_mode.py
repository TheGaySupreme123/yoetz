"""Local remote-mode record never opens a network connection (ADR-033)."""

from __future__ import annotations

import socket
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

import yoetz.application.remote_mode as remote_mode
from yoetz.application.remote_mode import (
    REMOTE_MODE_SCHEMA,
    RemoteModeError,
    configure_remote,
    connect_remote,
    disconnect_remote,
    remote_status,
)
from yoetz.cli.app import app

_RUNNER = CliRunner()


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "remote-mode"
    root.mkdir(mode=0o700, exist_ok=True)
    return root


def test_status_without_a_record_stays_local(tmp_path: Path) -> None:
    status = remote_status(_root(tmp_path))
    assert status["service"] == "local"
    assert status["forwarding"] is False
    assert status["endpoint"] is None
    assert status["schema"] == REMOTE_MODE_SCHEMA


def test_configure_stores_the_endpoint_and_not_a_secret(tmp_path: Path) -> None:
    root = _root(tmp_path)
    status = configure_remote(root, transport="https", endpoint="https://reviews.example/")
    assert status["endpoint"] == "https://reviews.example"
    document = (root / "remote-mode.json").read_text(encoding="utf-8")
    assert "secret" not in document
    assert "api_key" in document
    mode = stat.S_IMODE((root / "remote-mode.json").stat().st_mode)
    assert mode == 0o600


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://reviews.example",
        "https://user:sk-secret@reviews.example",
        "https://reviews.example/ledger",
        "https://reviews.example/secret",
        "https://reviews.example?q=1",
        "file:///tmp/ledger",
    ],
)
def test_unsafe_https_endpoints_are_refused(tmp_path: Path, endpoint: str) -> None:
    root = _root(tmp_path)
    with pytest.raises(RemoteModeError) as exc_info:
        configure_remote(root, transport="https", endpoint=endpoint)
    assert exc_info.value.reason == "remote_endpoint_invalid"
    assert not (root / "remote-mode.json").exists()


def test_ssh_target_is_stored_without_a_path(tmp_path: Path) -> None:
    root = _root(tmp_path)
    status = configure_remote(root, transport="ssh", endpoint="owner@reviews.example:22")
    assert status["transport"] == "ssh"
    assert status["endpoint"] == "owner@reviews.example:22"
    with pytest.raises(RemoteModeError):
        configure_remote(root, transport="ssh", endpoint="owner@reviews.example:/home/key")


def test_oauth_is_not_accepted(tmp_path: Path) -> None:
    with pytest.raises(RemoteModeError) as exc_info:
        configure_remote(
            _root(tmp_path),
            transport="https",
            endpoint="https://reviews.example",
            credential_kind="oauth",
        )
    assert exc_info.value.reason == "remote_credential_unsupported"


def test_a_symlink_or_loose_mode_is_invalid(tmp_path: Path) -> None:
    root = _root(tmp_path)
    configure_remote(root, transport="https", endpoint="https://reviews.example")
    path = root / "remote-mode.json"
    path.chmod(0o644)
    with pytest.raises(RemoteModeError) as loose:
        remote_status(root)
    assert loose.value.reason == "remote_state_invalid"
    path.chmod(0o600)
    moved = root / "moved.json"
    path.rename(moved)
    path.symlink_to(moved)
    with pytest.raises(RemoteModeError) as linked:
        remote_status(root)
    assert linked.value.reason == "remote_state_invalid"


def test_connect_fails_closed_without_using_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path)
    configure_remote(root, transport="https", endpoint="https://reviews.example")

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    with pytest.raises(RemoteModeError) as exc_info:
        connect_remote(root)
    assert exc_info.value.reason == "remote_egress_not_authorized"
    assert remote_status(root)["endpoint"] == "https://reviews.example"
    source = Path(remote_mode.__file__).read_text(encoding="utf-8")
    assert "import socket" not in source
    assert "httpx" not in source
    assert "urllib.request" not in source


def test_disconnect_is_idempotent(tmp_path: Path) -> None:
    root = _root(tmp_path)
    configure_remote(root, transport="https", endpoint="https://reviews.example")
    assert disconnect_remote(root)["endpoint"] is None
    assert disconnect_remote(root)["service"] == "local"


def test_cli_connect_reports_the_registry_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "yoetz.application.remote_mode.default_remote_root",
        lambda: _root(tmp_path),
    )
    configured = _RUNNER.invoke(
        app,
        [
            "remote",
            "configure",
            "--transport",
            "https",
            "--endpoint",
            "https://reviews.example",
            "--json",
        ],
    )
    assert configured.exit_code == 0
    assert '"forwarding":false' in configured.stdout.replace(" ", "")
    refused = _RUNNER.invoke(app, ["remote", "connect", "--json"])
    assert refused.exit_code == 20
    assert "remote_egress_not_authorized" in refused.stdout
    assert "remote_forwarding_closed" in refused.stdout
    assert "reviews.example" not in refused.stderr
