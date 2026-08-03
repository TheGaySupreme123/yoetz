"""The first-run questionnaire materializes only closed, bounded policies."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from builders.privacy_policies import local_only_policy
from yoetz.cli.privacy_setup import (
    PrivacyRecipe,
    PrivacySetupAnswers,
    build_candidate_policy,
)
from yoetz.config.models import OFFICIAL_OPENAI_ENDPOINT_PROFILE_ID
from yoetz.domain.privacy import (
    AuthorizationScopeKind,
    DataClass,
    EgressChannel,
    PrivacyPolicy,
    ProviderBinding,
    ReviewContextProfile,
)
from yoetz.domain.values import JsonObject
from yoetz.protocol.models import DataCategory
from yoetz.service.confidential_protocol import PrivacyDecisionResult


def _answers(**overrides: object) -> PrivacySetupAnswers:
    values: dict[str, object] = {
        "network_egress": True,
        "local_models": False,
        "external_provider": ProviderBinding(
            "fireworks",
            "accounts/fireworks/models/minimax-m3",
            "fireworks-responses",
            "1.0.0",
            "external",
        ),
        "require_current_provider_data_use_evidence": False,
        "local_model_binding": None,
        "review_context": ReviewContextProfile.ASSISTED,
        "content_categories": (
            DataCategory.BOUNDED_STRUCTURAL_METADATA,
            DataCategory.EVIDENCE_EXCERPT,
            DataCategory.REPOSITORY_EXCERPT,
        ),
        "content_data_classes": (
            DataClass.PUBLIC_STRUCTURAL,
            DataClass.ORDINARY_USER_CONTENT,
        ),
        "agent_context_categories": (DataCategory.BOUNDED_STRUCTURAL_METADATA,),
        "agent_context_data_classes": (DataClass.PUBLIC_STRUCTURAL,),
        "local_model_categories": (),
        "local_model_data_classes": (),
        "request_confirmation": False,
        "telemetry": False,
        "crash_diagnostics": False,
        "updates": False,
        "capability_testing": False,
        "authorization_scope": AuthorizationScopeKind.TASK,
    }
    values.update(overrides)
    return PrivacySetupAnswers(**values)  # type: ignore[arg-type]


def test_assisted_review_is_bound_to_one_provider_and_bounded_categories() -> None:
    current = local_only_policy()
    candidate = build_candidate_policy(
        current,
        _answers(),
        now=datetime(2026, 7, 29, tzinfo=UTC),
    )

    llm = next(
        channel
        for channel in candidate.channel_policies
        if channel.channel is EgressChannel.LLM_INFERENCE
    )
    assert candidate.network_egress_permitted is True
    assert llm.enabled is True
    assert llm.provider_binding is not None
    assert llm.provider_binding.provider_id == "fireworks"
    assert llm.scope_ceiling is AuthorizationScopeKind.TASK
    assert llm.allowed_data_classes == (
        DataClass.ORDINARY_USER_CONTENT,
        DataClass.PUBLIC_STRUCTURAL,
    )
    assert DataCategory.TRANSCRIPT_EXCERPT not in llm.allowed_categories
    assert all(
        not channel.enabled
        for channel in candidate.channel_policies
        if channel.channel
        not in {EgressChannel.LLM_INFERENCE, EgressChannel.UPDATE_CHECKS}
    )
    # Product default recipes and _answers leave updates off unless set; this helper defaults off.
    assert candidate.supersedes_policy_digest == current.policy_digest


def test_unavailable_network_channels_fail_closed() -> None:
    with pytest.raises(ValueError, match="privacy_setup_channel_unsupported"):
        build_candidate_policy(
            local_only_policy(),
            _answers(telemetry=True),
            now=datetime(2026, 7, 29, tzinfo=UTC),
        )


def test_update_checks_may_be_enabled_without_llm() -> None:
    candidate = build_candidate_policy(
        local_only_policy(),
        _answers(
            network_egress=False,
            external_provider=None,
            review_context=ReviewContextProfile.STRUCTURAL,
            content_categories=(),
            content_data_classes=(DataClass.PUBLIC_STRUCTURAL,),
            request_confirmation=False,
            updates=True,
            authorization_scope=AuthorizationScopeKind.MACHINE,
        ),
        now=datetime(2026, 7, 29, tzinfo=UTC),
    )
    updates = next(
        channel
        for channel in candidate.channel_policies
        if channel.channel is EgressChannel.UPDATE_CHECKS
    )
    llm = next(
        channel
        for channel in candidate.channel_policies
        if channel.channel is EgressChannel.LLM_INFERENCE
    )
    assert candidate.network_egress_permitted is True
    assert updates.enabled is True
    assert llm.enabled is False
    assert candidate.profile.value == "local_only"


def test_network_egress_requires_an_exact_provider_binding() -> None:
    with pytest.raises(ValueError, match="privacy_setup_provider_binding_required"):
        build_candidate_policy(
            local_only_policy(),
            _answers(external_provider=None),
            now=datetime(2026, 7, 29, tzinfo=UTC),
        )


def test_metadata_only_hint_is_the_selected_privacy_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as module

    prompts: list[tuple[str, str]] = []

    def prompt(label: str, *, default: str) -> str:
        prompts.append((label, default))
        return default

    monkeypatch.setattr(module.typer, "prompt", prompt)

    recipe = module._recipe_prompt(  # pyright: ignore[reportPrivateUsage]
        "metadata_only", "metadata_only"
    )

    assert recipe == "metadata_only"
    assert prompts == [("Choose a privacy option", "2")]


def test_listing_the_options_never_reads_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendering must not be able to fail on an unrelated environment variable.

    `_recipe_prompt` briefly derived the recommendation itself, which loads configuration and
    raises `ConfigError` on any unrecognized `YOETZ_*` variable. `ConfigError` is not a
    `ValueError`, so it escaped every handler and turned the option list into a traceback.
    """

    import yoetz.cli.privacy_setup as module

    def exploding_bindings() -> tuple[ProviderBinding | None, ProviderBinding | None]:
        raise AssertionError("rendering the option list must not read configuration")

    def prompt(_label: str, *, default: str) -> str:
        return default

    monkeypatch.setattr(module, "_configured_bindings", exploding_bindings)
    monkeypatch.setattr(module.typer, "prompt", prompt)

    assert (
        module._recipe_prompt("private", "private")  # pyright: ignore[reportPrivateUsage]
        == "private"
    )


