"""Runtime discovery must not treat discovery order as connection authority."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from builders.tui_runtime import CLI, DESKTOP
from yoetz.tui.models import HarnessOption
from yoetz.tui.runtime import YoetzRuntime, _WorkSession  # pyright: ignore[reportPrivateUsage]

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_detect_reports_connected_when_the_second_installation_is_owned(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = YoetzRuntime(cwd=tmp_path)

    def harnesses(_self: YoetzRuntime) -> tuple[HarnessOption, ...]:
        return (DESKTOP, CLI)

    monkeypatch.setattr(
        YoetzRuntime,
        "discover_harnesses",
        harnesses,
    )

    async def state(_self: YoetzRuntime, option: HarnessOption) -> str:
        return "yoetz_owned" if option is CLI else "absent"

    monkeypatch.setattr(YoetzRuntime, "mcp_state", state)

    detection = await runtime.detect()

    assert detection.already_connected is True


async def test_service_client_uses_ui_kind_and_runtime_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import yoetz.cli.app as cli_app
    from yoetz.ports.control import ControlClientKind, WorkspaceLocator

    observed: list[tuple[ControlClientKind, WorkspaceLocator]] = []

    class Client:
        async def close(self) -> None:
            return None

    async def build(
        client_kind: ControlClientKind,
        *,
        workspace_locator: WorkspaceLocator | None = None,
    ) -> Client:
        assert workspace_locator is not None
        observed.append((client_kind, workspace_locator))
        return Client()

    monkeypatch.setattr(cli_app, "build_service_client", build)
    runtime = YoetzRuntime(cwd=tmp_path)

    async with runtime._client():  # pyright: ignore[reportPrivateUsage]
        pass

    assert observed == [(ControlClientKind.UI, WorkspaceLocator(str(tmp_path)))]


async def test_provider_posture_reads_at_the_repository_root_not_the_launch_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PR #478 review: host admission files live at the repository root, so a subdirectory
    launch must hand the resolved root, not the launch directory, to the status report."""

    from yoetz.cli import provider_status

    (tmp_path / ".git").mkdir()
    subdirectory = tmp_path / "src" / "nested"
    subdirectory.mkdir(parents=True)
    observed: list[Path | None] = []

    async def report(*, workspace_locator: Path | None = None) -> dict[str, object]:
        observed.append(workspace_locator)
        return {}

    monkeypatch.setattr(provider_status, "provider_status_report", report)
    runtime = YoetzRuntime(cwd=subdirectory)

    await runtime.provider_posture()

    assert observed == [tmp_path]


def test_work_detail_preserves_unknown_open_obligation_count(tmp_path: Path) -> None:
    runtime = YoetzRuntime(cwd=tmp_path)
    session = _WorkSession(
        task_id="tsk_00000000-0000-4000-8000-000000000001",
        session_id="ses_00000000-0000-4000-8000-000000000002",
        writer_id="wri_00000000-0000-4000-8000-000000000003",
        frontier=SimpleNamespace(sequence="2"),
    )
    compact = SimpleNamespace(
        coverage=SimpleNamespace(known_gaps=("redacted_event",)),
        gaps=("readiness_unknown",),
        ledger_freshness="redacted_gap",
        open_obligation_count=None,
        unanswered_finding_count="0",
        receipt_blocking_finding_count="0",
    )

    detail = runtime._work_detail(  # pyright: ignore[reportPrivateUsage]
        "Unreadable plan scope", session, compact
    )

    assert detail.evidence_count is None
    assert detail.coverage == ("redacted_event",)


