"""The first-run questionnaire materializes only closed, bounded policies."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from builders.privacy_policies import local_only_policy
from yoetz.cli.privacy_setup import PrivacySetupAnswers, build_candidate_policy
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
        if channel.channel is not EgressChannel.LLM_INFERENCE
    )
    assert candidate.supersedes_policy_digest == current.policy_digest


def test_unavailable_network_channels_fail_closed() -> None:
    with pytest.raises(ValueError, match="privacy_setup_channel_unsupported"):
        build_candidate_policy(
            local_only_policy(),
            _answers(telemetry=True),
            now=datetime(2026, 7, 29, tzinfo=UTC),
        )


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

    recipe = module._recipe_prompt("metadata_only")  # pyright: ignore[reportPrivateUsage]

    assert recipe == "metadata_only"
    assert prompts == [("Choose a privacy option", "2")]


def test_recommended_metadata_policy_is_bounded_and_confirmation_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as module

    current = local_only_policy()
    external = _answers().external_provider
    monkeypatch.setattr(module, "_configured_bindings", lambda: (external, None))

    answers = module._recommended_metadata_answers(  # pyright: ignore[reportPrivateUsage]
        current
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


def test_private_recommendation_requires_no_provider() -> None:
    import yoetz.cli.privacy_setup as module

    answers = module._recommended_private_answers(  # pyright: ignore[reportPrivateUsage]
        local_only_policy()
    )

    assert answers.network_egress is False
    assert answers.external_provider is None
    assert answers.content_categories == ()
    assert answers.request_confirmation is False
    assert answers.authorization_scope is AuthorizationScopeKind.MACHINE


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


@pytest.mark.anyio
async def test_accepting_recommended_policy_skips_one_by_one_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as module

    current = local_only_policy()

    async def effective() -> PrivacyPolicy:
        return current

    def recommended(_current: PrivacyPolicy) -> PrivacySetupAnswers:
        return _answers(
            review_context=ReviewContextProfile.STRUCTURAL,
            content_categories=(
                DataCategory.BOUNDED_STRUCTURAL_METADATA,
                DataCategory.DECLARED_FILE_TYPE,
            ),
            content_data_classes=(DataClass.PUBLIC_STRUCTURAL,),
            request_confirmation=True,
            require_current_provider_data_use_evidence=False,
        )

    def forbidden_questions(*_args: object) -> PrivacySetupAnswers:
        raise AssertionError("recommended acceptance must skip detailed questions")

    prompts: list[str] = []

    def confirm(prompt: str, *, default: bool = False) -> bool:
        prompts.append(prompt)
        assert default is True
        return True

    async def propose(_candidate: PrivacyPolicy, _digest: str) -> str:
        return "pvp_1"

    async def decide(_proposal: str) -> PrivacyDecisionResult:
        return PrivacyDecisionResult("committed", "sha256:" + "c" * 64)

    def render_options(**_kwargs: object) -> None:
        return None

    def render_review(_candidate: PrivacyPolicy) -> None:
        return None

    monkeypatch.setattr(module, "_interactive_terminal", lambda: True)
    monkeypatch.setattr(module, "_effective_policy", effective)
    monkeypatch.setattr(module, "_recommended_metadata_answers", recommended)
    monkeypatch.setattr(module, "_ask_answers", forbidden_questions)
    monkeypatch.setattr(module, "_render_recipe_options", render_options)
    monkeypatch.setattr(module, "_render_review", render_review)
    monkeypatch.setattr(module.typer, "confirm", confirm)
    monkeypatch.setattr(module, "_propose", propose)
    monkeypatch.setattr(module, "_decide", decide)

    report = await module.run_privacy_setup(
        recipe_hint="metadata_only",
        offer_recommended=True,
    )

    assert report.outcome == "configured"
    assert prompts == ["Use this recommended privacy policy?"]


@pytest.mark.anyio
async def test_declining_recommended_policy_opens_one_by_one_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yoetz.cli.privacy_setup as module

    current = local_only_policy()
    detailed: list[str] = []
    confirmations = iter((False, True))

    async def effective() -> PrivacyPolicy:
        return current

    def recommended(_current: PrivacyPolicy) -> PrivacySetupAnswers:
        return _answers(
            review_context=ReviewContextProfile.STRUCTURAL,
            content_categories=(DataCategory.BOUNDED_STRUCTURAL_METADATA,),
            content_data_classes=(DataClass.PUBLIC_STRUCTURAL,),
            request_confirmation=True,
            require_current_provider_data_use_evidence=False,
        )

    def answers(recipe: object, _current: PrivacyPolicy) -> PrivacySetupAnswers:
        detailed.append(str(recipe))
        return _answers()

    async def propose(_candidate: PrivacyPolicy, _digest: str) -> str:
        return "pvp_1"

    async def decide(_proposal: str) -> PrivacyDecisionResult:
        return PrivacyDecisionResult("committed", "sha256:" + "c" * 64)

    def choose(_hint: object) -> str:
        return "custom"

    def render_options(**_kwargs: object) -> None:
        return None

    def render_review(_candidate: PrivacyPolicy) -> None:
        return None

    def confirm(_prompt: str, *, default: bool = False) -> bool:
        del default
        return next(confirmations)

    monkeypatch.setattr(module, "_interactive_terminal", lambda: True)
    monkeypatch.setattr(module, "_effective_policy", effective)
    monkeypatch.setattr(module, "_recommended_metadata_answers", recommended)
    monkeypatch.setattr(module, "_recipe_choice", choose)
    monkeypatch.setattr(module, "_ask_answers", answers)
    monkeypatch.setattr(module, "_render_recipe_options", render_options)
    monkeypatch.setattr(module, "_render_review", render_review)
    monkeypatch.setattr(module.typer, "confirm", confirm)
    monkeypatch.setattr(module, "_propose", propose)
    monkeypatch.setattr(module, "_decide", decide)

    report = await module.run_privacy_setup(
        recipe_hint="metadata_only",
        offer_recommended=True,
    )

    assert report.outcome == "configured"
    assert detailed == ["custom"]


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

    current = local_only_policy()

    async def effective() -> PrivacyPolicy:
        return current

    def recipe(_hint: object) -> str:
        return "custom"

    def answers(_recipe: object, _current: PrivacyPolicy) -> PrivacySetupAnswers:
        return _answers()

    def review(_candidate: PrivacyPolicy) -> None:
        return None

    def confirm(_prompt: str, *, default: bool = False) -> bool:
        del default
        return True

    async def propose(_candidate: PrivacyPolicy, _digest: str) -> str:
        return "pvp_1"

    async def decide(_proposal: str) -> PrivacyDecisionResult:
        return PrivacyDecisionResult("committed", "sha256:" + "c" * 64)

    monkeypatch.setattr(module, "_interactive_terminal", lambda: True)
    monkeypatch.setattr(module, "_effective_policy", effective)
    monkeypatch.setattr(module, "_recipe_prompt", recipe)
    monkeypatch.setattr(module, "_ask_answers", answers)
    monkeypatch.setattr(module, "_render_review", review)
    monkeypatch.setattr(module.typer, "confirm", confirm)
    monkeypatch.setattr(module, "_propose", propose)
    monkeypatch.setattr(module, "_decide", decide)

    report = await module.run_privacy_setup(recipe_hint="custom")

    assert report.outcome == "configured"
    assert report.proposal_id == "pvp_1"
