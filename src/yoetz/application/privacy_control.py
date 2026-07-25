"""Ordinary-control support handlers for privacy policy show/propose/tighten."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import cast

from yoetz.adapters.privacy.catalog import (
    decode_privacy_policy_canonical,
    encode_privacy_policy_json,
)
from yoetz.application.privacy_policy import (
    GetPrivacyEffectiveRequest,
    PolicyDecisionRequired,
    PrivacyPolicyApplication,
    PrivacyPolicyResult,
    ProposePrivacyPolicyRequest,
    TightenPrivacyPolicyRequest,
    privacy_get_effective,
    privacy_propose_policy,
    privacy_tighten_policy,
)
from yoetz.domain.privacy import AuthorizationScope, AuthorizationScopeKind, PrivacyPolicy
from yoetz.domain.values import JsonObject, format_rfc3339_millis, freeze_json
from yoetz.ports.control import ControlError, ControlMethod
from yoetz.ports.privacy import EffectivePrivacyPolicy, ProviderReconciliation
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.errors import ProtocolValueError

__all__ = [
    "build_privacy_support_handlers",
    "encode_effective_privacy_policy",
    "encode_privacy_policy_result",
]

type _SupportHandler = Callable[[object], Awaitable[JsonObject]]


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


def encode_effective_privacy_policy(effective: EffectivePrivacyPolicy) -> JsonObject:
    return JsonObject(
        {
            "schema_version": "1.0.0",
            "policy": encode_privacy_policy_json(effective.policy),
        }
    )


def encode_privacy_policy_result(result: PrivacyPolicyResult) -> JsonObject:
    return JsonObject(
        {
            "schema_version": "1.0.0",
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
    required: PolicyDecisionRequired, *, expected_policy_version: int
) -> JsonObject:
    proposal = required.prepared.proposal
    return JsonObject(
        {
            "schema_version": "1.0.0",
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

    async def get_effective(request: object) -> JsonObject:
        body = _as_json_object(request)
        try:
            scope = _scope_from_body(body, app.setup_scope)
            effective = await privacy_get_effective(app, GetPrivacyEffectiveRequest(scope))
        except ControlError:
            raise
        except (TypeError, ValueError) as exc:
            raise _policy_rejection() from exc
        return encode_effective_privacy_policy(effective)

    async def propose_policy(request: object) -> JsonObject:
        body = _as_json_object(request)
        try:
            digest = body["expected_policy_digest"]
            if type(digest) is not str:
                raise ControlError("invalid_request")
            candidate = _candidate_from_body(body)
            result = await privacy_propose_policy(
                app, ProposePrivacyPolicyRequest(digest, candidate)
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
                    result, expected_policy_version=current.policy.version
                )
            if type(result) is PrivacyPolicyResult:
                return encode_privacy_policy_result(result)
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

    return {
        ControlMethod.PRIVACY_GET_EFFECTIVE: get_effective,
        ControlMethod.PRIVACY_PROPOSE_POLICY: propose_policy,
        ControlMethod.PRIVACY_TIGHTEN_POLICY: tighten_policy,
    }