def test_recommendation_is_metadata_only_when_a_provider_is_configured() -> None:
    from yoetz.cli.privacy_setup import recommended_privacy_recipe

    assert recommended_privacy_recipe(_answers().external_provider) == "metadata_only"


def test_recommendation_is_private_when_no_provider_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as module

    monkeypatch.setattr(module, "_configured_bindings", lambda: (None, None))

    assert module.recommended_privacy_recipe() == "private"


def test_recommended_metadata_recipe_is_bounded_and_confirmation_first() -> None:
    import yoetz.cli.privacy_setup as module

    external = _answers().external_provider
    answers = module._recipe_answers(  # pyright: ignore[reportPrivateUsage]
        "metadata_only", local_only_policy(), external
    )

    assert answers.network_egress is True
    assert answers.external_provider == external
    assert answers.review_context is ReviewContextProfile.STRUCTURAL
    assert answers.content_categories == (
        DataCategory.BOUNDED_STRUCTURAL_METADATA,
        DataCategory.DECLARED_FILE_TYPE,
    )
    assert answers.content_data_classes == (DataClass.PUBLIC_STRUCTURAL,)
    assert answers.request_confirmation is True
    assert answers.require_current_provider_data_use_evidence is False
    assert answers.authorization_scope is AuthorizationScopeKind.TASK


def test_private_recipe_requires_no_provider() -> None:
    import yoetz.cli.privacy_setup as module

    answers = module._recipe_answers(  # pyright: ignore[reportPrivateUsage]
        "private", local_only_policy(), None
    )

    assert answers.network_egress is False
    assert answers.external_provider is None
    assert answers.content_categories == ()
    assert answers.request_confirmation is False
    assert answers.authorization_scope is AuthorizationScopeKind.MACHINE


@pytest.mark.parametrize("recipe", ["metadata_only", "assisted_review", "expanded_review"])
def test_external_recipes_fail_closed_without_a_configured_provider(
    recipe: PrivacyRecipe,
) -> None:
    """No recipe may enable egress against a destination the installation has not bound."""

    import yoetz.cli.privacy_setup as module

    with pytest.raises(ValueError, match="privacy_setup_provider_binding_required"):
        module._recipe_answers(recipe, local_only_policy(), None)  # pyright: ignore[reportPrivateUsage]


