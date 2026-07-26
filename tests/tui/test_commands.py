"""The post-install composer: slash filtering, dispatch, and each command view."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from builders.tui_runtime import FakeRuntime
from yoetz.tui.app import YoetzTui
from yoetz.tui.models import CheckMode, PrivacyPosture, ProviderPosture

pytestmark = pytest.mark.anyio

WIDE = (100, 34)
MakeApp = Callable[..., YoetzTui]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def transcript(app: YoetzTui) -> str:
    return "\n".join("\n".join((event.title, *event.body)) for event in app.transcript.events)


async def run_command(pilot: object, app: YoetzTui, command: str) -> None:
    app.composer.focus_input()
    app.composer.text = command
    await pilot.pause()  # type: ignore[attr-defined]
    await pilot.press("enter")  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Landing surface
# ---------------------------------------------------------------------------


async def test_the_post_install_landing_is_a_header_a_tip_and_a_composer(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        assert app.open_view is None
        assert "Ask Codex to use Yoetz" in transcript(app)
        assert app.composer.display is True
        # No dashboard, sidebar, or permanent settings screen exists to find.
        assert app.query("#sidebar").__len__() == 0


# ---------------------------------------------------------------------------
# Slash popup
# ---------------------------------------------------------------------------


async def test_typing_a_slash_opens_the_filtered_command_popup(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        popup = app.composer.popup
        assert popup.display is True
        assert len(popup.matches) >= 11


async def test_the_popup_narrows_as_the_command_is_typed(make_app: MakeApp) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("slash", "s", "t")
        await pilot.pause()
        popup = app.composer.popup
        assert popup.selected is not None
        assert popup.selected.name == "status"


async def test_the_popup_closes_on_escape_without_running_anything(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        before = len(app.transcript.events)
        await pilot.press("slash", "s")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.composer.popup.display is False
        assert len(app.transcript.events) == before


async def test_an_unknown_command_is_reported_rather_than_guessed(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/nonsense")
        assert "not a Yoetz command" in transcript(app)


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


async def test_status_renders_each_readiness_layer_and_the_privacy_claim(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/status")
        text = transcript(app)
        assert "Yoetz status" in text
        assert "Nothing is leaving this computer." in text
        # Technical details keep every layer separate.
        event = app.transcript.latest_with_details()
        assert event is not None
        details = "\n".join(event.details)
        assert "MCP verified" in details
        assert "Deeper review ready" in details


async def test_status_does_not_claim_privacy_it_could_not_read(
    make_app: MakeApp,
) -> None:
    unreadable = PrivacyPosture(profile=None, llm_inference_enabled=None, readable=False)
    app = make_app(runtime=FakeRuntime(privacy=unreadable))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/status")
        text = transcript(app)
        assert "Nothing is leaving this computer." not in text
        assert "could not be read" in text


# ---------------------------------------------------------------------------
# /help and /doctor
# ---------------------------------------------------------------------------


async def test_help_lists_every_command_with_a_plain_language_description(
    make_app: MakeApp,
) -> None:
    from yoetz.tui.commands import SLASH_COMMANDS

    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/help")
        text = transcript(app)
        for command in SLASH_COMMANDS:
            assert command.token in text
            assert command.summary in text


async def test_doctor_reports_without_changing_anything(make_app: MakeApp) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/doctor")
        assert "Installation report" in transcript(app)
        assert runtime.applied == []
        assert runtime.ceremonies == []
        assert runtime.bindings == []


# ---------------------------------------------------------------------------
# /privacy
# ---------------------------------------------------------------------------


async def test_privacy_offers_the_four_profiles_and_starts_from_the_current_one(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/privacy")
        view = app.open_view
        assert view is not None
        labels = [option.label for option in view.options]  # type: ignore[attr-defined]
        assert labels == [
            "Local only",
            "Ask every time",
            "Minimal external review",
            "Trusted provider",
        ]
        assert "Local only is the default" in transcript(app)


async def test_widening_privacy_shows_an_exact_disclosure_before_confirming(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/privacy")
        await pilot.press("down", "down", "enter")  # Minimal external review
        await pilot.pause()
        view = app.open_view
        assert view is not None
        assert view.view_name == "privacy-widen"
        body = "\n".join(getattr(view, "_body", ()))
        assert "Data that may be sent" in body
        assert "Never sent, under any choice" in body
        assert "Purpose" in body
        assert "Scope" in body
        # The cursor starts on the declining option for a widening decision.
        assert view.selected.key == "decline"  # type: ignore[attr-defined]


async def test_approving_a_widening_still_defers_to_the_trusted_ceremony(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/privacy")
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        await pilot.press("up", "enter")  # approve
        await pilot.pause()
        text = transcript(app)
        assert "yoetz privacy propose --profile minimal_external" in text
        assert "Nothing has changed yet." in text


async def test_escaping_the_privacy_picker_keeps_the_current_setting(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/privacy")
        await pilot.press("escape")
        await pilot.pause()
        assert "Privacy was left unchanged." in transcript(app)


# ---------------------------------------------------------------------------
# /provider
# ---------------------------------------------------------------------------


async def test_the_provider_picker_is_searchable_over_the_reviewed_presets(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/provider")
        view = app.open_view
        assert view is not None
        assert view.view_name == "provider"
        labels = [option.label for option in view.options]  # type: ignore[attr-defined]
        assert "OpenAI" in labels
        view.filter("anthro")  # type: ignore[attr-defined]
        assert [option.label for option in view.options] == ["Anthropic"]  # type: ignore[attr-defined]


async def test_the_provider_flow_saves_a_binding_then_asks_for_the_key_separately(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/provider")
        await pilot.press("enter")  # OpenAI
        await pilot.pause()
        await pilot.press("enter")  # accept the default model
        await pilot.pause()
        view = app.open_view
        assert view is not None
        assert view.view_name == "provider-confirm"
        # Nothing is written until the endpoint has been shown and approved.
        assert runtime.bindings == []
        await pilot.press("enter")
        await pilot.pause()
        assert runtime.bindings == [("official_openai", "gpt-4.1-mini")]
        # The credential is a separate, explicit handoff.
        assert app.open_view is not None
        assert app.open_view.view_name == "confidential"
        assert runtime.ceremonies == []


async def test_cancelling_the_credential_handoff_stores_no_key(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/provider")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("escape")  # decline the handoff
        await pilot.pause()
        assert runtime.ceremonies == []
        text = transcript(app)
        assert "Nothing was stored." in text
        assert "without a key" in text


async def test_a_stored_provider_is_never_reported_as_a_tested_one(
    make_app: MakeApp,
) -> None:
    bound = ProviderPosture(
        endpoint_bound=True,
        provider_id="openai",
        model="gpt-4.1-mini",
        endpoint_profile_id="openai-responses",
        credential_connected=True,
        llm_inference_enabled=False,
        semantic_enabled=False,
        semantic_ready=False,
        readiness_determinable=True,
        transport_tested=False,
    )
    app = make_app(runtime=FakeRuntime(provider=bound))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/provider")
        # provider, model, endpoint confirmation, then the credential handoff.
        for _ in range(4):
            await pilot.press("enter")
            await pilot.pause()
        text = transcript(app)
        assert "Live provider connection has not been tested" in text
        assert "External semantic review is not yet proven ready" in text


async def test_an_unavailable_provider_test_is_never_reported_as_a_pass(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/provider")
        for _ in range(4):  # provider, model, confirm, credential handoff
            await pilot.press("enter")
            await pilot.pause()
        assert app.open_view is not None
        assert app.open_view.view_name == "provider-test"
        await pilot.press("enter")  # run the test
        await pilot.pause()
        text = transcript(app)
        assert "not available from this build" in text
        assert "will not report a connection as working without probing it" in text


# ---------------------------------------------------------------------------
# /work, /check, /receipt
# ---------------------------------------------------------------------------


async def test_work_is_honest_that_no_task_index_exists(make_app: MakeApp) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/work")
        view = app.open_view
        assert view is not None
        assert view.view_name == "work"
        body = "\n".join(getattr(view, "_body", ()))
        assert "does not keep" in body


async def test_opening_a_task_by_name_uses_the_start_operation(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/work")
        await pilot.press("enter")  # "Open a task by name"
        await pilot.pause()
        for character in "upload":
            await pilot.press(character)
        await pilot.press("enter")
        await pilot.pause()
        assert runtime.opened == ["upload"]
        assert "upload" in transcript(app)


async def test_check_offers_the_three_modes_and_passes_the_chosen_one_through(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    app._active_task_title = "upload"  # pyright: ignore[reportPrivateUsage]
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/check")
        view = app.open_view
        assert view is not None
        labels = [option.label for option in view.options]  # type: ignore[attr-defined]
        assert labels == [
            "Use deeper review when available",
            "Require deeper review",
            "Local deterministic checks only",
        ]
        await pilot.press("down", "down", "enter")  # deterministic only
        await pilot.pause()
        assert runtime.checks == [("upload", CheckMode.DETERMINISTIC_ONLY)]


async def test_receipt_offers_the_supported_formats(make_app: MakeApp) -> None:
    app = make_app()
    app._active_task_title = "upload"  # pyright: ignore[reportPrivateUsage]
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/receipt")
        view = app.open_view
        assert view is not None
        labels = [option.label for option in view.options]  # type: ignore[attr-defined]
        assert labels == ["Markdown", "Plain text", "JSON"]
        await pilot.press("enter")
        await pilot.pause()
        assert "Receipt for upload" in transcript(app)


# ---------------------------------------------------------------------------
# /connect and /service
# ---------------------------------------------------------------------------


async def test_connect_offers_inspection_before_any_mutation(make_app: MakeApp) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/connect")
        view = app.open_view
        assert view is not None
        assert view.view_name == "connect"
        labels = [option.label for option in view.options]  # type: ignore[attr-defined]
        assert labels == [
            "Inspect this connection",
            "Connect or repair",
            "Show exact technical state",
        ]
        await pilot.press("enter")  # inspect
        await pilot.pause()
        assert runtime.applied == []


async def test_connect_without_a_harness_points_at_local_verification(
    make_app: MakeApp,
) -> None:
    app = make_app(runtime=FakeRuntime(harnesses=()))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/connect")
        text = transcript(app)
        assert "No Codex installation was found" in text
        assert "/check" in text


async def test_repair_still_shows_the_preview_before_confirmation(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/connect")
        await pilot.press("down", "enter")  # Connect or repair
        await pilot.pause()
        assert app.open_view is not None
        assert app.open_view.view_name == "integration"
        assert runtime.applied == []


async def test_service_shows_state_and_offers_lifecycle_actions(
    make_app: MakeApp,
) -> None:
    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/service")
        assert "Local service" in transcript(app)
        view = app.open_view
        assert view is not None
        labels = [option.label for option in view.options]  # type: ignore[attr-defined]
        assert labels == ["Unlock", "Set up a passphrase", "Lock now", "Stop the service"]
