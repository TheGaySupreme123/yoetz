from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

import yoetz.cli.bootstrap as bootstrap
import yoetz.cli.entry as entry
import yoetz.cli.observe_hooks as observe_hooks
import yoetz.config.paths as paths
import yoetz.service.client as client_module
from yoetz.ports.control import ServiceState, ServiceStatus


@pytest.fixture
def isolated_cli_modules() -> Iterator[None]:
    """Temporarily remove the full Typer graph without poisoning later test imports.

    The fast path must prove that ``yoetz.cli.app`` is absent while it connects.  Restoring both
    ``sys.modules`` and the package attributes matters because another test may already hold a
    function imported from the original module; leaving a newly re-imported module attached to the
    package makes string-based monkeypatching target a different module instance.
    """

    package = sys.modules["yoetz.cli"]
    module_names = ("yoetz.cli.app", "yoetz.cli.project")
    sentinel = object()
    saved_modules: dict[str, ModuleType | None] = {
        name: sys.modules.get(name) for name in module_names
    }
    saved_attributes: dict[str, object] = {
        name.rsplit(".", 1)[-1]: getattr(package, name.rsplit(".", 1)[-1], sentinel)
        for name in module_names
    }
    for name in module_names:
        sys.modules.pop(name, None)
        attribute = name.rsplit(".", 1)[-1]
        if hasattr(package, attribute):
            delattr(package, attribute)
    try:
        yield
    finally:
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        for attribute, value in saved_attributes.items():
            if value is sentinel:
                if hasattr(package, attribute):
                    delattr(package, attribute)
            else:
                setattr(package, attribute, value)


def test_observe_fast_path_propagates_handler_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def exit_seven(**_kwargs: object) -> int:
        return 7

    monkeypatch.setattr(observe_hooks, "handle_observe", exit_seven)

    assert (
        entry._observe_fast_path(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            ["--event", "PostToolUse"]
        )
        == 7
    )


def test_observe_fast_path_degrades_handler_failure_to_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**_kwargs: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(observe_hooks, "handle_observe", fail)

    assert (
        entry._observe_fast_path(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            ["--event", "PostToolUse"]
        )
        == 0
    )


def test_service_status_fast_path_falls_through_for_help_and_unknown_options() -> None:
    assert (
        entry._service_status_fast_path(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            ["--help"]
        )
        is None
    )
    assert (
        entry._service_status_fast_path(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            ["--json", "--json"]
        )
        is None
    )


def test_service_status_fast_path_connects_before_loading_full_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    isolated_cli_modules: None,
) -> None:
    del isolated_cli_modules
    closed = False
    status = ServiceStatus(
        protocol_version="1.0",
        service_version="0.1.0",
        service_instance_id="svc_00000000-0000-4000-8000-000000000001",
        service_generation="1",
        state=ServiceState.LOCKED,
        state_reason="vault_uninitialized",
        vault_mode="uninitialized",
        capabilities=("workflow",),
        session_monitor="unavailable",
    )

    class FakeClient:
        async def service_status(self) -> ServiceStatus:
            return status

        async def close(self) -> None:
            nonlocal closed
            closed = True

    async def connect(*_args: object, **_kwargs: object) -> FakeClient:
        assert "yoetz.cli.app" not in sys.modules
        return FakeClient()

    monkeypatch.setattr(client_module, "connect_service", connect)

    assert (
        entry._service_status_fast_path(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
            ["--json"]
        )
        == 0
    )
    assert closed
    assert "yoetz.cli.app" not in sys.modules
    assert "yoetz.cli.project" not in sys.modules
    payload = json.loads(capsys.readouterr().out)
    assert payload["service_version"] == "0.1.0"
    assert payload["state"] == "locked"


def test_service_status_fast_path_preserves_silent_service_guidance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    isolated_cli_modules: None,
) -> None:
    del isolated_cli_modules
    root = tmp_path

    def state_dir(**_kwargs: object) -> Path:
        return root

    monkeypatch.setattr(paths, "state_dir", state_dir)

    async def refuse(*_args: object, **_kwargs: object) -> object:
        raise client_module._AcceptedServiceUnresponsive()  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(client_module, "connect_service", refuse)

    assert (
        entry._service_status_fast_path([])  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        == 20
    )
    assert "yoetz.cli.app" not in sys.modules
    assert "yoetz.cli.project" not in sys.modules
    captured = capsys.readouterr()
    assert "did not answer within 5 seconds" in captured.err
    assert "Do not run 'yoetz service run'" in captured.err


def test_service_status_fast_path_bounds_rendering_failure_without_retry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = 0

    class FakeClient:
        async def service_status(self) -> ServiceStatus:
            return ServiceStatus(
                protocol_version="1.0",
                service_version="0.1.0",
                service_instance_id="svc_00000000-0000-4000-8000-000000000001",
                service_generation="1",
                state=ServiceState.LOCKED,
                state_reason="vault_uninitialized",
                vault_mode="uninitialized",
                capabilities=(),
                session_monitor="unavailable",
            )

        async def close(self) -> None:
            return None

    async def connect(*_args: object, **_kwargs: object) -> FakeClient:
        nonlocal calls
        calls += 1
        return FakeClient()

    def broken_pipe(*_args: object, **_kwargs: object) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(client_module, "connect_service", connect)
    monkeypatch.setattr(bootstrap, "human_or_json", broken_pipe)

    assert (
        entry._service_status_fast_path(["--json"])  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        == 70
    )
    assert calls == 1
    assert capsys.readouterr().err == "internal_error: the command could not be completed\n"