def test_named_recipes_never_enable_an_unsupported_channel() -> None:
    """Telemetry, diagnostics, and capability testing stay off; update_checks defaults on."""

    import yoetz.cli.privacy_setup as module

    named: tuple[PrivacyRecipe, ...] = (
        "private",
        "metadata_only",
        "assisted_review",
        "expanded_review",
    )
    for recipe in named:
        answers = module._recipe_answers(  # pyright: ignore[reportPrivateUsage]
            recipe, local_only_policy(), _answers().external_provider
        )
        assert (
            answers.telemetry,
            answers.crash_diagnostics,
            answers.updates,
            answers.capability_testing,
        ) == (False, False, True, False)


def test_privacy_options_explain_tradeoffs_and_change_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import yoetz.cli.privacy_setup as module

    module._render_recipe_options(  # pyright: ignore[reportPrivateUsage]
        recommended="metadata_only"
    )

    output = capsys.readouterr().out
    assert "Private — maximum confidentiality" in output
    assert "Metadata only (recommended)" in output
    assert "Assisted review — better problem-specific feedback" in output
    assert "Expanded review — most reviewer context" in output
    assert "Custom — maximum control" in output
    assert "yoetz --privacy" in output


def test_named_review_context_reprompts_instead_of_aborting_on_yes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import yoetz.cli.privacy_setup as module

    answers = iter(("y", "assisted"))

    def prompt(*_args: object, **_kwargs: object) -> str:
        return next(answers)

    monkeypatch.setattr(module.typer, "prompt", prompt)

    review = module._review_context_prompt(  # pyright: ignore[reportPrivateUsage]
        ReviewContextProfile.ASSISTED
    )

    assert review is ReviewContextProfile.ASSISTED
    assert "Choose structural, goal_aware, assisted, or expanded." in capsys.readouterr().out


def _install_setup_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    current: PrivacyPolicy,
    confirmations: Iterator[bool],
    prompts: list[str],
    external: ProviderBinding | None = None,
) -> None:
    """Stub only the I/O edges, leaving the real recipe and branch selection under test."""

    import yoetz.cli.privacy_setup as module

    async def effective() -> PrivacyPolicy:
        return current

    async def propose(_candidate: PrivacyPolicy, _digest: str) -> str:
        return "pvp_1"

    async def decide(_proposal: str) -> PrivacyDecisionResult:
        return PrivacyDecisionResult("committed", "sha256:" + "c" * 64)

    def confirm(prompt: str, *, default: bool = False) -> bool:
        del default
        prompts.append(prompt)
        return next(confirmations)

    def interactive() -> bool:
        return True

    def bindings() -> tuple[ProviderBinding | None, ProviderBinding | None]:
        return external, None

    def render_options(*, recommended: object = None) -> None:
        del recommended

    def render_review(_candidate: PrivacyPolicy) -> None:
        return None

    monkeypatch.setattr(module, "_interactive_terminal", interactive)
    monkeypatch.setattr(module, "_effective_policy", effective)
    monkeypatch.setattr(module, "_configured_bindings", bindings)
    monkeypatch.setattr(module, "_render_recipe_options", render_options)
    monkeypatch.setattr(module, "_render_review", render_review)
    monkeypatch.setattr(module.typer, "confirm", confirm)
    monkeypatch.setattr(module, "_propose", propose)
    monkeypatch.setattr(module, "_decide", decide)


