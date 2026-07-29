"""Runtime discovery must not treat discovery order as connection authority."""

from __future__ import annotations

from pathlib import Path

import pytest

from builders.tui_runtime import CLI, DESKTOP
from yoetz.tui.models import HarnessOption
from yoetz.tui.runtime import YoetzRuntime

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
