from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from yoetz.cli import app as cli
from yoetz.ports.control import ControlError
from yoetz.service.confidential_protocol import InstallationRecoveryTarget
from yoetz.service.installation_recovery import (
    InstallationRecoveryState,
    InstallationRecoveryStatus,
)


def test_recovery_cli_surface_and_target_envelopes_are_explicit() -> None:
    result = CliRunner().invoke(cli.app, ["service", "recovery", "--help"])
    assert result.exit_code == 0
    for command in ("status", "import", "export", "provision", "rotate", "revoke", "restore"):
        assert command in result.stdout

    provision = cast(
        InstallationRecoveryTarget,
        cli._installation_recovery_target(  # pyright: ignore[reportPrivateUsage]
            operation="provision",
            recovery_generation=1,
            set_mode="compact",
            secret_kind="generated_code",
        ),
    )
    rotate = cast(
        InstallationRecoveryTarget,
        cli._installation_recovery_target(  # pyright: ignore[reportPrivateUsage]
            operation="rotate",
            recovery_generation=2,
            set_mode="self_contained",
            secret_kind="argon2id_passphrase",
        ),
    )
    assert provision.target_envelope == "preserve"
    assert rotate.target_envelope == "passphrase"
    assert provision.confirmed_plan_digest == provision.plan_digest()
    assert rotate.confirmed_plan_digest == rotate.plan_digest()


@pytest.mark.anyio
async def test_recovery_status_is_pathless_and_agent_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = tmp_path / "installation-state.json"
    marker.write_text("structural", encoding="utf-8")
    canary_path = "/Users/canary/secret/recovery.yirs"

    class _Store:
        def status(self, **_kwargs: object) -> InstallationRecoveryStatus:
            return InstallationRecoveryStatus(
                InstallationRecoveryState.RECOVERY_MATERIAL_REQUIRED,
                "provisioned_recovery_available",
                4,
                ("self_contained",),
                None,
                "yoetz service recovery restore",
            )

    async def _unavailable() -> object:
        raise ControlError("service_unavailable")

    def _bundle_root(*, _data_dir: Path | None = None) -> Path:
        del _data_dir
        return tmp_path

    monkeypatch.setattr(cli, "_installation_recovery_store", lambda: _Store())
    monkeypatch.setattr(cli, "build_service_client", _unavailable)
    monkeypatch.setattr("yoetz.config.paths.bundle_root", _bundle_root)

    assert await cli._service_recovery_status(True) == 0  # pyright: ignore[reportPrivateUsage]
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "recovery_material_required"
    assert report["next_command"] == "yoetz service recovery restore"
    encoded = json.dumps(report)
    assert canary_path not in encoded
    assert "secret" not in encoded
