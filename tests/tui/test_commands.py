"""The post-install composer: slash filtering, dispatch, and each command view."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from builders.tui_runtime import RECOMMEND_PRIVATE, FakeRuntime
from yoetz.tui.app import YoetzTui
from yoetz.tui.models import CheckMode, PrivacyPosture, ProviderOption, ProviderPosture

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


async def test_popup_selection_preserves_the_typed_argument_suffix(
    make_app: MakeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yoetz.tui.widgets.composer import CommandSubmitted

    app = make_app()
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        captured: list[str] = []
        post_message = app.composer.post_message

        def record(message: object) -> bool:
            if isinstance(message, CommandSubmitted):
                captured.append(message.value)
            return post_message(message)  # pyright: ignore[reportArgumentType]

        monkeypatch.setattr(app.composer, "post_message", record)
        app.composer.focus_input()
        app.composer.text = "/receipt markdown"
        await pilot.pause()
        assert app.composer.popup.display is True
        await pilot.press("enter")
        await pilot.pause()
        assert captured == ["/receipt markdown"]


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


async def test_privacy_leads_with_the_recommendation_and_three_choices(
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
            "Keep current",
            "Review recommended change (Metadata only)",
            "Other privacy options",
        ]
        text = transcript(app)
        assert "Currently: local only" in text
        assert "Recommended: Metadata only" in text
        # The cost of accepting is stated next to the benefit, in the production wording —
        # including the consequence, which is the part that makes it a cost rather than a note.
        assert (
            "In exchange, the reviewer sees structural metadata only, so it cannot judge "
            "whether a claim is actually supported." in text
        )


async def test_choosing_the_recommendation_runs_the_trusted_ceremony_with_no_local_approval(
    make_app: MakeApp,
) -> None:
    """The interface hands over directly; it does not take a consent of its own first."""

    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/privacy")
        await pilot.press("down", "enter")  # Review recommended change
        await pilot.pause()
        assert runtime.ceremonies == ["privacy:metadata_only"]
        text = transcript(app)
        assert "Privacy setup complete" in text
        assert "Effective profile: confirm_every_request" in text


async def test_other_privacy_options_lists_the_command_line_recipe_names(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/privacy")
        await pilot.press("down", "down", "enter")  # Other privacy options
        await pilot.pause()
        view = app.open_view
        assert view is not None
        assert view.view_name == "privacy-recipe"
        assert [option.label for option in view.options] == [  # type: ignore[attr-defined]
            "Private",
            "Metadata only",
            "Assisted review",
            "Expanded review",
            "Custom",
        ]
        await pilot.press("down", "down", "down", "enter")  # Expanded review
        await pilot.pause()
        assert runtime.ceremonies == ["privacy:expanded_review"]


async def test_a_policy_already_on_the_recommendation_is_not_offered_as_a_change(
    make_app: MakeApp,
) -> None:
    app = make_app(runtime=FakeRuntime(recommendation=RECOMMEND_PRIVATE))
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/privacy")
        view = app.open_view
        assert view is not None
        assert [option.key for option in view.options] == ["keep", "other"]  # type: ignore[attr-defined]
        assert "already on the recommended privacy policy" in transcript(app)


async def test_escaping_the_privacy_picker_keeps_the_current_setting(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/privacy")
        await pilot.press("escape")
        await pilot.pause()
        assert "Privacy was left unchanged." in transcript(app)
        assert runtime.ceremonies == []


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
        assert "Codex subscription status" in labels
        assert "Disconnect dedicated Codex home" in labels
        assert "Roll back Yoetz Codex binding" in labels
        assert "Switch Codex ChatGPT account" in labels
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
        assert runtime.ceremonies == ["privacy:metadata_only+recommended"]
        assert runtime.privacy.repository_grant_state == "granted"
        assert "Exact repository grant: granted for this repository." in transcript(app)


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
        assert runtime.ceremonies == ["privacy:metadata_only+recommended"]
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


async def test_provider_setup_ends_with_an_honest_readiness_summary(
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
        assert app.open_view is None
        assert runtime.ceremonies == [
            "privacy:metadata_only+recommended",
            "provider_credential",
        ]
        text = transcript(app)
        assert "Live provider connection has not been tested" in text
        assert "External semantic review is not yet proven ready" in text


async def test_provider_setup_never_requests_a_key_without_an_exact_repository_grant(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime(privacy_setup_grant_state="missing")
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/provider")
        for _ in range(3):  # provider, model, endpoint confirmation
            await pilot.press("enter")
            await pilot.pause()
        assert runtime.bindings == [("official_openai", "gpt-4.1-mini")]
        assert runtime.ceremonies == ["privacy:metadata_only+recommended"]
        assert app.open_view is None
        text = transcript(app)
        assert "Provider binding saved, without repository authority" in text
        assert "no API key was requested" in text


async def test_provider_exposes_codex_status_disconnect_and_rollback(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/provider")
        view = app.open_view
        assert view is not None
        view.filter("status")  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause()
        text = transcript(app)
        assert runtime.subscription_actions == ["status"]
        assert "Codex subscription status" in text
        assert "Auth mode: chatgpt" in text

        await run_command(pilot, app, "/provider")
        view = app.open_view
        assert view is not None
        view.filter("disconnect")  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert runtime.subscription_actions == ["status", "disconnect"]
        assert "Codex subscription disconnected" in transcript(app)

        await run_command(pilot, app, "/provider")
        view = app.open_view
        assert view is not None
        view.filter("roll back")  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert runtime.subscription_actions == ["status", "disconnect", "rollback"]
        assert "Codex subscription binding rolled back" in transcript(app)


async def test_provider_can_switch_the_codex_account(make_app: MakeApp) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/provider")
        view = app.open_view
        assert view is not None
        view.filter("switch")  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause()
        for _ in range(4):
            await pilot.press("enter")
            await pilot.pause()
        view = app.open_view
        assert view is not None
        body = " ".join(getattr(view, "body", ()) or getattr(view, "_body", ()))
        assert "log out the dedicated home first" in body.casefold()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert runtime.subscription_actions == ["switch"]


async def test_provider_switch_preserves_existing_subscription_model_when_omitted(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    runtime.codex_subscription_defaults = lambda: (  # type: ignore[method-assign]
        "/opt/codex/codex",
        "/var/lib/yoetz/codex-home",
        "gpt-5.6-sol",
        "high",
    )
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/provider")
        view = app.open_view
        assert view is not None
        view.filter("switch")  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause()
        for _ in range(4):
            await pilot.press("enter")
            await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    assert runtime.subscription_setups == [
        (
            "/opt/codex/codex",
            "/var/lib/yoetz/codex-home",
            "gpt-5.6-sol",
            "high",
            True,
        )
    ]


async def test_provider_discloses_and_reports_a_reused_codex_login(make_app: MakeApp) -> None:
    """Setup on a home Codex already reports signed in reuses it and says so (#534)."""

    runtime = FakeRuntime()
    runtime.subscription_login_reused = True
    base_options = runtime.provider_options()
    codex_option = ProviderOption(
        choice="codex_subscription",
        label="Codex with ChatGPT subscription",
        provider_id="openai-codex",
        host="openai.com",
        base_path_prefix=" through Codex-managed login",
        default_model="gpt-5.6-luna",
        api_style="Codex app-server v2 (stdio)",
        endpoint_profile_id="codex-chatgpt-subscription",
        endpoint_profile_version="1.0.0",
    )
    runtime.provider_options = lambda: (*base_options, codex_option)  # type: ignore[method-assign]
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/provider")
        view = app.open_view
        assert view is not None
        view.filter("Codex with ChatGPT subscription")  # type: ignore[attr-defined]
        await pilot.press("enter")
        await pilot.pause()
        for _ in range(4):
            await pilot.press("enter")
            await pilot.pause()
        view = app.open_view
        assert view is not None
        body = " ".join(getattr(view, "body", ()) or getattr(view, "_body", ()))
        assert "reused without a new sign-in" in body.casefold()
        assert "log out the dedicated home first" not in body.casefold()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert runtime.subscription_actions == ["setup"]
        assert runtime.subscription_setups[0][2] == "gpt-5.6-luna"
        assert "reused the existing Codex login" in transcript(app)


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
        assert labels == [
            "Unlock",
            "Set up a passphrase",
            "Change the passphrase",
            "Lock now",
            "Stop the service",
        ]


async def test_service_rotate_uses_the_same_confidential_handoff(
    make_app: MakeApp,
) -> None:
    runtime = FakeRuntime()
    app = make_app(runtime=runtime)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/service")
        await pilot.press("down", "down", "enter")  # Change the passphrase
        await pilot.pause()
        view = app.open_view
        assert view is not None
        assert view.view_name == "confidential"
        assert view.title_text == "Change your Yoetz passphrase"
        await pilot.press("enter")
        await pilot.pause()
        assert runtime.ceremonies == ["rotate_vault_passphrase"]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


async def test_the_transcript_gets_the_space_and_the_composer_stays_one_line(
    make_app: MakeApp,
) -> None:
    """A container defaulting to ``1fr`` once ate the whole transcript.

    The shape of this interface is the product: a large scrollable history with
    a single composer line and a one-line footer beneath it.
    """

    from yoetz.tui.widgets.composer import Composer, Footer
    from yoetz.tui.widgets.history import History

    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        history = app.query_one(History).region
        composer = app.query_one(Composer).region
        footer = app.query_one(Footer).region

        assert composer.height == 1
        assert footer.height == 1
        # The history takes everything the header and bottom pane do not.
        assert history.height >= 15
        assert history.y + history.height <= composer.y
        assert footer.y == 29


async def test_a_temporary_view_replaces_the_composer_and_then_restores_it(
    make_app: MakeApp,
) -> None:
    """Opening a view must not cost the user the line they were writing."""

    from yoetz.tui.widgets.views import Option, SelectionView

    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.composer.text = "half typed"
        await pilot.pause()

        app.push_view(SelectionView(name="probe", options=[Option("a", "Alpha")]))
        await pilot.pause()
        assert app.open_view is not None
        assert app.composer.display is False

        await pilot.press("escape")
        await pilot.pause()
        assert app.open_view is None
        assert app.composer.display is True
        assert app.composer.text == "half typed"


async def test_option_rows_use_the_width_the_terminal_actually_has(
    make_app: MakeApp,
) -> None:
    """Rows were once laid out against a width the widget did not have yet."""

    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await run_command(pilot, app, "/service")
        view = app.open_view
        assert view is not None
        assert view.region.width >= 90
