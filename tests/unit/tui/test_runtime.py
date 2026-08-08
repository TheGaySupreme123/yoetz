"""Runtime discovery must not treat discovery order as connection authority."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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
        unresolved_finding_count="0",
    )

    detail = runtime._work_detail(  # pyright: ignore[reportPrivateUsage]
        "Unreadable plan scope", session, compact
    )

    assert detail.evidence_count is None
    assert detail.coverage == ("redacted_event",)