async def test_store_provider_credential_supplies_the_scoped_reauthentication_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Keychain-provisioned vault must not ask the TUI user for an unseen passphrase."""

    observed: list[tuple[object, object]] = []
    observed_workspaces: list[Path | None] = []

    def configuration(*_args: object) -> SimpleNamespace:
        return SimpleNamespace(
            provider=SimpleNamespace(
                provider_id="fireworks",
                model="accounts/fireworks/models/minimax-m3",
                endpoint_profile_id="fireworks-responses",
                endpoint_profile_version="1.0.0",
            )
        )

    async def snapshot(workspace_locator: Path | None = None) -> SimpleNamespace:
        observed_workspaces.append(workspace_locator)
        return SimpleNamespace(bound_scope={"workspace_ref_commitment": "hmac-sha256:" + "7" * 64})

    async def ceremony(
        _target: object,
        credential: bytearray | None = None,
        reauthentication: bytearray | None = None,
    ) -> SimpleNamespace:
        observed.append((credential, reauthentication))
        return SimpleNamespace(activation_status="stored")

    monkeypatch.setattr("yoetz.config.load.load_config", configuration)
    monkeypatch.setattr("yoetz.cli.privacy_setup.get_privacy_setup_snapshot", snapshot)
    monkeypatch.setattr("yoetz.cli.unlock.set_provider_credential", ceremony)
    monkeypatch.setattr(
        "yoetz.cli.unlock.load_auto_unlock_reauthentication",
        lambda: bytearray(b"scoped-reauth"),
    )

    status = await YoetzRuntime(cwd=tmp_path).store_provider_credential()

    assert status == "stored"
    assert observed_workspaces == [tmp_path]
    assert len(observed) == 1
    credential, reauthentication = observed[0]
    assert credential is None
    assert reauthentication is not None
    assert bytes(cast(bytearray, reauthentication)) == b"scoped-reauth"


def test_every_reviewed_provider_preset_gets_a_friendly_picker_label(tmp_path: Path) -> None:
    """A preset missing from the label map either vanishes from ``/provider`` or
    shows its raw provider id; both regress silently, so the map is locked to
    the preset registry rather than to a hand-maintained list."""

    from yoetz.config.write import PROVIDER_PRESETS

    options = {option.choice: option for option in YoetzRuntime(cwd=tmp_path).provider_options()}

    missing = sorted(set(PROVIDER_PRESETS) - set(options))
    assert missing == [], f"presets without a picker entry: {missing}"
    for choice, preset in PROVIDER_PRESETS.items():
        option = options[choice]
        assert option.provider_id == preset.provider_id
        assert option.label.strip(), f"empty label for preset {choice}"
        assert option.label != preset.provider_id, (
            f"preset {choice} falls through to its raw provider id {preset.provider_id!r}"
        )
        assert option.label != choice, f"preset {choice} falls through to its choice key"
    assert options["grok"].label == "Grok (xAI)"


# ---------------------------------------------------------------------------
# Host facts are named once in /doctor and detection (issues #720, #721, #724)
# ---------------------------------------------------------------------------


def test_secure_storage_probe_names_the_reason_from_the_vault_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yoetz.adapters.keys import os_keyring
    from yoetz.tui import runtime as runtime_module

    monkeypatch.setattr(
        os_keyring,
        "describe_vault_keyring_backend",
        lambda: os_keyring.KeyringBackendReport(
            "keyring.backends.null.Keyring", False, "backend_not_approved", "macOS Keychain"
        ),
    )

    available, reason = runtime_module._secure_storage_probe()  # pyright: ignore[reportPrivateUsage]

    assert available is False
    assert "keyring.backends.null.Keyring" in reason
    assert reason.endswith("Yoetz needs macOS Keychain")


def test_host_entries_report_platform_sandbox_and_storage_without_mutating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.version as version_module
    from yoetz.adapters import check_sandbox
    from yoetz.adapters.keys import os_keyring
    from yoetz.ports.check_sandbox import CheckSandboxAvailability, CheckSandboxStatus
    from yoetz.tui import runtime as runtime_module
    from yoetz.tui.models import LayerState

    monkeypatch.setattr(version_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(version_module.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(
        check_sandbox,
        "default_check_sandbox",
        lambda: check_sandbox.LinuxCheckSandbox(bwrap="/nonexistent/bwrap", _probe=lambda _argv: 1),
    )
    monkeypatch.setattr(
        os_keyring,
        "describe_vault_keyring_backend",
        lambda: os_keyring.KeyringBackendReport("", False, "keyring_unavailable", "a store"),
    )

    entries = runtime_module._host_entries()  # pyright: ignore[reportPrivateUsage]

    by_key = {entry.key: entry for entry in entries}
    assert list(by_key) == ["platform_cell", "check_sandbox", "secure_storage"]
    assert by_key["platform_cell"].state is LayerState.UNPROVEN
    assert "aarch64" in by_key["platform_cell"].detail
    assert "macOS arm64 and Linux x86-64" in by_key["platform_cell"].remediation
    assert by_key["check_sandbox"].state is LayerState.NOT_CONFIGURED
    assert by_key["check_sandbox"].detail.startswith("bwrap_missing")
    assert "apt install bubblewrap" in by_key["check_sandbox"].remediation
    assert by_key["secure_storage"].state is LayerState.NOT_CONFIGURED
    assert by_key["secure_storage"].detail == "no credential store is loaded; Yoetz needs a store"
    assert "passphrase" in by_key["secure_storage"].remediation

    ready = CheckSandboxAvailability(CheckSandboxStatus.READY, "seatbelt", "ready", "")
    monkeypatch.setattr(check_sandbox, "probe_check_sandbox", lambda: ready)
    monkeypatch.setattr(version_module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        os_keyring,
        "describe_vault_keyring_backend",
        lambda: os_keyring.KeyringBackendReport("x", True, "approved", "a store"),
    )

    verified = {entry.key: entry for entry in runtime_module._host_entries()}  # pyright: ignore[reportPrivateUsage]
    assert all(entry.state is LayerState.VERIFIED for entry in verified.values())
    assert verified["platform_cell"].detail == "Linux x86_64 (manylinux_2_28_x86_64)"
    assert verified["check_sandbox"].detail == "seatbelt"
    assert all(entry.remediation == "" for entry in verified.values())