def _forbid_custom_sections(monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    import yoetz.cli.privacy_setup as module

    def forbidden(*_args: object) -> PrivacySetupAnswers:
        raise AssertionError(reason)

    monkeypatch.setattr(module, "_ask_custom_answers", forbidden)


@pytest.mark.anyio
async def test_accepting_recommended_policy_asks_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as module

    prompts: list[str] = []

    def forbidden_choice(_hint: object) -> str:
        raise AssertionError("accepting the recommendation must not open the recipe list")

    _install_setup_stubs(
        monkeypatch,
        current=local_only_policy(),
        confirmations=iter((True,)),
        prompts=prompts,
        external=_answers().external_provider,
    )
    _forbid_custom_sections(
        monkeypatch, "recommended acceptance must skip field-level configuration"
    )
    monkeypatch.setattr(module, "_recipe_choice", forbidden_choice)

    report = await module.run_privacy_setup(offer_recommended=True)

    assert report.outcome == "configured"
    assert prompts == ["Use this recommended privacy policy?"]


@pytest.mark.anyio
async def test_declining_recommended_policy_opens_the_recipe_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as module

    prompts: list[str] = []
    offered: list[object] = []
    _install_setup_stubs(
        monkeypatch,
        current=local_only_policy(),
        confirmations=iter((False, True)),
        prompts=prompts,
        external=_answers().external_provider,
    )

    def choose(hint: object) -> str:
        offered.append(hint)
        return "assisted_review"

    monkeypatch.setattr(module, "_recipe_choice", choose)
    _forbid_custom_sections(monkeypatch, "a named recipe must not open field-level configuration")

    report = await module.run_privacy_setup(offer_recommended=True)

    assert report.outcome == "configured"
    # The declined recommendation is what the recipe list starts on.
    assert offered == ["metadata_only"]
    assert prompts == [
        "Use this recommended privacy policy?",
        "Create this exact privacy proposal (Assisted review)?",
    ]


@pytest.mark.anyio
async def test_a_named_recipe_hint_skips_both_the_list_and_the_custom_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as module

    prompts: list[str] = []

    def forbidden_prompt(_hint: object) -> str:
        raise AssertionError("a named hint must not re-ask which recipe to use")

    _install_setup_stubs(
        monkeypatch,
        current=local_only_policy(),
        confirmations=iter((True,)),
        prompts=prompts,
        external=_answers().external_provider,
    )
    monkeypatch.setattr(module, "_recipe_prompt", forbidden_prompt)
    _forbid_custom_sections(monkeypatch, "a named recipe must not open field-level configuration")

    report = await module.run_privacy_setup(recipe_hint="expanded_review")

    assert report.outcome == "configured"
    assert prompts == ["Create this exact privacy proposal (Expanded review)?"]


@pytest.mark.anyio
async def test_custom_is_the_only_path_into_field_level_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as module

    prompts: list[str] = []
    asked: list[object] = []
    _install_setup_stubs(
        monkeypatch,
        current=local_only_policy(),
        confirmations=iter((True,)),
        prompts=prompts,
        external=_answers().external_provider,
    )

    def custom_recipe(_hint: object) -> str:
        return "custom"

    def custom(*args: object) -> PrivacySetupAnswers:
        asked.append(args)
        return _answers()

    monkeypatch.setattr(module, "_recipe_prompt", custom_recipe)
    monkeypatch.setattr(module, "_ask_custom_answers", custom)

    report = await module.run_privacy_setup(recipe_hint="custom")

    assert report.outcome == "configured"
    assert len(asked) == 1
    assert prompts == ["Use this exact custom privacy policy?"]


@pytest.mark.anyio
async def test_a_custom_hint_does_not_reopen_the_recipe_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller that passed `custom` already chose; asking again is a confirmation of nothing."""

    import yoetz.cli.privacy_setup as module

    def forbidden_prompt(*_args: object) -> str:
        raise AssertionError("a custom hint must not reopen the recipe list")

    _install_setup_stubs(
        monkeypatch,
        current=local_only_policy(),
        confirmations=iter((True,)),
        prompts=[],
        external=_answers().external_provider,
    )

    def custom(*_args: object) -> PrivacySetupAnswers:
        return _answers()

    monkeypatch.setattr(module, "_recipe_prompt", forbidden_prompt)
    monkeypatch.setattr(module, "_ask_custom_answers", custom)

    assert (await module.run_privacy_setup(recipe_hint="custom")).outcome == "configured"


def test_custom_configuration_announces_all_five_sections(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Grouped sections replace the flat thirteen questions, and none is silently skipped."""

    import yoetz.cli.privacy_setup as module

    def confirm(_prompt: str, *, default: bool = False) -> bool:
        del default
        return False

    def prompt(_label: str, *, default: str = "") -> str:
        return default

    monkeypatch.setattr(module.typer, "confirm", confirm)
    monkeypatch.setattr(module.typer, "prompt", prompt)

    answers = module._ask_custom_answers(  # pyright: ignore[reportPrivateUsage]
        local_only_policy(), _answers().external_provider, None
    )

    output = capsys.readouterr().out
    for number, title in (
        (1, "External and local destinations"),
        (2, "What an external reviewer may see"),
        (3, "Local visibility: agent host and local model"),
        (4, "Per-request confirmation and authorization scope"),
        (5, "Package updates and unsupported channels"),
    ):
        assert f"Section {number} of 5 — {title}" in output
    # Section 5 asks about package updates; the other three remain unsupported/off.
    assert "cannot be turned on here" in output
    assert "package update" in output.casefold()
    # Mock confirm returns False for every prompt, so updates is explicitly declined.
    assert (
        answers.telemetry,
        answers.crash_diagnostics,
        answers.updates,
        answers.capability_testing,
    ) == (False, False, False, False)


@pytest.mark.anyio
async def test_effective_policy_accepts_service_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.app as app_module
    import yoetz.cli.privacy_setup as module
    import yoetz.cli.provider_status as provider_status_module
    from yoetz.adapters.privacy.catalog import encode_privacy_policy_json

    current = local_only_policy()

    class Client:
        async def privacy_get_effective(self, request: object) -> JsonObject:
            assert request == JsonObject({"scope": "machine"})
            return JsonObject({"policy": encode_privacy_policy_json(current)})

        async def close(self) -> None:
            return None

    async def build_client() -> Client:
        return Client()

    monkeypatch.setattr(app_module, "build_service_client", build_client)
    monkeypatch.setattr(
        provider_status_module,
        "machine_scope_request",
        lambda: JsonObject({"scope": "machine"}),
    )

    observed = await module._effective_policy()  # pyright: ignore[reportPrivateUsage]

    assert observed == current


@pytest.mark.anyio
async def test_widening_reports_configured_only_after_committed_trusted_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as module

    def custom_recipe(_hint: object) -> str:
        return "custom"

    def custom(*_args: object) -> PrivacySetupAnswers:
        return _answers()

    _install_setup_stubs(
        monkeypatch,
        current=local_only_policy(),
        confirmations=iter((True,)),
        prompts=[],
        external=_answers().external_provider,
    )
    monkeypatch.setattr(module, "_recipe_prompt", custom_recipe)
    monkeypatch.setattr(module, "_ask_custom_answers", custom)

    report = await module.run_privacy_setup(recipe_hint="custom")

    assert report.outcome == "configured"
    assert report.proposal_id == "pvp_1"


def test_review_warns_when_data_use_requirement_cannot_be_satisfied(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """RT-privacy-egress-2 companion: the refusal must be visible before it is committed.

    'fireworks-responses' ships owner-declared data-use, whose facts are unknown and therefore
    never recommendation-eligible. Paired with the requirement, the runtime guard refuses every
    external review, so setup has to say so while the operator can still change either side.
    """

    from yoetz.cli.privacy_setup import _render_review  # pyright: ignore[reportPrivateUsage]

    candidate = build_candidate_policy(
        local_only_policy(),
        _answers(require_current_provider_data_use_evidence=True),
        now=datetime(2026, 7, 29, tzinfo=UTC),
    )
    assert candidate.require_current_provider_data_use_evidence is True

    _render_review(candidate)

    out = capsys.readouterr().out
    assert "fireworks-responses" in out
    assert "no reviewed data-use evidence" in out
    assert "will be refused" in out


def test_review_is_silent_when_the_requirement_is_off(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from yoetz.cli.privacy_setup import _render_review  # pyright: ignore[reportPrivateUsage]

    candidate = build_candidate_policy(
        local_only_policy(),
        _answers(require_current_provider_data_use_evidence=False),
        now=datetime(2026, 7, 29, tzinfo=UTC),
    )

    _render_review(candidate)

    assert "will be refused" not in capsys.readouterr().out


def test_assisted_recipe_requires_data_use_only_where_it_can_be_satisfied() -> None:
    """The recipe default must stay dispatchable; the requirement is the operator's call.

    require_current_provider_data_use_evidence is enforced at dispatch, so setting it against an
    endpoint with unknown data-use facts yields a setup that refuses every external review. The
    recipe therefore asks for it only where the bound endpoint ships reviewed evidence.
    """

    import yoetz.cli.privacy_setup as module

    owner_declared = ProviderBinding(
        "fireworks",
        "accounts/fireworks/models/minimax-m3",
        "fireworks-responses",
        "1.0.0",
        "external",
    )
    reviewed = ProviderBinding(
        "openai",
        "gpt-5",
        OFFICIAL_OPENAI_ENDPOINT_PROFILE_ID,
        "1.0.0",
        "external",
    )

    unknown_answers = module._recipe_answers(  # pyright: ignore[reportPrivateUsage]
        "assisted_review", local_only_policy(), owner_declared
    )
    reviewed_answers = module._recipe_answers(  # pyright: ignore[reportPrivateUsage]
        "assisted_review", local_only_policy(), reviewed
    )

    assert unknown_answers.require_current_provider_data_use_evidence is False
    assert reviewed_answers.require_current_provider_data_use_evidence is True
