"""Trusted interactive privacy setup used by first run and ``privacy setup``.

The recommended-first path renders one exact bounded policy and asks one question.
Only declining it opens all thirteen expert settings.  Both paths show the exact
resulting disclosure boundary and approve any widening through the existing locally
reauthenticated privacy ceremony.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final, Literal, cast

import typer

from yoetz.adapters.privacy.catalog import (
    decode_privacy_policy_canonical,
    encode_privacy_policy_json,
)
from yoetz.domain.privacy import (
    AuthorizationScopeKind,
    ChannelPolicy,
    DataClass,
    EgressChannel,
    PrivacyPolicy,
    PrivacyProfile,
    ProviderBinding,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.domain.values import JsonObject
from yoetz.protocol.canonical import JsonValue, canonical_digest, canonical_encode
from yoetz.protocol.models import DataCategory

__all__ = ["PrivacySetupReport", "build_candidate_policy", "run_privacy_setup"]

type PrivacyRecipe = Literal[
    "private", "metadata_only", "assisted_review", "expanded_review", "custom"
]

_SEMANTIC_CATEGORIES: Final = (
    DataCategory.BOUNDED_STRUCTURAL_METADATA,
    DataCategory.DECLARED_FILE_TYPE,
    DataCategory.TASK_DESCRIPTION,
    DataCategory.CLAIM_TEXT,
    DataCategory.OBLIGATION_TEXT,
    DataCategory.DECISION_EXCERPT,
    DataCategory.EVIDENCE_EXCERPT,
    DataCategory.FINDING_SUMMARY,
    DataCategory.COMMAND_METADATA,
    DataCategory.DIFF_METADATA,
    DataCategory.REPOSITORY_EXCERPT,
)
_AGENT_CATEGORIES: Final = tuple(DataCategory)
_UNSUPPORTED_CHANNELS: Final = (
    EgressChannel.PRODUCT_TELEMETRY,
    EgressChannel.CRASH_DIAGNOSTICS,
    EgressChannel.UPDATE_CHECKS,
    EgressChannel.CAPABILITY_TESTING,
)
_RECIPE_DEFAULTS: Final = {
    "private": "1",
    "metadata_only": "2",
    "assisted_review": "3",
    "expanded_review": "4",
    "custom": "5",
}
_RECIPE_CHOICES: Final = {
    "1": "private",
    "2": "metadata_only",
    "3": "assisted_review",
    "4": "expanded_review",
    "5": "custom",
}


@dataclass(frozen=True, slots=True)
class PrivacySetupAnswers:
    network_egress: bool
    local_models: bool
    external_provider: ProviderBinding | None
    require_current_provider_data_use_evidence: bool
    local_model_binding: ProviderBinding | None
    review_context: ReviewContextProfile
    content_categories: tuple[DataCategory, ...]
    content_data_classes: tuple[DataClass, ...]
    agent_context_categories: tuple[DataCategory, ...]
    agent_context_data_classes: tuple[DataClass, ...]
    local_model_categories: tuple[DataCategory, ...]
    local_model_data_classes: tuple[DataClass, ...]
    request_confirmation: bool
    telemetry: bool
    crash_diagnostics: bool
    updates: bool
    capability_testing: bool
    authorization_scope: AuthorizationScopeKind


@dataclass(frozen=True, slots=True)
class PrivacySetupReport:
    outcome: Literal["configured", "unchanged", "cancelled", "failed"]
    profile: str
    proposal_id: str | None = None
    reason: str | None = None


def _disabled_channel(channel: EgressChannel) -> ChannelPolicy:
    return ChannelPolicy(
        channel,
        False,
        (),
        (),
        None,
        (),
        AuthorizationScopeKind.MACHINE,
        False,
        0,
        0,
        0,
    )


def _ordered_channels(
    channels: dict[EgressChannel, ChannelPolicy],
) -> tuple[ChannelPolicy, ...]:
    return tuple(
        channels[channel] for channel in sorted(EgressChannel, key=lambda item: item.value)
    )


def build_candidate_policy(
    current: PrivacyPolicy,
    answers: PrivacySetupAnswers,
    *,
    now: datetime,
) -> PrivacyPolicy:
    """Materialize one reviewed questionnaire into the closed policy type."""

    if type(current) is not PrivacyPolicy or type(answers) is not PrivacySetupAnswers:
        raise TypeError("privacy_setup_candidate_invalid")
    now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
    if any(
        (
            answers.telemetry,
            answers.crash_diagnostics,
            answers.updates,
            answers.capability_testing,
        )
    ):
        raise ValueError("privacy_setup_channel_unsupported")
    if answers.local_models != (answers.local_model_binding is not None):
        raise ValueError("privacy_setup_local_model_binding_required")
    if not answers.local_models and (
        answers.local_model_categories or answers.local_model_data_classes
    ):
        raise ValueError("privacy_setup_local_model_binding_required")
    if answers.network_egress != (answers.external_provider is not None):
        raise ValueError("privacy_setup_provider_binding_required")

    channels = {channel: _disabled_channel(channel) for channel in EgressChannel}
    if answers.network_egress:
        profile = (
            PrivacyProfile.CONFIRM_EVERY_REQUEST
            if answers.request_confirmation
            else (
                PrivacyProfile.TRUSTED_PROVIDER
                if answers.require_current_provider_data_use_evidence
                or answers.review_context is ReviewContextProfile.EXPANDED
                else PrivacyProfile.MINIMAL_EXTERNAL
            )
        )
        channels[EgressChannel.LLM_INFERENCE] = ChannelPolicy(
            EgressChannel.LLM_INFERENCE,
            True,
            answers.content_categories,
            answers.content_data_classes,
            answers.external_provider,
            ("semantic-review",),
            answers.authorization_scope,
            answers.request_confirmation,
            256 * 1024,
            4096,
            300,
        )
    else:
        profile = PrivacyProfile.LOCAL_ONLY

    placeholder = PrivacyPolicy(
        current.policy_id,
        current.version + 1,
        "sha256:" + "0" * 64,
        profile,
        answers.review_context,
        ReviewSelectionPolicy.for_profile(answers.review_context),
        answers.require_current_provider_data_use_evidence,
        answers.network_egress,
        current.effective_scope,
        _ordered_channels(channels),
        answers.local_models,
        answers.local_model_binding,
        answers.local_model_categories,
        answers.local_model_data_classes,
        answers.agent_context_categories,
        answers.agent_context_data_classes,
        current.trusted_human_control_categories,
        current.trusted_human_control_data_classes,
        now,
        current.policy_digest,
    )
    identity = encode_privacy_policy_json(placeholder)
    identity.pop("policy_digest")
    return replace(placeholder, policy_digest=canonical_digest(cast(JsonValue, identity)))


_IDENTITY_EXCLUDED_KEYS: Final = frozenset(
    {"policy_digest", "version", "created_at", "supersedes_policy_digest"}
)


def _substantive_identity(policy: PrivacyPolicy) -> str:
    """Digest only the disclosure boundary, ignoring lineage and issue time.

    A candidate always carries a fresh version, ``created_at``, and supersedes link, so the
    policy digest alone can never answer "did the human actually change anything?".
    """

    encoded = encode_privacy_policy_json(policy)
    identity = {key: value for key, value in encoded.items() if key not in _IDENTITY_EXCLUDED_KEYS}
    return canonical_digest(cast(JsonValue, identity))


def _interactive_terminal() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except OSError, ValueError:
        return False


def _render_recipe_options(*, recommended: PrivacyRecipe | None = None) -> None:
    typer.echo("Privacy options:")
    typer.echo(
        "  1. Private"
        + (" (recommended)" if recommended == "private" else "")
        + " — maximum confidentiality; no network egress or external semantic review"
    )
    typer.echo(
        "  2. Metadata only"
        + (" (recommended)" if recommended == "metadata_only" else "")
        + " — strongest semantic privacy; structural metadata only and approval every request"
    )
    typer.echo(
        "  3. Assisted review — better problem-specific feedback from bounded excerpts; "
        "requires eligible provider data-use evidence"
    )
    typer.echo(
        "  4. Expanded review — most reviewer context and detail; allows broader in-scope excerpts"
    )
    typer.echo("  5. Custom — maximum control; review each privacy setting one by one")
    typer.echo("Credentials, secrets, complete transcripts, and unrelated files are never sent.")
    typer.echo("You can change this any time by running 'yoetz --privacy'.")


def _recipe_choice(hint: PrivacyRecipe | None) -> PrivacyRecipe:
    while True:
        raw = typer.prompt(
            "Choose a privacy option",
            default=_RECIPE_DEFAULTS.get(hint or "private", "1"),
        ).strip()
        choice = cast(PrivacyRecipe | None, _RECIPE_CHOICES.get(raw))
        if choice is not None:
            return choice
        typer.echo("Please enter 1, 2, 3, 4, or 5.")


def _recipe_prompt(hint: PrivacyRecipe | None) -> PrivacyRecipe:
    _render_recipe_options()
    return _recipe_choice(hint)


def _recommended_metadata_answers(current: PrivacyPolicy) -> PrivacySetupAnswers:
    external, _local = _configured_bindings()
    if external is None:
        raise ValueError("privacy_setup_provider_binding_required")
    return PrivacySetupAnswers(
        network_egress=True,
        local_models=False,
        external_provider=external,
        require_current_provider_data_use_evidence=False,
        local_model_binding=None,
        review_context=ReviewContextProfile.STRUCTURAL,
        content_categories=(
            DataCategory.BOUNDED_STRUCTURAL_METADATA,
            DataCategory.DECLARED_FILE_TYPE,
        ),
        content_data_classes=(DataClass.PUBLIC_STRUCTURAL,),
        agent_context_categories=(
            current.agent_context_categories
            or (
                DataCategory.BOUNDED_STRUCTURAL_METADATA,
                DataCategory.DECLARED_FILE_TYPE,
            )
        ),
        agent_context_data_classes=(
            current.agent_context_data_classes or (DataClass.PUBLIC_STRUCTURAL,)
        ),
        local_model_categories=(),
        local_model_data_classes=(),
        request_confirmation=True,
        telemetry=False,
        crash_diagnostics=False,
        updates=False,
        capability_testing=False,
        authorization_scope=AuthorizationScopeKind.TASK,
    )


def _recommended_private_answers(current: PrivacyPolicy) -> PrivacySetupAnswers:
    return PrivacySetupAnswers(
        network_egress=False,
        local_models=False,
        external_provider=None,
        require_current_provider_data_use_evidence=False,
        local_model_binding=None,
        review_context=ReviewContextProfile.STRUCTURAL,
        content_categories=(),
        content_data_classes=(DataClass.PUBLIC_STRUCTURAL,),
        agent_context_categories=(
            current.agent_context_categories
            or (
                DataCategory.BOUNDED_STRUCTURAL_METADATA,
                DataCategory.DECLARED_FILE_TYPE,
            )
        ),
        agent_context_data_classes=(
            current.agent_context_data_classes or (DataClass.PUBLIC_STRUCTURAL,)
        ),
        local_model_categories=(),
        local_model_data_classes=(),
        request_confirmation=False,
        telemetry=False,
        crash_diagnostics=False,
        updates=False,
        capability_testing=False,
        authorization_scope=AuthorizationScopeKind.MACHINE,
    )


def _review_context_prompt(default: ReviewContextProfile) -> ReviewContextProfile:
    typer.echo("4/13 Review context: structural, goal_aware, assisted, or expanded")
    while True:
        raw = typer.prompt("Review context", default=default.value).strip()
        try:
            review = ReviewContextProfile(raw)
        except ValueError:
            typer.echo("Choose structural, goal_aware, assisted, or expanded.")
            continue
        if review is ReviewContextProfile.CUSTOM:
            typer.echo("Custom selectors use desired-state TOML; choose a listed context here.")
            continue
        return review


def _authorization_scope_prompt(default: str) -> AuthorizationScopeKind:
    while True:
        raw = typer.prompt(
            "13/13 Authorization ceiling (request, task, workspace, machine)",
            default=default,
        ).strip()
        try:
            return AuthorizationScopeKind(raw)
        except ValueError:
            typer.echo("Choose request, task, workspace, or machine.")


def _categories_prompt(
    number: int,
    label: str,
    default: tuple[DataCategory, ...],
) -> tuple[DataCategory, ...]:
    raw = typer.prompt(
        f"{number}/13a {label} (comma separated)",
        default=",".join(item.value for item in default),
    ).strip()
    if not raw:
        return ()
    try:
        values = tuple(DataCategory(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("privacy_setup_category_invalid") from exc
    if len(set(values)) != len(values):
        raise ValueError("privacy_setup_category_invalid")
    return values


def _data_classes_prompt(
    number: int,
    label: str,
    default: tuple[DataClass, ...],
) -> tuple[DataClass, ...]:
    allowed = {
        DataClass.PUBLIC_STRUCTURAL,
        DataClass.ORDINARY_USER_CONTENT,
        DataClass.SENSITIVE_CONFIDENTIAL,
    }
    raw = typer.prompt(
        f"{number}/13b {label} data classes (comma separated)",
        default=",".join(item.value for item in default),
    ).strip()
    if not raw:
        return ()
    try:
        values = tuple(DataClass(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("privacy_setup_data_class_invalid") from exc
    if len(set(values)) != len(values) or not set(values) <= allowed:
        raise ValueError("privacy_setup_data_class_invalid")
    return values


def _configured_bindings() -> tuple[ProviderBinding | None, ProviderBinding | None]:
    from yoetz.config.load import load_config

    config = load_config({}, os.environ, None)
    external = (
        None
        if config.provider is None
        else ProviderBinding(
            config.provider.provider_id,
            config.provider.model,
            config.provider.endpoint_profile_id,
            config.provider.endpoint_profile_version,
            "external",
        )
    )
    local = (
        None
        if config.local_model is None
        else ProviderBinding(
            config.local_model.profile_id,
            config.local_model.model,
            config.local_model.endpoint_profile_id,
            config.local_model.endpoint_profile_version,
            "local_af_unix",
        )
    )
    return external, local


def _ask_answers(recipe: PrivacyRecipe, current: PrivacyPolicy) -> PrivacySetupAnswers:
    external, local = _configured_bindings()
    semantic = recipe != "private"
    context = {
        "private": ReviewContextProfile.STRUCTURAL,
        "metadata_only": ReviewContextProfile.STRUCTURAL,
        "assisted_review": ReviewContextProfile.ASSISTED,
        "expanded_review": ReviewContextProfile.EXPANDED,
        "custom": ReviewContextProfile.ASSISTED,
    }[recipe]
    categories = (
        (DataCategory.BOUNDED_STRUCTURAL_METADATA, DataCategory.DECLARED_FILE_TYPE)
        if recipe == "metadata_only"
        else (() if recipe == "private" else _SEMANTIC_CATEGORIES)
    )

    network = typer.confirm("1/13 Permit network egress?", default=semantic)
    local_models = typer.confirm(
        "2/13 Permit configured local-model processing?",
        default=local is not None and current.local_model_enabled,
    )
    provider_label = (
        "none configured" if external is None else f"{external.provider_id}/{external.model_id}"
    )
    use_provider = typer.confirm(
        f"3/13a Bind external semantic review to {provider_label}?",
        default=network and external is not None,
    )
    require_evidence = False
    if use_provider:
        require_evidence = typer.confirm(
            "3/13b Require a current eligible provider data-use record?",
            default=recipe == "assisted_review",
        )
    review = _review_context_prompt(context)
    content = _categories_prompt(5, "External content categories", tuple(categories))
    content_classes = _data_classes_prompt(
        5,
        "External content",
        (
            (DataClass.PUBLIC_STRUCTURAL,)
            if recipe in {"private", "metadata_only"}
            else (DataClass.PUBLIC_STRUCTURAL, DataClass.ORDINARY_USER_CONTENT)
        ),
    )
    agent = _categories_prompt(
        6,
        "Local agent-context categories",
        current.agent_context_categories or _AGENT_CATEGORIES,
    )
    agent_classes = _data_classes_prompt(
        6,
        "Local agent context",
        current.agent_context_data_classes,
    )
    local_categories = _categories_prompt(
        7,
        "Local-model categories",
        current.local_model_categories if local_models else (),
    )
    local_classes = _data_classes_prompt(
        7,
        "Local model",
        current.local_model_data_classes if local_models else (),
    )
    confirmation = typer.confirm(
        "8/13 Require confirmation before every provider request?",
        default=recipe == "metadata_only",
    )
    telemetry = typer.confirm(
        "9/13 Enable product telemetry? (unsupported; stays off)", default=False
    )
    crash = typer.confirm("10/13 Enable crash diagnostics? (unsupported; stays off)", default=False)
    updates = typer.confirm(
        "11/13 Enable network update checks? (unsupported; stays off)", default=False
    )
    capability = typer.confirm(
        "12/13 Enable external capability testing? (unsupported; stays off)", default=False
    )
    scope = _authorization_scope_prompt(
        "workspace"
        if recipe in {"assisted_review", "expanded_review"}
        else ("task" if semantic else "machine")
    )

    if network and not use_provider:
        raise ValueError("privacy_setup_provider_binding_required")
    if local_models and local is None:
        raise ValueError("privacy_setup_local_model_binding_required")
    return PrivacySetupAnswers(
        network,
        local_models,
        external if use_provider else None,
        require_evidence,
        local if local_models else None,
        review,
        content,
        content_classes,
        agent,
        agent_classes,
        local_categories,
        local_classes,
        confirmation,
        telemetry,
        crash,
        updates,
        capability,
        scope,
    )


def _render_review(candidate: PrivacyPolicy) -> None:
    llm = next(
        policy
        for policy in candidate.channel_policies
        if policy.channel is EgressChannel.LLM_INFERENCE
    )
    typer.echo("")
    typer.echo("Exact privacy draft:")
    typer.echo(f"  Profile: {candidate.profile.value}")
    typer.echo(f"  Network egress: {'allowed' if candidate.network_egress_permitted else 'off'}")
    typer.echo(f"  LLM inference: {'enabled' if llm.enabled else 'off'}")
    if llm.provider_binding is not None:
        typer.echo(
            "  Destination: "
            f"{llm.provider_binding.provider_id}/{llm.provider_binding.model_id} "
            f"via {llm.provider_binding.endpoint_profile_id}@"
            f"{llm.provider_binding.endpoint_profile_version}"
        )
    typer.echo(
        "  Allowed categories: "
        + (", ".join(item.value for item in llm.allowed_categories) or "none")
    )
    typer.echo(
        "  Allowed data classes: "
        + (", ".join(item.value for item in llm.allowed_data_classes) or "none")
    )
    typer.echo(f"  Authorization ceiling: {llm.scope_ceiling.value}")
    typer.echo(f"  Per-request confirmation: {'yes' if llm.preview_required else 'no'}")
    typer.echo(
        "  Current provider data-use evidence required: "
        + ("yes" if candidate.require_current_provider_data_use_evidence else "no")
    )
    typer.echo("  Maximum: 16 KiB per excerpt; 256 KiB / 4096 tokens per case")
    typer.echo(
        "  Never sent: credentials, encryption material, environment variables, "
        "complete transcripts, unrelated or out-of-scope files"
    )
    typer.echo("  Telemetry, crash diagnostics, update checks, capability testing: off")


async def _effective_policy() -> PrivacyPolicy:
    from yoetz.cli.app import build_service_client
    from yoetz.cli.provider_status import machine_scope_request

    client = await build_service_client()
    try:
        raw = await client.privacy_get_effective(machine_scope_request())
    finally:
        await client.close()
    plain = cast(dict[str, JsonValue], dict(raw))
    policy = plain.get("policy")
    if not isinstance(policy, Mapping):
        raise ValueError("privacy_setup_effective_unavailable")
    return decode_privacy_policy_canonical(canonical_encode(cast(JsonValue, policy)))


async def _propose(candidate: PrivacyPolicy, expected_digest: str) -> str | None:
    from yoetz.adapters.privacy.catalog import encode_privacy_policy_json
    from yoetz.cli.app import build_service_client

    client = await build_service_client()
    try:
        result = await client.privacy_propose_policy(
            JsonObject(
                {
                    # Required by the frozen privacy_propose_policy body. Without it the frame
                    # fails validation before it leaves this process, and the whole setup flow
                    # reports a closed invalid_request that names nothing.
                    "schema_version": "1.0.0",
                    "expected_policy_digest": expected_digest,
                    "candidate_policy": encode_privacy_policy_json(candidate),
                }
            )
        )
    finally:
        await client.close()
    proposal_id = result.get("proposal_id")
    if result.get("outcome") == "decision_required" and type(proposal_id) is str:
        return proposal_id
    if result.get("outcome") == "tightening_applied":
        return None
    raise ValueError("privacy_setup_proposal_invalid")


async def _decide(proposal_id: str) -> object:
    from yoetz.adapters.keys.os_keyring import AutoUnlockPassphraseStore
    from yoetz.cli.privacy_control import (
        decide_policy,
        decide_policy_with_local_reauthentication,
    )
    from yoetz.config.load import load_config
    from yoetz.config.paths import bundle_root

    config = load_config({}, os.environ, None)
    passphrase = AutoUnlockPassphraseStore(bundle_root(_data_dir=config.storage.data_dir)).load()
    if passphrase is None:
        return await decide_policy(proposal_id)
    return await decide_policy_with_local_reauthentication(proposal_id, passphrase)


async def run_privacy_setup(
    *,
    recipe_hint: PrivacyRecipe | None = None,
    offer_recommended: bool = False,
) -> PrivacySetupReport:
    """Run the trusted questionnaire and apply only an explicitly approved draft."""

    if not _interactive_terminal():
        return PrivacySetupReport(
            "failed",
            "unknown",
            reason="local_terminal_required",
        )
    current = await _effective_policy()
    if offer_recommended:
        recommended_recipe: PrivacyRecipe = "metadata_only"
        try:
            recommended_answers = _recommended_metadata_answers(current)
        except ValueError as error:
            if str(error) != "privacy_setup_provider_binding_required":
                return PrivacySetupReport("failed", current.profile.value, reason=str(error))
            recommended_recipe = "private"
            recommended_answers = _recommended_private_answers(current)
        _render_recipe_options(recommended=recommended_recipe)
        typer.echo("")
        if recommended_recipe == "metadata_only":
            typer.echo("Recommended policy: Metadata only")
            typer.echo(
                "Why: it enables semantic review while minimizing disclosure and asks before "
                "every provider request."
            )
        else:
            typer.echo("Recommended policy: Private")
            typer.echo("Why: no external provider is configured, so this keeps network egress off.")
        candidate = build_candidate_policy(
            current,
            recommended_answers,
            now=datetime.now(UTC),
        )
        _render_review(candidate)
        if not typer.confirm("Use this recommended privacy policy?", default=True):
            typer.echo("")
            typer.echo("Customize privacy one setting at a time.")
            recipe = _recipe_choice(recommended_recipe)
            try:
                answers = _ask_answers(recipe, current)
                candidate = build_candidate_policy(current, answers, now=datetime.now(UTC))
            except ValueError as error:
                return PrivacySetupReport("failed", current.profile.value, reason=str(error))
            _render_review(candidate)
            if not typer.confirm("Use this exact custom privacy policy?", default=False):
                return PrivacySetupReport("cancelled", current.profile.value)
    else:
        recipe = _recipe_prompt(recipe_hint)
        try:
            answers = _ask_answers(recipe, current)
            candidate = build_candidate_policy(current, answers, now=datetime.now(UTC))
        except ValueError as error:
            return PrivacySetupReport("failed", current.profile.value, reason=str(error))
        _render_review(candidate)
        if not typer.confirm(
            "Create this exact privacy proposal?",
            default=False,
        ):
            return PrivacySetupReport("cancelled", current.profile.value)
    if _substantive_identity(candidate) == _substantive_identity(current):
        return PrivacySetupReport("unchanged", current.profile.value)
    proposal_id = await _propose(candidate, current.policy_digest)
    if proposal_id is None:
        return PrivacySetupReport("configured", candidate.profile.value)
    typer.echo("")
    typer.echo("Final widening decision (trusted local ceremony)")
    decision = await _decide(proposal_id)
    status = getattr(decision, "status", None)
    if status == "committed":
        return PrivacySetupReport("configured", candidate.profile.value, proposal_id)
    return PrivacySetupReport(
        "cancelled",
        current.profile.value,
        proposal_id,
        "privacy_decision_not_approved" if status != "stale" else "privacy_proposal_stale",
    )
