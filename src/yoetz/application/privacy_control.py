"""Ordinary-control support handlers for privacy policy show/propose/tighten."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import cast

from yoetz.adapters.privacy.catalog import (
    decode_privacy_policy_canonical,
    encode_privacy_policy_json,
)
from yoetz.application.privacy_policy import (
    GetPrivacyEffectiveRequest,
    GetPrivacySetupRequest,
    PolicyDecisionRequired,
    PrivacyPolicyApplication,
    PrivacyPolicyResult,
    ProposePrivacyPolicyRequest,
    TightenPrivacyPolicyRequest,
    privacy_get_effective,
    privacy_get_setup,
    privacy_pending_list,
    privacy_propose_policy,
    privacy_tighten_policy,
)
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    PrivacyPolicy,
    ReviewSelectionPolicy,
)
from yoetz.domain.values import JsonObject, format_rfc3339_millis, freeze_json
from yoetz.ports.control import ControlError, ControlMethod, RepositoryPrivacyContext
from yoetz.ports.privacy import EffectivePrivacyPolicy, ProviderReconciliation
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "build_privacy_support_handlers",
    "encode_effective_privacy_policy",
    "encode_privacy_policy_result",
]

type _SupportHandler = Callable[..., Awaitable[JsonObject]]


def _repository_scope(
    app: PrivacyPolicyApplication, context: RepositoryPrivacyContext | None
) -> AuthorizationScope:
    if context is None:
        raise ControlError("invalid_request")
    return AuthorizationScope(
        AuthorizationScopeKind.WORKSPACE,
        app.setup_scope.installation_id,
        context.commitment,
    )


def _as_json_object(request: object) -> JsonObject:
    try:
        normalized = freeze_json(request)
    except ProtocolValueError as exc:
        raise ControlError("invalid_request") from exc
    if type(normalized) is not JsonObject:
        raise ControlError("invalid_request")
    return normalized


def _provider_reconciliation_to_wire(value: ProviderReconciliation) -> dict[str, JsonValue]:
    """Encode reconciliation as the frozen control-result shape.

    The domain type is deliberately wider than the wire contract: it names the generation
    ``policy_generation`` and pairs each unavailable binding digest with an internal reason.
    The reviewed schema carries a decimal-string ``policy_version`` and digests only, so this
    is written out by hand rather than reflected off the dataclass.
    """

    digests: list[JsonValue] = []
    for digest, _reason in value.unavailable_bindings:
        if digest not in digests:
            digests.append(digest)
    return {
        "policy_version": str(value.policy_generation),
        "activated_count": value.activated_count,
        "deactivated_count": value.deactivated_count,
        "unavailable_binding_digests": digests,
    }


def _review_selection_to_wire(value: object) -> JsonValue:
    if type(value) is not ReviewSelectionPolicy:
        raise TypeError("review_selection_invalid")
    return cast(
        JsonValue,
        {
            "sections": list(value.sections),
            "excerpt_kinds": list(value.excerpt_kinds),
            "relevance": value.relevance,
            "include_finding_prose": value.include_finding_prose,
            "include_exact_command_text": value.include_exact_command_text,
            "max_timeline_items": value.max_timeline_items,
            "max_assessments": value.max_assessments,
            "max_change_observations": value.max_change_observations,
            "max_excerpts": value.max_excerpts,
            "max_omissions": value.max_omissions,
            "max_excerpt_bytes": value.max_excerpt_bytes,
            "max_total_excerpt_bytes": value.max_total_excerpt_bytes,
        },
    )


def encode_effective_privacy_policy(effective: EffectivePrivacyPolicy) -> JsonObject:
    return JsonObject(
        {
            "schema_version": "1.0.0",
            "policy": encode_privacy_policy_json(effective.policy),
        }
    )


def encode_privacy_policy_result(
    result: PrivacyPolicyResult, *, schema_version: str = "1.0.0"
) -> JsonObject:
    return JsonObject(
        {
            "schema_version": schema_version,
            "outcome": "tightening_applied",
            "policy": encode_privacy_policy_json(result.policy),
            "revoked_authorization_count": result.revoked_authorization_count,
            "closed_session_count": result.closed_session_count,
            "provider_reconciliation": _provider_reconciliation_to_wire(
                result.provider_reconciliation
            ),
        }
    )


def _encode_decision_required(
    required: PolicyDecisionRequired,
    *,
    expected_policy_version: int,
    schema_version: str = "1.0.0",
) -> JsonObject:
    proposal = required.prepared.proposal
    return JsonObject(
        {
            "schema_version": schema_version,
            "outcome": "decision_required",
            "proposal_id": required.privacy_proposal_id,
            "proposal_digest": proposal.proposal_digest,
            "candidate_policy_digest": proposal.proposed_policy.policy_digest,
            "expected_policy_version": str(expected_policy_version),
            "expires_at": format_rfc3339_millis(proposal.expires_at),
        }
    )


def _scope_from_body(body: JsonObject, default: AuthorizationScope) -> AuthorizationScope:
    raw = body.get("scope")
    if raw is None:
        return default
    if type(raw) is not JsonObject:
        raise ControlError("invalid_request")
    if len(raw) == 0:
        return default
    try:
        return AuthorizationScope(
            AuthorizationScopeKind(cast(str, raw["kind"])),
            cast(str, raw["installation_id"]),
            cast(str | None, raw.get("workspace_ref_commitment")),
            cast(str | None, raw.get("task_id")),
            cast(str | None, raw.get("request_id")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ControlError("invalid_request") from exc


def _candidate_from_body(body: JsonObject) -> PrivacyPolicy:
    raw = body.get("candidate_policy")
    if raw is None:
        raise ControlError("invalid_request")
    try:
        return decode_privacy_policy_canonical(canonical_encode(cast(JsonValue, raw)))
    except (TypeError, ValueError, ProtocolValueError) as exc:
        raise ControlError("invalid_request") from exc


def _policy_rejection() -> ControlError:
    """Every policy-layer rejection is a caller-fixable, non-retryable request error.

    The application raises bounded reason tokens (``privacy_policy_stale``,
    ``privacy_authority_required``, …) but the control envelope carries no free-form detail,
    so they collapse to one closed reason rather than leaking the internal token.
    """

    return ControlError("invalid_request", retryable=False)


def build_privacy_support_handlers(
    app: PrivacyPolicyApplication,
) -> Mapping[ControlMethod, _SupportHandler]:
    """Bind privacy_* ordinary-control methods to one PrivacyPolicyApplication."""

    async def get_setup(
        request: object,
        *,
        repository_privacy_context: RepositoryPrivacyContext | None = None,
    ) -> JsonObject:
        body = _as_json_object(request)
        if body.get("schema_version") != "2.0.0":
            raise ControlError("invalid_request")
        scope = _repository_scope(app, repository_privacy_context)
        now = app.clock.now_utc()
        try:
            view = await privacy_get_setup(
                app,
                GetPrivacySetupRequest(
                    "repository_setup",
                    "begin",
                    0,
                    now + timedelta(seconds=app.proposal_ttl_seconds),
                    True,
                    repository_scope=scope,
                ),
            )
        except (TypeError, ValueError) as exc:
            raise _policy_rejection() from exc
        authority = view.authority
        if authority is None:
            raise _policy_rejection()
        return JsonObject(
            {
                "schema_version": "2.0.0",
                "composed_policy": encode_privacy_policy_json(view.effective.policy),
                "bound_scope": cast(
                    JsonValue,
                    {
                        "kind": scope.kind.value,
                        "installation_id": scope.installation_id,
                        "workspace_ref_commitment": scope.workspace_ref_commitment,
                    },
                ),
                "authority_digest": authority.authority_digest,
                "grant_state": authority.grant_state,
                "migration_state": authority.migration_state,
                "channel_choices": cast(
                    JsonValue,
                    [
                        {
                            "channel": choice.channel.value,
                            "enabled": choice.enabled,
                            "capability_state": choice.capability_state,
                        }
                        for choice in view.channel_choices
                    ],
                ),
                "allowed_blocked_examples": cast(
                    JsonValue,
                    [
                        {"code": item.code, "allowed": item.allowed}
                        for item in view.allowed_blocked_examples
                    ],
                ),
                "recipes": cast(
                    JsonValue,
                    [
                        {
                            "recipe": recipe.recipe,
                            "privacy_profile": recipe.privacy_profile.value,
                            "review_context_profile": recipe.review_context_profile.value,
                            "review_selection": _review_selection_to_wire(recipe.review_selection),
                        }
                        for recipe in view.recipes
                    ],
                ),
                "never_send_editable": False,
            }
        )

    async def get_effective(
        request: object,
        *,
        repository_privacy_context: RepositoryPrivacyContext | None = None,
    ) -> JsonObject:
        body = _as_json_object(request)
        try:
            is_v2 = body.get("schema_version") == "2.0.0"
            if is_v2:
                scope = _repository_scope(app, repository_privacy_context)
                authority = await app.policy_store.repository_authority(scope)
                effective = authority.effective
            else:
                scope = _scope_from_body(body, app.setup_scope)
                effective = await privacy_get_effective(app, GetPrivacyEffectiveRequest(scope))
        except ControlError:
            raise
        except (TypeError, ValueError) as exc:
            raise _policy_rejection() from exc
        return encode_effective_privacy_policy(effective)

    async def propose_policy(
        request: object,
        *,
        repository_privacy_context: RepositoryPrivacyContext | None = None,
    ) -> JsonObject:
        body = _as_json_object(request)
        try:
            is_v2 = body.get("schema_version") == "2.0.0"
            if is_v2:
                scope = _repository_scope(app, repository_privacy_context)
                digest = body["authority_digest"]
                if type(digest) is not str:
                    raise ControlError("invalid_request")
                authority = await app.policy_store.repository_authority(scope)
                expected_policy_digest = authority.effective.effective_digest
            else:
                scope = None
                digest = body["expected_policy_digest"]
                if type(digest) is not str:
                    raise ControlError("invalid_request")
                expected_policy_digest = digest
            if type(digest) is not str:
                raise ControlError("invalid_request")
            candidate = _candidate_from_body(body)
            if not is_v2 and candidate.effective_scope.kind is not AuthorizationScopeKind.MACHINE:
                raise ControlError("invalid_request")
            result = await privacy_propose_policy(
                app,
                ProposePrivacyPolicyRequest(
                    expected_policy_digest,
                    candidate,
                    digest if scope is not None else None,
                    scope,
                ),
            )
            if type(result) is PolicyDecisionRequired:
                proposal = result.prepared.proposal
                current = await app.policy_store.effective_policy(proposal.scope)
                # This is a second read; a concurrent commit in between would otherwise report
                # a version belonging to a different policy than the one just prepared. The
                # proposal pins the generation it was prepared against, so disagreement means
                # the proposal is already stale and the caller must re-read and retry.
                if current.generation != proposal.expected_generation or (
                    proposal.expected_policy_digest is not None
                    and current.effective_digest != proposal.expected_policy_digest
                ):
                    raise _policy_rejection()
                return _encode_decision_required(
                    result,
                    expected_policy_version=current.policy.version,
                    schema_version="2.0.0" if is_v2 else "1.0.0",
                )
            if type(result) is PrivacyPolicyResult:
                return encode_privacy_policy_result(
                    result, schema_version="2.0.0" if is_v2 else "1.0.0"
                )
            raise ControlError("invalid_request")
        except ControlError:
            raise
        except KeyError as exc:
            raise ControlError("invalid_request") from exc
        except (TypeError, ValueError) as exc:
            raise _policy_rejection() from exc

    async def tighten_policy(request: object) -> JsonObject:
        body = _as_json_object(request)
        try:
            digest = body["expected_policy_digest"]
            if type(digest) is not str:
                raise ControlError("invalid_request")
            candidate = _candidate_from_body(body)
            if candidate.effective_scope.kind is not AuthorizationScopeKind.MACHINE:
                raise ControlError("invalid_request")
            result = await privacy_tighten_policy(
                app, TightenPrivacyPolicyRequest(digest, candidate)
            )
        except ControlError:
            raise
        except KeyError as exc:
            raise ControlError("invalid_request") from exc
        except (TypeError, ValueError) as exc:
            raise _policy_rejection() from exc
        return encode_privacy_policy_result(result)

    async def pending_list(request: object) -> JsonObject:
        # Takes no parameters: a pending decision is open or it is not. Any body is accepted and
        # ignored rather than rejected, so a future caller adding a field cannot be told its
        # request was invalid when nothing about the answer would have changed.
        del request
        try:
            page = await privacy_pending_list(app)
        except ControlError:
            raise
        except (TypeError, ValueError) as exc:
            raise _policy_rejection() from exc
        return JsonObject(
            {
                "schema": "yoetz.privacy-pending-page/1",
                "snapshot_generation": page.snapshot_generation,
                "pending": [
                    {
                        "pending_id": entry.pending_id,
                        "task_id": entry.task_id,
                        "expires_at": format_rfc3339_millis(entry.expires_at),
                    }
                    for entry in page.pending
                ],
            }
        )

    return {
        ControlMethod.PRIVACY_GET_EFFECTIVE: get_effective,
        ControlMethod.PRIVACY_GET_SETUP: get_setup,
        ControlMethod.PRIVACY_PENDING_LIST: pending_list,
        ControlMethod.PRIVACY_PROPOSE_POLICY: propose_policy,
        ControlMethod.PRIVACY_TIGHTEN_POLICY: tighten_policy,
    }
