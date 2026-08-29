"""`service status` must not prescribe a start that is guaranteed to refuse.

"Nothing is listening" and "something is listening and silent" arrive on the same wire reason but
need opposite next steps. Sending an operator from an unresponsive-but-live daemon to
`yoetz service run` produced a pair of commands that each pointed at the other.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

import yoetz.cli.app as cli
import yoetz.config.paths as paths
import yoetz.service.client as client_module
from yoetz.ports.control import ControlError
from yoetz.protocol.errors import PublicErrorCode
from yoetz.service.client import ServiceClient


def _status_stderr(monkeypatch: pytest.MonkeyPatch, error: ControlError) -> tuple[int, str]:
    async def refuse(*_args: object, **_kwargs: object) -> ServiceClient:
        raise error

    monkeypatch.setattr(cli, "build_service_client", refuse)
    result = CliRunner().invoke(cli.app, ["service", "status"])
    return result.exit_code, result.stderr


@pytest.fixture(autouse=True)
def no_singleton_stamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def state_dir(**_kwargs: object) -> Path:
        return tmp_path

    monkeypatch.setattr(paths, "state_dir", state_dir)


def test_unresponsive_daemon_and_absent_daemon_get_different_next_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    silent_code, silent = _status_stderr(monkeypatch, client_module._AcceptedServiceUnresponsive())
    absent_code, absent = _status_stderr(
        monkeypatch, ControlError("service_unavailable", retryable=True)
    )

    assert silent_code == absent_code == 20
    assert silent.startswith("service_unavailable:")
    assert "did not answer" in silent
    assert "yoetz service stop" in silent
    # The one command the old guidance prescribed here is the one that must refuse.
    assert "Do not run 'yoetz service run'" in silent
    assert "under your selected user supervisor" not in silent

    assert absent.startswith("service_unavailable:")
    assert "run 'yoetz service run' under your selected user supervisor" in absent
    assert "did not answer" not in absent


def test_unresponsive_guidance_names_the_holder_pid_when_the_stamp_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from yoetz.protocol.canonical import canonical_encode

    lock = tmp_path / "service.lock"
    lock.write_bytes(canonical_encode({"instance_id": "svc", "pid": os.getpid()}) + b"\n")
    # The daemon creates this owner-only; the probe refuses anything wider.
    lock.chmod(0o600)

    _code, guidance = _status_stderr(monkeypatch, client_module._AcceptedServiceUnresponsive())

    assert f"holder pid {os.getpid()}" in guidance


def test_other_control_failures_keep_their_existing_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code, guidance = _status_stderr(monkeypatch, ControlError("vault_locked"))

    assert code == 20
    assert guidance.startswith(PublicErrorCode.VAULT_LOCKED.value.lower())
    assert "yoetz service unlock" in guidance


def test_incompatible_service_status_json_names_reason_holder_and_correlation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse

    lock = tmp_path / "service.lock"
    lock.write_bytes(
        canonical_encode(
            {
                "instance_id": "svc_00000000-0000-4000-8000-000000000001",
                "pid": os.getpid(),
                "service_version": "0.1.0",
                "schema_manifest_digest": "sha256:" + "a" * 64,
            }
        )
        + b"\n"
    )
    lock.chmod(0o600)

    async def refuse(*_args: object, **_kwargs: object) -> ServiceClient:
        raise ControlError("service_incompatible", retryable=True)

    monkeypatch.setattr(cli, "build_service_client", refuse)

    def record_correlation(**_kwargs: object) -> str:
        return "err_00000000-0000-4000-8000-000000000099"

    monkeypatch.setattr(
        "yoetz.observability.logging.record_public_error_without_raising",
        record_correlation,
    )
    result = CliRunner().invoke(cli.app, ["service", "status", "--json"])
    assert result.exit_code == 20
    assert "service_incompatible" in result.stderr
    assert "correlation_id err_" in result.stderr
    payload = cast(dict[str, JsonValue], strict_json_parse(result.stdout.encode("utf-8")))
    assert payload["ok"] is False
    assert payload["reason"] == "service_incompatible"
    assert payload["public_code"] == PublicErrorCode.SERVICE_UNAVAILABLE.value
    assert payload["correlation_id"] == "err_00000000-0000-4000-8000-000000000099"
    holder = cast(dict[str, JsonValue], payload["holder"])
    assert holder["pid"] == os.getpid()
    assert holder["schema_manifest_digest"] == "sha256:" + "a" * 64
