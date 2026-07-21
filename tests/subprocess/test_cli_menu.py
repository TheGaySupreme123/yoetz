"""Interactive menu: TTY gating, bare-invocation dispatch, and navigation."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import yoetz.cli.app as cli
import yoetz.cli.menu as menu
from yoetz.ports.control import ControlError
from yoetz.ports.harness_mcp import HarnessBinary
from yoetz.ports.integrations import HarnessId

_RUNNER = CliRunner()


@pytest.fixture
def menu_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Fake TTY probes, discovery, marker path, and an unreachable service client."""

    import yoetz.adapters.integrations.codex_discovery as discovery_module
    import yoetz.cli.setup as setup_module

    marker = tmp_path / "setup-wizard.json"
    marker.write_text('{"outcome":"registered","schema":"yoetz.setup-wizard-marker/1"}')

    def fake_discover(*, _probe: object = None) -> tuple[HarnessBinary, ...]:
        return (
            HarnessBinary(
                harness_id=HarnessId.CODEX,
                executable_path="/opt/harness/bin/codex",
                reported_version="0.144.5",
                compatibility="untested",
            ),
        )

    async def unreachable_client() -> object:
        raise ControlError("service_unavailable")

    monkeypatch.setattr(menu, "menu_available", lambda: True)
    monkeypatch.setattr(discovery_module, "discover_codex_binaries", fake_discover)
    monkeypatch.setattr(setup_module, "setup_marker_path", lambda: marker)
    monkeypatch.setattr(cli, "build_service_client", unreachable_client)
    return marker


def test_menu_without_tty_is_usage_failure() -> None:
    result = _RUNNER.invoke(cli.app, ["menu"])
    assert result.exit_code == 2
    assert "invalid_request" in result.output
    assert "Select" not in result.output


def test_menu_home_renders_overview_and_quits(menu_env: Path) -> None:
    result = _RUNNER.invoke(cli.app, ["menu"], input="q\n")
    assert result.exit_code == 0
    assert "yoetz service run" in result.output
    assert "codex (0.144.5)" in result.output
    assert "First-run  complete" in result.output
    for section in ("Setup wizard", "Harness connection", "LLM provider", "Privacy", "Service"):
        assert section in result.output


def test_menu_sections_navigate_back_to_home(menu_env: Path) -> None:
    result = _RUNNER.invoke(cli.app, ["menu"], input="4\nb\n5\nb\n6\nb\nq\n")
    assert result.exit_code == 0
    assert "Rotate a provider credential" in result.output
    assert "Show effective policy" in result.output
    assert "Unlock vault" in result.output
    # Returning from each section re-renders the home screen before quitting.
    assert result.output.count("1  Refresh status") >= 4


def test_bare_tty_invocation_with_marker_opens_menu(
    menu_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yoetz.cli.setup as setup_module

    monkeypatch.setattr(setup_module, "should_offer_first_run", lambda: False)
    result = _RUNNER.invoke(cli.app, [], input="q\n")
    assert result.exit_code == 0
    assert "Refresh status" in result.output


def test_bare_invocation_without_tty_still_prints_help(
    menu_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(menu, "menu_available", lambda: False)
    result = _RUNNER.invoke(cli.app, [])
    assert result.exit_code == 0
    assert "Usage" in result.output
    assert "Refresh status" not in result.output
