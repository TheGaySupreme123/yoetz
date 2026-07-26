"""The first-run flow, driven by real keystrokes through the Textual pilot."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from builders.tui_runtime import CLI, DESKTOP, FakeRuntime
from yoetz.tui.app import YoetzTui
from yoetz.tui.models import IntegrationOutcome, LayerState, ReadinessLayer
from yoetz.tui.runtime import RuntimeError_
from yoetz.tui.widgets.views import Option, SelectionView

pytestmark = pytest.mark.anyio

WIDE = (100, 34)
SMALL = (60, 18)
NARROW = (42, 24)

MakeApp = Callable[..., YoetzTui]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _options(view: object) -> tuple[Option, ...]:
    assert isinstance(view, SelectionView)
    return view.options


def _selected(view: object) -> Option:
    assert isinstance(view, SelectionView)
    option = view.selected
    assert option is not None
    return option


def transcript(app: YoetzTui) -> str:
    return "\n".join("\n".join((event.title, *event.body)) for event in app.transcript.events)


async def test_welcome_shows_detection_and_offers_the_recommended_path(
    make_app: MakeApp,
) -> None:
    app = make_app(first_run=True)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert "Welcome to Yoetz" in transcript(app)
        assert "Codex Desktop 0.44" in transcript(app)
        view = app.open_view
        assert view is not None
        assert view.view_name == "welcome"
        labels = [option.label for option in _options(view)]
        assert labels == [
            "Connect Yoetz to Codex",
            "Set up Yoetz without Codex",
            "Exit",
        ]


async def test_without_a_codex_installation_the_connect_option_is_disabled(
    make_app: MakeApp,
) -> None:
    app = make_app(first_run=True, runtime=FakeRuntime(harnesses=()))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        view = app.open_view
        assert view is not None
        options = _options(view)
        assert options[0].disabled is True
        # The cursor must not rest on a row that cannot be chosen.
        assert _selected(view).key == "local"


async def test_the_layout_still_renders_on_a_small_or_narrow_terminal(
    make_app: MakeApp,
) -> None:
    for size in (SMALL, NARROW):
        app = make_app(first_run=True)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            assert app.open_view is not None
            assert "Welcome to Yoetz" in transcript(app)
            # Nothing rendered may be wider than the terminal.
            for event in app.transcript.events:
                for line in event.body:
                    assert len(line) <= size[0]


async def test_multiple_installations_ask_which_one_before_previewing(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime(harnesses=(DESKTOP, CLI))
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # connect
        await pilot.pause()
        view = app.open_view
        assert view is not None
        assert view.view_name == "harness"
        labels = [option.label for option in _options(view)]
        assert labels == ["Codex Desktop 0.44", "Codex CLI 0.44", "Choose another executable"]


async def test_project_trust_precedes_any_integration_preview(make_app: MakeApp) -> None:
    runtime = FakeRuntime()
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # connect
        await pilot.pause()
        view = app.open_view
        assert view is not None
        assert view.view_name == "trust"
        assert runtime.applied == []


async def test_declining_project_trust_changes_nothing(make_app: MakeApp) -> None:
    runtime = FakeRuntime()
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("down", "enter")  # "No, quit"
        await pilot.pause()
        assert runtime.applied == []
        assert "This project was not changed." in transcript(app)


async def test_the_integration_preview_is_shown_before_anything_is_applied(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # connect
        await pilot.pause()
        await pilot.press("enter")  # trust
        await pilot.pause()
        view = app.open_view
        assert view is not None
        assert view.view_name == "integration"
        assert runtime.applied == []


async def test_approving_the_preview_applies_exactly_the_digests_that_were_shown(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # connect
        await pilot.pause()
        await pilot.press("enter")  # trust
        await pilot.pause()
        await pilot.press("enter")  # approve the preview
        await pilot.pause()
        assert runtime.applied == [("sha256:abc123", "7f8a92bd")]


async def test_declining_the_preview_leaves_the_project_unchanged(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("down", "enter")  # "No, keep this project unchanged"
        await pilot.pause()
        assert runtime.applied == []
        assert "left unchanged" in transcript(app)


async def test_a_foreign_mcp_entry_blocks_and_never_offers_to_replace_it(
    make_app: MakeApp,
) -> None:
    import dataclasses

    from builders.tui_runtime import PLAN

    runtime = FakeRuntime(plan=dataclasses.replace(PLAN, foreign_entry=True))
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")  # trust
        await pilot.pause()
        assert runtime.applied == []
        assert "Could not register Yoetz with Codex" in transcript(app)
        view = app.open_view
        assert view is not None
        assert view.view_name == "foreign"
        labels = [option.label.lower() for option in _options(view)]
        assert not any("replace" in label or "force" in label for label in labels)
        assert "Nothing was replaced or removed." in transcript(app)


async def test_a_failed_registration_is_reported_as_a_failure(make_app: MakeApp) -> None:
    runtime = FakeRuntime(
        apply_result=IntegrationOutcome(
            outcome="failed",
            reason="mcp_verification_failed",
            layers=(
                ReadinessLayer("mcp_registered", "MCP registered", LayerState.BLOCKED),
                ReadinessLayer("mcp_verified", "MCP verified", LayerState.UNPROVEN),
            ),
        )
    )
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        text = transcript(app)
        assert "could not finish connecting" in text
        assert "Yoetz is connected to this project" not in text


async def test_a_preview_failure_reports_the_reason_and_stops(make_app: MakeApp) -> None:
    runtime = FakeRuntime(
        plan_error=RuntimeError_("codex_unavailable", "the Codex registration could not be read")
    )
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert runtime.applied == []
        assert "codex_unavailable" in transcript(app)


async def test_keychain_unavailable_disables_system_storage_but_offers_a_passphrase(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime(secure_storage=False)
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # connect
        await pilot.pause()
        await pilot.press("enter")  # trust
        await pilot.pause()
        await pilot.press("enter")  # approve
        await pilot.pause()
        view = app.open_view
        assert view is not None
        assert view.view_name == "storage"
        options = _options(view)
        assert options[0].disabled is True
        assert options[1].disabled is False
        assert _selected(view).key == "passphrase"


async def test_setup_finishes_with_an_honest_summary_and_records_the_marker(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("enter")  # connect
        await pilot.pause()
        await pilot.press("enter")  # trust
        await pilot.pause()
        await pilot.press("enter")  # approve
        await pilot.pause()
        await pilot.press("escape")  # skip storage
        await pilot.pause()
        text = transcript(app)
        assert "Yoetz is ready" in text
        assert "Nothing is being sent to an external review model." in text
        assert app.markers_written == ["registered"]  # type: ignore[attr-defined]


async def test_choosing_local_only_setup_never_touches_the_project(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(first_run=True, runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("down", "enter")  # "Set up Yoetz without Codex"
        await pilot.pause()
        assert runtime.applied == []
        assert app.markers_written == ["local_only"]  # type: ignore[attr-defined]
        assert "Yoetz is ready" in transcript(app)
