from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

import yoetz.cli.app as module
import yoetz.cli.unlock as unlock_module


class _Client:
    def __init__(self, *, state: str, reason: str, vault_mode: str = "passphrase") -> None:
        self._status = SimpleNamespace(
            state=SimpleNamespace(value=state),
            state_reason=reason,
            vault_mode=vault_mode,
        )
        self.closed = False

    async def service_status(self) -> Any:
        return self._status

    async def close(self) -> None:
        self.closed = True


class _Store:
    def __init__(self, secret: bytearray | None, reason: str) -> None:
        self.secret = secret
        self.reason = reason
        self.saved: bytes | None = None

    def load_with_reason(self) -> tuple[bytearray | None, str]:
        return self.secret, self.reason

    def save(self, value: bytearray) -> None:
        self.saved = bytes(value)


@pytest.mark.anyio
async def test_auto_unlock_status_reports_service_verified_stale_without_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = bytearray(b"a" * 48)
    store = _Store(secret, "none")
    client = _Client(state="locked", reason="auto_unlock_stale")
    monkeypatch.setattr(module, "_auto_unlock_store", lambda: store)
    monkeypatch.setattr(module, "build_service_client", lambda: _async_value(client))

    assert (
        await module._service_auto_unlock_status(  # pyright: ignore[reportPrivateUsage]
            True
        )
        == 20
    )

    report = json.loads(capsys.readouterr().out)
    assert report == {
        "next_command": "yoetz service auto-unlock repair",
        "schema": "yoetz.auto-unlock-status/1",
        "service_state": "locked",
        "service_state_reason": "auto_unlock_stale",
        "state": "stale",
    }
    assert secret == bytearray(len(secret))
    assert client.closed


@pytest.mark.anyio
async def test_auto_unlock_repair_proves_passphrase_before_saving_and_wipes_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    passphrase = bytearray("correct horse 🔐 battery staple".encode())
    original = bytes(passphrase)
    store = _Store(None, "auto_unlock_absent")
    client = _Client(state="locked", reason="passphrase_required")
    supplied: list[bytes] = []

    async def unlock(value: bytearray | None = None) -> Any:
        supplied.append(bytes(value or b""))
        return SimpleNamespace(state="ready")

    monkeypatch.setattr(module, "_auto_unlock_store", lambda: store)
    monkeypatch.setattr(module, "build_service_client", lambda: _async_value(client))
    monkeypatch.setattr(unlock_module, "read_vault_passphrase_for_auto_unlock", lambda: passphrase)
    monkeypatch.setattr(unlock_module, "unlock_vault", unlock)

    assert (
        await module._service_auto_unlock_repair(  # pyright: ignore[reportPrivateUsage]
            True
        )
        == 0
    )

    report = json.loads(capsys.readouterr().out)
    assert report["outcome"] == "repaired"
    assert supplied == [original]
    assert store.saved == original
    assert passphrase == bytearray(len(passphrase))
    assert client.closed


@pytest.mark.anyio
async def test_auto_unlock_repair_maps_missing_trusted_tty_to_usage_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _Client(state="locked", reason="passphrase_required")

    def no_tty() -> bytearray:
        raise unlock_module.HumanCeremonyCliError("tty_required")

    monkeypatch.setattr(module, "build_service_client", lambda: _async_value(client))
    monkeypatch.setattr(unlock_module, "read_vault_passphrase_for_auto_unlock", no_tty)

    assert (
        await module._service_auto_unlock_repair(  # pyright: ignore[reportPrivateUsage]
            True
        )
        == 2
    )
    assert capsys.readouterr().err == "invalid_request: the command input is invalid\n"


async def _async_value(value: Any) -> Any:
    return value
