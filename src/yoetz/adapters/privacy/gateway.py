"""The sole authorization-fenced outbound and local-model semantic gateway.

``PolicyEnforcingOutboundGateway`` is the only component permitted to own semantic provider
transports. It revalidates and atomically consumes privacy authority immediately before dispatch,
renders and scans the exact final application request body before any credential is minted, and
never exposes a provider client, credential, or raw response to application code. ``ProviderRegistry``
is an immutable, generation-fenced snapshot of currently permitted adapters; only ``reconcile_policy``
may swap it, and only after policy/vault/human-authority validation.

Two narrow seams decouple this file from provider-specific SDK wiring (kept in
``adapters/providers/*`` and daemon composition, both outside this file's ownership):

- ``ExternalProviderFactory`` is a credential-free, per-``ProviderBinding`` factory that
  deterministically renders the final request body from an ``ApprovedOutboundCase`` (no I/O), and
  builds a one-attempt ``SemanticEvaluatorPort`` bound to a fresh credential handle. Daemon
  composition is expected to supply one factory builder per exact external binding (for example one
  wrapping ``adapters/providers/openai_responses.py``'s ``render_case``/``OpenAIResponsesEvaluator``).
- ``ProviderCredentialMinter`` is the narrow service-vault boundary that mints exactly one
  ``ProviderCredentialHandle`` per ``ProviderAttemptAuthBinding``; it never resolves generic secrets.

Local-model dispatch does not go through either seam: ``reconcile_policy`` alone may ask the
supplied ``LocalModelSocketResolverPort`` for a socket handle (only after the durable policy proves
``local_model_enabled`` for the exact installed profile), and the resulting
``LocalModelEvaluator`` is materialized once per reconciliation and reused directly at dispatch.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Protocol

from yoetz.adapters.privacy.catalog import _scope_digest  # pyright: ignore[reportPrivateUsage]
from yoetz.adapters.privacy.local_enforcer import SecretScanRuleset
from yoetz.adapters.providers.local_model import (
    InstalledLocalModelProfileRegistry,
    LocalModelEvaluator,
    LocalModelSocketResolverPort,
)
from yoetz.domain.findings import SamplingParams, SemanticFailureClass
from yoetz.domain.privacy import (
    PRIVACY_REQUEST_COMMITMENT_ALGORITHM,
    ApprovedLocalDisclosureCase,
    ApprovedOutboundCase,
    ConsentSource,
    EgressAuthorization,
    EgressChannel,
    EgressReceipt,
    LocalDisclosureSink,
    PrivacyOutcome,
    PrivacyPolicy,
    PrivacyReason,
    ProviderBinding,
    ReceiptCounts,
    ReceiptPolicyBinding,
    ReceiptSecretScan,
    ReceiptTransformations,
    RequestCommitment,
)
from yoetz.observability.privacy import privacy_request_commitment
from yoetz.ports.clock import ClockPort
from yoetz.ports.ids import IdPort
from yoetz.ports.keys import MacKeyHandle
from yoetz.ports.privacy import (
    EffectivePrivacyPolicy,
    HumanAuthorityCapability,
    OutboundGatewayPort,
    PrivacyAuditPort,
    PrivacyClassifierPort,
    ProviderReconciliation,
)
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding, ProviderCredentialHandle
from yoetz.ports.semantic import (
    Deadline,
    ProviderAttemptProvenance,
    SemanticEvaluatorPort,
    SemanticResult,
    SemanticResultInvalid,
    SemanticResultLate,
    SemanticResultRefused,
    SemanticResultSuccess,
    SemanticResultTimeout,
    SemanticResultUnavailable,
)
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.ids import IdKind
from yoetz.protocol.models import SemanticStatus

__all__ = [
    "ExternalProviderFactory",
    "PolicyEnforcingOutboundGateway",
    "ProviderCredentialMinter",
    "ProviderRegistry",
]

_ZERO_DIGEST = "sha256:" + "0" * 64
_SCAN = SecretScanRuleset()

_NO_AUTHORIZATION_ID_OUTCOMES = frozenset(
    {
        PrivacyOutcome.BLOCKED_BY_POLICY,
        PrivacyOutcome.BLOCKED_FORBIDDEN_DATA,
        PrivacyOutcome.CLASSIFICATION_UNCERTAIN,
        PrivacyOutcome.HUMAN_DENIED,
        PrivacyOutcome.CHANNEL_UNAVAILABLE,
    }
)

_PRECONSUME_OUTCOME: Mapping[PrivacyReason, PrivacyOutcome] = MappingProxyType(
    {
        PrivacyReason.CHANNEL_UNAVAILABLE: PrivacyOutcome.CHANNEL_UNAVAILABLE,
        PrivacyReason.NEVER_SEND_DETECTED: PrivacyOutcome.BLOCKED_FORBIDDEN_DATA,
        PrivacyReason.AUTHORIZATION_EXPIRED: PrivacyOutcome.APPROVAL_EXPIRED,
        PrivacyReason.AUTHORIZATION_STALE: PrivacyOutcome.APPROVAL_EXPIRED,
        PrivacyReason.AUTHORIZATION_REUSED: PrivacyOutcome.APPROVAL_EXPIRED,
        PrivacyReason.DEADLINE_EXPIRED: PrivacyOutcome.TIMEOUT,
        PrivacyReason.PROVIDER_UNAVAILABLE: PrivacyOutcome.TRANSPORT_FAILED,
        PrivacyReason.AUDIT_FAILED: PrivacyOutcome.AUDIT_FAILED,
    }
)


class ExternalProviderFactory(Protocol):
    """Credential-free, per-binding external provider factory installed by daemon composition.

    Only ``PolicyEnforcingOutboundGateway`` may hold or invoke one. Construction of the factory
    itself must never require a credential or perform network I/O; only :meth:`build_evaluator`
    receives a freshly minted, dispatch-bound credential handle.
    """

    def render(self, case: ApprovedOutboundCase) -> bytes:
        """Deterministically render the exact final application request body; no I/O."""
        ...

    def build_evaluator(
        self,
        binding: ProviderAttemptAuthBinding,
        credential: ProviderCredentialHandle,
        request_commitment: RequestCommitment,
    ) -> SemanticEvaluatorPort:
        """Build a one-attempt evaluator bound to this exact credential handle and commitment."""
        ...


class ProviderCredentialMinter(Protocol):
    """The narrow service-vault boundary that mints one exact dispatch-bound credential."""

    async def mint(self, binding: ProviderAttemptAuthBinding) -> ProviderCredentialHandle: ...


@dataclass(frozen=True, slots=True)
class ProviderRegistry:
    """One immutable, generation-fenced snapshot of currently permitted provider adapters.

    Only :meth:`PolicyEnforcingOutboundGateway.reconcile_policy` constructs and swaps this snapshot.
    It has no default adapter, wildcard provider, generic URL, redirect, or fallback: every entry is
    keyed by the exact :class:`ProviderBinding` the durable policy currently permits.
    """

    policy_id: str
    policy_version: int
    policy_generation: int
    policy_digest: str
    service_generation: int
    vault_generation: int
    human_authority_digest: str
    external: Mapping[ProviderBinding, ExternalProviderFactory]
    local_model: tuple[ProviderBinding, SemanticEvaluatorPort] | None

    def __post_init__(self) -> None:
        if type(self.external) is not MappingProxyType:
            raise TypeError("provider_registry_external_not_immutable")
        if self.local_model is not None and type(self.local_model) is not tuple:
            raise TypeError("provider_registry_local_model_invalid")

    def resolve_external(self, binding: ProviderBinding) -> ExternalProviderFactory | None:
        return self.external.get(binding)

    def resolve_local(self, binding: ProviderBinding) -> SemanticEvaluatorPort | None:
        if self.local_model is not None and self.local_model[0] == binding:
            return self.local_model[1]
        return None


def _llm_binding(policy: PrivacyPolicy) -> ProviderBinding | None:
    llm = next(
        item for item in policy.channel_policies if item.channel is EgressChannel.LLM_INFERENCE
    )
    if not policy.network_egress_permitted or not llm.enabled:
        return None
    return llm.provider_binding


def _local_binding(policy: PrivacyPolicy) -> ProviderBinding | None:
    return policy.local_model_binding if policy.local_model_enabled else None


async def _best_effort_close(target: object) -> None:
    closer = getattr(target, "close", None) or getattr(target, "aclose", None)
    if closer is None:
        return
    try:
        result = closer()
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001 - closure of a foreign adapter must never raise
        return


class PolicyEnforcingOutboundGateway(OutboundGatewayPort):
    """The sole gateway allowed to own semantic provider transports.

    Construction receives only verified credential-free factory builders, the privacy audit
    authority needed for atomic consumption, the exact same :class:`PrivacyClassifierPort` instance
    used by the application coordinator, one opaque ``MacKeyHandle(purpose=privacy_audit)``, and a
    narrow service-vault credential minter. It never reads environment/config secrets and never
    discovers endpoints on its own.
    """

    __slots__ = (
        "_audit",
        "_audit_mac",
        "_classifier",
        "_clock",
        "_close_task",
        "_closed",
        "_credential_minter",
        "_external_factory_builders",
        "_ids",
        "_lock",
        "_local_model_registry",
        "_local_model_resolver",
        "_registry",
    )

    def __init__(
        self,
        *,
        external_factory_builders: Mapping[ProviderBinding, Callable[[], ExternalProviderFactory]],
        local_model_registry: InstalledLocalModelProfileRegistry,
        local_model_resolver: LocalModelSocketResolverPort | None,
        credential_minter: ProviderCredentialMinter,
        audit: PrivacyAuditPort,
        classifier: PrivacyClassifierPort,
        audit_mac: MacKeyHandle,
        clock: ClockPort,
        ids: IdPort,
    ) -> None:
        if type(local_model_registry) is not InstalledLocalModelProfileRegistry:
            raise TypeError("privacy_gateway_local_model_registry_invalid")
        self._external_factory_builders = dict(external_factory_builders)
        self._local_model_registry = local_model_registry
        self._local_model_resolver = local_model_resolver
        self._credential_minter = credential_minter
        self._audit = audit
        self._classifier = classifier
        self._audit_mac = audit_mac
        self._clock = clock
        self._ids = ids
        self._lock = asyncio.Lock()
        self._registry: ProviderRegistry | None = None
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def _current_registry(self) -> ProviderRegistry | None:
        """Read the live snapshot through a call boundary so static narrowing never assumes away
        a concurrent :meth:`close`/:meth:`close_revoked`/:meth:`reconcile_policy` mutation that can
        happen during an ``await`` elsewhere in this coroutine."""

        return self._registry

    def configured_provider_ids(self) -> tuple[str, ...]:
        """Return bounded structural provider IDs with verified factory builders."""

        return tuple(
            sorted(
                {binding.provider_id for binding in self._external_factory_builders},
                key=str.encode,
            )
        )

    def connected_provider_ids(self) -> tuple[str, ...]:
        """Return provider IDs present in the current generation-fenced registry."""

        registry = self._current_registry()
        if registry is None:
            return ()
        connected = {binding.provider_id for binding in registry.external}
        if registry.local_model is not None:
            connected.add(registry.local_model[0].provider_id)
        return tuple(sorted(connected, key=str.encode))

    # -- reconciliation -----------------------------------------------------------------------

    async def reconcile_policy(
        self, policy: EffectivePrivacyPolicy, human_authority: HumanAuthorityCapability
    ) -> ProviderReconciliation:
        if type(policy) is not EffectivePrivacyPolicy:
            raise TypeError("privacy_gateway_effective_policy_invalid")
        if type(human_authority) is not HumanAuthorityCapability:
            raise TypeError("privacy_gateway_human_authority_invalid")

        async with self._lock:
            if self._closed:
                return ProviderReconciliation(policy.generation, 0, 0, ())
            previous = self._registry
            allowed_external = _llm_binding(policy.policy)
            allowed_local = _local_binding(policy.policy)
            # Phase 1: install an immediate deny fence for anything the new policy no longer
            # permits, before any (possibly slow) candidate construction happens outside the lock.
            fenced_external: dict[ProviderBinding, ExternalProviderFactory] = {}
            fenced_local: tuple[ProviderBinding, SemanticEvaluatorPort] | None = None
            stale_external: dict[ProviderBinding, ExternalProviderFactory] = {}
            stale_local: tuple[ProviderBinding, SemanticEvaluatorPort] | None = None
            if previous is not None:
                for binding, factory in previous.external.items():
                    if binding == allowed_external:
                        fenced_external[binding] = factory
                    else:
                        stale_external[binding] = factory
                if previous.local_model is not None:
                    if previous.local_model[0] == allowed_local:
                        fenced_local = previous.local_model
                    else:
                        stale_local = previous.local_model
            self._registry = ProviderRegistry(
                policy.policy.policy_id,
                policy.policy.version,
                policy.generation,
                policy.effective_digest,
                human_authority.service_generation,
                human_authority.vault_generation,
                human_authority.capability_digest,
                MappingProxyType(fenced_external),
                fenced_local,
            )

        for factory in stale_external.values():
            await _best_effort_close(factory)
        if stale_local is not None:
            await _best_effort_close(stale_local[1])

        # Phase 2: outside the lock, build newly allowed candidates. No inference/capability
        # request occurs here and no credential handle is minted.
        new_external: dict[ProviderBinding, ExternalProviderFactory] = dict(fenced_external)
        unavailable: list[tuple[str, str]] = []
        if (
            allowed_external is not None
            and allowed_external not in new_external
            and human_authority.source != "unavailable"
            and human_authority.external_activation_allowed
        ):
            builder = self._external_factory_builders.get(allowed_external)
            if builder is None:
                unavailable.append((_binding_digest(allowed_external), "factory_unavailable"))
            else:
                try:
                    new_external[allowed_external] = builder()
                except Exception:  # noqa: BLE001 - a foreign factory failure is bounded here
                    unavailable.append(
                        (_binding_digest(allowed_external), "factory_construction_failed")
                    )

        new_local = fenced_local
        if (
            allowed_local is not None
            and new_local is None
            and self._local_model_resolver is not None
        ):
            # `InstalledLocalModelProfileRegistry.resolve` is keyed by the release-artifact's own
            # `(profile_id, profile_version)`, which the privacy policy's `ProviderBinding` does not
            # carry; the only correlation available here is the endpoint profile identity, so the
            # exact installed entry is found by matching that instead.
            profile = next(
                (
                    entry
                    for entry in self._local_model_registry.entries
                    if entry.endpoint_profile_id == allowed_local.endpoint_profile_id
                    and entry.endpoint_profile_version == allowed_local.endpoint_profile_version
                ),
                None,
            )
            if profile is None:
                unavailable.append(
                    (_binding_digest(allowed_local), "local_model_profile_unavailable")
                )
            else:
                try:
                    handle = self._local_model_resolver.resolve(profile)
                    if (
                        handle.service_generation == human_authority.service_generation
                        and handle.profile_digest == profile.capability_evidence_digest
                    ):
                        new_local = (
                            allowed_local,
                            LocalModelEvaluator(profile, handle, self._clock),
                        )
                    else:
                        unavailable.append(
                            (_binding_digest(allowed_local), "local_model_generation_mismatch")
                        )
                except Exception:  # noqa: BLE001 - a foreign resolver failure is bounded here
                    unavailable.append(
                        (_binding_digest(allowed_local), "local_model_resolve_failed")
                    )

        # Phase 3: re-take the lock, recheck currency, and atomically publish only matching
        # candidates. Stale candidates built against an outdated snapshot are closed, not adopted.
        async with self._lock:
            current = self._current_registry()
            if self._closed or current is None or current.policy_generation != policy.generation:
                for binding, factory in new_external.items():
                    if binding not in fenced_external:
                        await _best_effort_close(factory)
                if new_local is not None and new_local is not fenced_local:
                    await _best_effort_close(new_local[1])
                return ProviderReconciliation(policy.generation, 0, 0, tuple(sorted(unavailable)))
            activated = (
                len(new_external)
                - len(fenced_external)
                + (1 if new_local is not None and new_local is not fenced_local else 0)
            )
            deactivated = len(stale_external) + (1 if stale_local is not None else 0)
            self._registry = ProviderRegistry(
                policy.policy.policy_id,
                policy.policy.version,
                policy.generation,
                policy.effective_digest,
                human_authority.service_generation,
                human_authority.vault_generation,
                human_authority.capability_digest,
                MappingProxyType(new_external),
                new_local,
            )
        return ProviderReconciliation(
            policy.generation, activated, deactivated, tuple(sorted(unavailable))
        )

    # -- external dispatch ---------------------------------------------------------------------

    async def dispatch_external_semantic(
        self,
        case: ApprovedOutboundCase,
        authorization: EgressAuthorization,
        deadline: Deadline,
    ) -> SemanticResult:
        if type(case) is not ApprovedOutboundCase:
            raise TypeError("privacy_gateway_case_invalid")
        if type(authorization) is not EgressAuthorization:
            raise TypeError("privacy_gateway_authorization_invalid")
        if type(deadline) is not Deadline:
            raise TypeError("privacy_gateway_deadline_invalid")

        registry = self._registry
        now_monotonic = self._clock.monotonic_seconds()
        now_utc = self._clock.now_utc()

        reason = self._predispatch_reason(
            case, authorization, registry, deadline, now_monotonic, now_utc
        )
        if reason is not None:
            return await self._preconsume_failure(case, authorization, reason)
        assert registry is not None

        factory = registry.resolve_external(case.provider_binding)
        if factory is None:
            return await self._preconsume_failure(
                case, authorization, PrivacyReason.CHANNEL_UNAVAILABLE
            )

        dispatch_id = self._ids.new(IdKind.EGRESS_DISPATCH)

        try:
            body = factory.render(case)
        except Exception:  # noqa: BLE001 - a foreign factory failure is bounded here
            return await self._preconsume_failure(
                case, authorization, PrivacyReason.PROVIDER_UNAVAILABLE
            )
        if type(body) is not bytes or not body or case.payload not in body:
            return await self._preconsume_failure(
                case, authorization, PrivacyReason.PROVIDER_UNAVAILABLE
            )

        if self._classifier.scan_exact_bytes(body):
            return await self._preconsume_failure(
                case, authorization, PrivacyReason.NEVER_SEND_DETECTED
            )

        body_digest = "sha256:" + hashlib.sha256(body).hexdigest()
        commitment = privacy_request_commitment(body, self._audit_mac)

        binding = ProviderAttemptAuthBinding(
            provider_id=case.provider_binding.provider_id,
            model_id=case.provider_binding.model_id,
            endpoint_profile_id=case.provider_binding.endpoint_profile_id,
            endpoint_profile_version=case.provider_binding.endpoint_profile_version,
            purpose=case.purpose,
            authorization_scope_digest=_scope_digest(authorization.scope),
            purpose_digest=canonical_digest({"purpose": case.purpose}),
            dispatch_id=dispatch_id,
            request_body_digest=body_digest,
            service_generation=registry.service_generation,
            monotonic_deadline=deadline.monotonic_deadline,
        )

        try:
            credential = await self._credential_minter.mint(binding)
        except Exception:  # noqa: BLE001 - a foreign vault failure is bounded here
            return await self._preconsume_failure(
                case, authorization, PrivacyReason.PROVIDER_UNAVAILABLE
            )

        if self._closed or self._current_registry() is not registry:
            return await self._preconsume_failure(
                case, authorization, PrivacyReason.AUTHORIZATION_STALE
            )

        try:
            evaluator = factory.build_evaluator(
                binding,
                credential,
                RequestCommitment(PRIVACY_REQUEST_COMMITMENT_ALGORITHM, commitment),
            )
        except Exception:  # noqa: BLE001 - a foreign factory failure is bounded here
            return await self._preconsume_failure(
                case, authorization, PrivacyReason.PROVIDER_UNAVAILABLE
            )

        try:
            await self._audit.consume(authorization.authorization_id, dispatch_id, now_utc)
        except Exception:  # noqa: BLE001 - a lost consume race is bounded here
            return await self._preconsume_failure(
                case, authorization, PrivacyReason.AUTHORIZATION_REUSED
            )

        # From here on the physical attempt is admitted: no failure below can restore authority,
        # only its actual terminal or `outcome_unknown` receipt is produced.
        dispatch_started_at = self._clock.now_utc()
        try:
            result = await evaluator.evaluate(case, deadline)
            outcome, receipt_reason = _result_outcome(result)
        except Exception:  # noqa: BLE001 - an ambiguous transport failure never leaks native text
            result = _unknown_outcome_result(case, binding)
            outcome, receipt_reason = PrivacyOutcome.TRANSPORT_FAILED, PrivacyReason.OUTCOME_UNKNOWN

        receipt = self._attempt_receipt(
            case,
            authorization,
            registry,
            dispatch_id,
            dispatch_started_at,
            commitment,
            body,
            outcome,
            receipt_reason,
        )
        try:
            await self._audit.complete_egress(dispatch_id, receipt)
        except Exception:  # noqa: BLE001 - best-effort: the caller already has the real result
            pass
        return result

    def _predispatch_reason(
        self,
        case: ApprovedOutboundCase,
        authorization: EgressAuthorization,
        registry: ProviderRegistry | None,
        deadline: Deadline,
        now_monotonic: float,
        now_utc: datetime,
    ) -> PrivacyReason | None:
        if self._closed or registry is None:
            return PrivacyReason.CHANNEL_UNAVAILABLE
        if (
            case.authorization_id != authorization.authorization_id
            or case.case_digest != authorization.case_digest
            or case.provider_binding != authorization.provider_binding
            or case.purpose != authorization.purpose
        ):
            return PrivacyReason.AUTHORIZATION_REUSED
        if (
            case.policy_digest != authorization.policy_digest
            or registry.policy_digest != authorization.policy_digest
            or registry.service_generation != authorization.service_generation
        ):
            return PrivacyReason.AUTHORIZATION_STALE
        if deadline.expired(now_monotonic):
            return PrivacyReason.DEADLINE_EXPIRED
        if now_utc >= authorization.expires_at:
            return PrivacyReason.AUTHORIZATION_EXPIRED
        return None

    async def _preconsume_failure(
        self, case: ApprovedOutboundCase, authorization: EgressAuthorization, reason: PrivacyReason
    ) -> SemanticResult:
        outcome = _PRECONSUME_OUTCOME[reason]
        now = self._clock.now_utc()
        authorization_id = (
            None if outcome in _NO_AUTHORIZATION_ID_OUTCOMES else authorization.authorization_id
        )
        receipt = EgressReceipt(
            "1.0.0",
            self._ids.new(IdKind.EGRESS_RECEIPT),
            case.request_id,
            authorization.privacy_proposal_id,
            EgressChannel.LLM_INFERENCE,
            outcome,
            now,
            authorization.scope,
            case.purpose,
            case.provider_binding,
            _bounded_policy_binding(authorization),
            ConsentSource.NONE,
            (),
            case.blocked_categories,
            ReceiptCounts(
                len(case.included_item_ids),
                0,
                len(case.included_item_ids),
                0,
                len(case.included_item_ids),
                case.byte_count,
                0,
                None,
                None,
            ),
            ReceiptTransformations(0, 0, len(case.included_item_ids)),
            ReceiptSecretScan(_SCAN.version, _SCAN.profile_digest, 0, True),
            reason,
            1,
            authorization_id=authorization_id,
        )
        try:
            await self._audit.complete_decision(authorization.privacy_proposal_id, receipt)
        except Exception:  # noqa: BLE001 - best-effort: the bounded result still returns
            pass
        return _preconsume_result(case, reason)

    def _attempt_receipt(
        self,
        case: ApprovedOutboundCase,
        authorization: EgressAuthorization,
        registry: ProviderRegistry,
        dispatch_id: str,
        dispatch_started_at: datetime,
        commitment: str,
        body: bytes,
        outcome: PrivacyOutcome,
        reason: PrivacyReason | None,
    ) -> EgressReceipt:
        return EgressReceipt(
            "1.0.0",
            self._ids.new(IdKind.EGRESS_RECEIPT),
            case.request_id,
            authorization.privacy_proposal_id,
            EgressChannel.LLM_INFERENCE,
            outcome,
            self._clock.now_utc(),
            authorization.scope,
            case.purpose,
            case.provider_binding,
            ReceiptPolicyBinding(
                registry.policy_id,
                registry.policy_version,
                registry.policy_digest,
                _scope_digest(authorization.scope),
            ),
            authorization.consent_source,
            case.approved_categories,
            case.blocked_categories,
            ReceiptCounts(
                len(case.included_item_ids),
                len(case.included_item_ids),
                0,
                len(case.included_item_ids),
                0,
                case.byte_count,
                case.byte_count,
                case.token_count,
                len(body),
            ),
            ReceiptTransformations(0, 0, 0),
            ReceiptSecretScan(_SCAN.version, _SCAN.profile_digest, 0, True),
            reason,
            1,
            authorization_id=authorization.authorization_id,
            dispatch_id=dispatch_id,
            dispatch_started_at=dispatch_started_at,
            request_commitment=RequestCommitment(PRIVACY_REQUEST_COMMITMENT_ALGORITHM, commitment),
        )

    # -- local dispatch -------------------------------------------------------------------------

    async def dispatch_local_semantic(
        self, case: ApprovedLocalDisclosureCase, deadline: Deadline
    ) -> SemanticResult:
        if (
            type(case) is not ApprovedLocalDisclosureCase
            or case.sink is not LocalDisclosureSink.LOCAL_MODEL
        ):
            raise TypeError("privacy_gateway_local_case_invalid")
        if type(deadline) is not Deadline:
            raise TypeError("privacy_gateway_deadline_invalid")

        registry = self._current_registry()
        if self._closed or registry is None or registry.local_model is None or case.binding is None:
            return _local_unavailable_result(case)
        binding, evaluator = registry.local_model
        if case.binding != binding or case.policy_digest != registry.policy_digest:
            return _local_unavailable_result(case)
        if self._classifier.scan_exact_bytes(case.payload):
            return _local_unavailable_result(case)

        now = self._clock.now_utc()
        try:
            await self._audit.consume_local(case.privacy_proposal_id, case.case_digest, now)
        except Exception:  # noqa: BLE001 - a lost consume race is bounded here
            return _local_unavailable_result(case)

        return await evaluator.evaluate(case, deadline)

    # -- lifecycle ------------------------------------------------------------------------------

    async def close_revoked(self, policy_generation: int) -> None:
        if type(policy_generation) is not int or policy_generation < 1:
            raise TypeError("privacy_gateway_generation_invalid")
        async with self._lock:
            registry = self._registry
            if self._closed or registry is None or registry.policy_generation > policy_generation:
                return
            self._registry = None
        await _close_registry(registry)

    async def close(self) -> None:
        async with self._lock:
            if self._close_task is None:
                self._closed = True
                registry = self._registry
                self._registry = None
                self._close_task = asyncio.create_task(_close_registry(registry))
            task = self._close_task
        await task


def _binding_digest(binding: ProviderBinding) -> str:
    return canonical_digest(
        {
            "endpoint_profile_id": binding.endpoint_profile_id,
            "endpoint_profile_version": binding.endpoint_profile_version,
            "model_id": binding.model_id,
            "provider_id": binding.provider_id,
            "transport": binding.transport,
        }
    )


def _bounded_policy_binding(authorization: EgressAuthorization) -> ReceiptPolicyBinding:
    """Use the durably-visible fields on the authorization itself for a preconsume receipt.

    ``EgressAuthorization`` carries ``policy_version``/``policy_digest`` but not a ``policy_id``;
    the exact policy identifier is only reliably known while a matching :class:`ProviderRegistry`
    snapshot is current (see the attempted-receipt path, which uses that snapshot instead). A
    preconsume failure may occur precisely because no such snapshot is current, so this binding
    reuses the authorization's own proposal identifier as a stable, non-widening placeholder policy
    identifier alongside the authorization's real version/digest.
    """

    return ReceiptPolicyBinding(
        authorization.privacy_proposal_id.replace("ppr_", "pvy_", 1),
        authorization.policy_version,
        authorization.policy_digest,
        _scope_digest(authorization.scope),
    )


def _result_outcome(result: SemanticResult) -> tuple[PrivacyOutcome, PrivacyReason | None]:
    if type(result) is SemanticResultSuccess:
        return PrivacyOutcome.COMPLETED, None
    if type(result) is SemanticResultRefused:
        return PrivacyOutcome.PROVIDER_REFUSED, PrivacyReason.PROVIDER_REFUSED
    if type(result) is SemanticResultTimeout:
        return PrivacyOutcome.TIMEOUT, PrivacyReason.PROVIDER_TIMEOUT
    if type(result) is SemanticResultInvalid:
        return PrivacyOutcome.INVALID_RESPONSE, PrivacyReason.PROVIDER_INVALID_RESPONSE
    if type(result) is SemanticResultLate:
        return PrivacyOutcome.LATE, PrivacyReason.LATE
    if type(result) is SemanticResultUnavailable:
        return PrivacyOutcome.TRANSPORT_FAILED, PrivacyReason.TRANSPORT_FAILED
    raise TypeError("privacy_gateway_semantic_result_invalid")


def _preconsume_result(case: ApprovedOutboundCase, reason: PrivacyReason) -> SemanticResult:
    failure_class = (
        SemanticFailureClass.TIMEOUT
        if reason is PrivacyReason.DEADLINE_EXPIRED
        else SemanticFailureClass.UNSUPPORTED_PROFILE
    )
    return SemanticResultUnavailable(
        ProviderAttemptProvenance(
            provider=case.provider_binding.provider_id,
            endpoint_profile_id=case.provider_binding.endpoint_profile_id,
            endpoint_profile_version=case.provider_binding.endpoint_profile_version,
            model=case.provider_binding.model_id,
            sdk_version="0.0.0",
            prompt_digest=_ZERO_DIGEST,
            schema_digest=_ZERO_DIGEST,
            policy_digest=_ZERO_DIGEST,
            privacy_policy_digest=_ZERO_DIGEST,
            sampling_params=SamplingParams(1),
            latency_ms=0,
            status=SemanticStatus.UNAVAILABLE,
            failure_class=failure_class,
        )
    )


def _unknown_outcome_result(
    case: ApprovedOutboundCase, binding: ProviderAttemptAuthBinding
) -> SemanticResult:
    return SemanticResultUnavailable(
        ProviderAttemptProvenance(
            provider=case.provider_binding.provider_id,
            endpoint_profile_id=case.provider_binding.endpoint_profile_id,
            endpoint_profile_version=case.provider_binding.endpoint_profile_version,
            model=case.provider_binding.model_id,
            sdk_version="0.0.0",
            prompt_digest=binding.request_body_digest,
            schema_digest=binding.request_body_digest,
            policy_digest=_ZERO_DIGEST,
            privacy_policy_digest=_ZERO_DIGEST,
            sampling_params=SamplingParams(1),
            latency_ms=0,
            status=SemanticStatus.UNAVAILABLE,
            failure_class=SemanticFailureClass.TRANSPORT,
        )
    )


def _local_unavailable_result(case: ApprovedLocalDisclosureCase) -> SemanticResult:
    binding = case.binding
    provider = "local-model" if binding is None else binding.provider_id
    endpoint = "unavailable" if binding is None else binding.endpoint_profile_id
    version = "0.0.0" if binding is None else binding.endpoint_profile_version
    model = "unavailable" if binding is None else binding.model_id
    return SemanticResultUnavailable(
        ProviderAttemptProvenance(
            provider=provider,
            endpoint_profile_id=endpoint,
            endpoint_profile_version=version,
            model=model,
            sdk_version="0.0.0",
            prompt_digest=_ZERO_DIGEST,
            schema_digest=_ZERO_DIGEST,
            policy_digest=_ZERO_DIGEST,
            privacy_policy_digest=_ZERO_DIGEST,
            sampling_params=SamplingParams(1),
            latency_ms=0,
            status=SemanticStatus.UNAVAILABLE,
            failure_class=SemanticFailureClass.UNSUPPORTED_PROFILE,
        )
    )


async def _close_registry(registry: ProviderRegistry | None) -> None:
    if registry is None:
        return
    for factory in registry.external.values():
        await _best_effort_close(factory)
    if registry.local_model is not None:
        await _best_effort_close(registry.local_model[1])
