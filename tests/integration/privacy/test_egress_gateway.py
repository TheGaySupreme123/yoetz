"""Durable privacy pipeline and adapter isolation for ``PolicyEnforcingOutboundGateway``.

Exercises the gateway end to end against real domain/port value types: policy reconciliation
activating/deactivating adapters without restart, exact-body scanning and final validation before
credential exposure, atomic authorization consumption, and the local-model dispatch path. The
in-file ``_FullPrivacyAudit`` fake asserts (by raising) that the gateway calls only the exact audit
methods its contract allows, and the fake provider/local-model adapters assert they receive only an
``ApprovedProviderCase`` -- never a repository/object-store handle, environment, or policy authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as hmac_module
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from yoetz.adapters.privacy.gateway import PolicyEnforcingOutboundGateway
from yoetz.adapters.privacy.local_enforcer import LocalPrivacyEnforcer
from yoetz.adapters.providers.fake import (
    FakeSemanticScript,
    ScriptedFakeSemanticEvaluator,
    scripted_success,
)
from yoetz.adapters.providers.local_model import (
    InstalledLocalModelProfileRegistry,
    LocalModelEndpointProfile,
)
from yoetz.domain.findings import SemanticFailureClass
from yoetz.domain.privacy import (
    ApprovedLocalDisclosureCase,
    ApprovedOutboundCase,
    AuthorizationScope,
    AuthorizationScopeKind,
    ChannelPolicy,
    ConsentSource,
    DataCategory,
    DataClass,
    EgressAuthorization,
    EgressChannel,
    EgressReceipt,
    LocalDisclosureSink,
    PrivacyOutcome,
    PrivacyPolicy,
    PrivacyProfile,
    PrivacyReason,
    ProviderBinding,
    ReviewContextProfile,
    ReviewSelectionPolicy,
)
from yoetz.observability.privacy import privacy_request_commitment
from yoetz.ports.privacy import (
    ConsumedAuthorization,
    ConsumedLocalDisclosure,
    EffectivePrivacyPolicy,
    HumanAuthorityCapability,
    ProviderReconciliation,
)
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding
from yoetz.ports.semantic import (
    Deadline,
    SemanticJudgment,
    SemanticResult,
    SemanticResultSuccess,
    SemanticResultUnavailable,
)
from yoetz.protocol.canonical import canonical_encode
from yoetz.protocol.ids import IdKind

_INSTALLATION = "ins_60000000-0000-4000-8000-000000000001"
_TASK = "tsk_60000000-0000-4000-8000-000000000002"
_WORKSPACE = f"hmac-sha256:{'6' * 64}"
_POLICY_ID = "pvy_60000000-0000-4000-8000-000000000003"
_PROPOSAL = "ppr_60000000-0000-4000-8000-000000000004"
_REQUEST = "req_60000000-0000-4000-8000-000000000005"
_DIGEST = f"sha256:{'a' * 64}"
_DIGEST_2 = f"sha256:{'b' * 64}"
_NOW = datetime(2026, 7, 19, tzinfo=UTC)

_PROVIDER_ID = "fake-provider"
_MODEL_ID = "fake-model-v1"
_ENDPOINT_ID = "ep-fake"
_ENDPOINT_VERSION = "1.0.0"


class _Clock:
    def __init__(self, *, utc: datetime = _NOW, monotonic: float = 100.0) -> None:
        self.utc = utc
        self.monotonic = monotonic

    def now_utc(self) -> datetime:
        return self.utc

    def monotonic_seconds(self) -> float:
        return self.monotonic


class _Ids:
    def __init__(self) -> None:
        self._n = 0

    def new(self, kind: IdKind) -> str:
        self._n += 1
        prefix = {IdKind.EGRESS_DISPATCH: "dsp_", IdKind.EGRESS_RECEIPT: "egr_"}[kind]
        return f"{prefix}{self._n:08x}-0000-4000-8000-000000000001"


class _AuditKey:
    """A deterministic ``MacKeyHandle`` stand-in; never a raw key/bytes value to callers."""

    def __init__(self, seed: bytes = b"k" * 32) -> None:
        self._seed = seed

    def mac(self, domain: bytes, message: bytes) -> str:
        digest = hmac_module.new(self._seed, domain + message, hashlib.sha256).hexdigest()
        return f"hmac-sha256:{digest}"


@dataclass(slots=True)
class _CredentialRecord:
    consumed: bool = False


class _FakeCredentialHandle:
    """A single-use credential handle: its transport-callback contract is enforced here."""

    def __init__(self, token: bytes, record: _CredentialRecord) -> None:
        self._token = token
        self._record = record

    async def authorize_attempt(self, binding: object, inject_and_start: object) -> object:
        if self._record.consumed:
            raise RuntimeError("credential_already_consumed")
        self._record.consumed = True
        return await inject_and_start.inject_and_start(memoryview(self._token))  # type: ignore[attr-defined]


class _CredentialMinter:
    def __init__(self) -> None:
        self.mint_calls: list[ProviderAttemptAuthBinding] = []
        self.fail = False

    async def mint(self, binding: ProviderAttemptAuthBinding) -> _FakeCredentialHandle:
        self.mint_calls.append(binding)
        if self.fail:
            raise RuntimeError("simulated_credential_mint_failure")
        return _FakeCredentialHandle(b"fake-secret-token-0123456789", _CredentialRecord())


class _InjectCallback:
    def __init__(self) -> None:
        self.injected: bytes | None = None

    async def inject_and_start(self, credential_view: memoryview) -> None:
        self.injected = bytes(credential_view)


class _FakeEvaluator:
    """Consumes its credential exactly once, then defers to a scripted fake evaluation."""

    def __init__(self, script: FakeSemanticScript, credential: object, binding: object) -> None:
        self._inner = ScriptedFakeSemanticEvaluator(script)
        self._credential = credential
        self._binding = binding
        self.evaluate_calls = 0

    async def evaluate(self, case: object, deadline: object) -> object:
        self.evaluate_calls += 1
        callback = _InjectCallback()
        await self._credential.authorize_attempt(self._binding, callback)  # type: ignore[attr-defined]
        assert callback.injected == b"fake-secret-token-0123456789"
        return await self._inner.evaluate(case, deadline)  # type: ignore[arg-type]


class _FakeExternalFactory:
    """Credential-free external factory: renders deterministically, no I/O until dispatch."""

    def __init__(self, script_factory: object) -> None:
        self._script_factory = script_factory
        self.render_calls = 0
        self.rendered_bodies: list[bytes] = []
        self.built: list[_FakeEvaluator] = []
        self.closed = False

    def render(self, case: ApprovedOutboundCase) -> bytes:
        self.render_calls += 1
        # A tiny fixed reviewed template wraps the exact approved payload bytes only.
        body = b'{"template":"reviewed-schema/1","payload":' + case.payload + b"}"
        self.rendered_bodies.append(body)
        return body

    def build_evaluator(
        self, binding: object, credential: object, request_commitment: object
    ) -> object:
        del request_commitment
        evaluator = _FakeEvaluator(self._script_factory(), credential, binding)  # type: ignore[operator]
        self.built.append(evaluator)
        return evaluator

    async def close(self) -> None:
        self.closed = True


class _ForbiddenBodyFactory(_FakeExternalFactory):
    """Renders a body whose fixed template segment carries a never-send credential pattern."""

    def render(self, case: ApprovedOutboundCase) -> bytes:
        self.render_calls += 1
        body = b'{"payload":' + case.payload + b',"leak":"AKIAABCDEFGHIJKLMNOP"}'
        self.rendered_bodies.append(body)
        return body


class _RaisingEvaluatorFactory(_FakeExternalFactory):
    """A factory whose transport raises an ambiguous, native-text-bearing failure."""

    def build_evaluator(
        self, binding: object, credential: object, request_commitment: object
    ) -> object:
        del binding, credential, request_commitment

        class _Raiser:
            async def evaluate(self, case: object, deadline: object) -> object:
                del case, deadline
                raise RuntimeError("native-provider-socket-reset: 10.0.0.7:443 leaked")

        return _Raiser()


class _LocalSocketHandle:
    def __init__(self, service_generation: int, profile_digest: str, response: bytes) -> None:
        self._service_generation = service_generation
        self._profile_digest = profile_digest
        self._response = response
        self.sent: list[bytes] = []
        self.closed = False

    @property
    def service_generation(self) -> int:
        return self._service_generation

    @property
    def profile_digest(self) -> str:
        return self._profile_digest

    async def send(self, payload: bytes) -> None:
        self.sent.append(payload)

    async def receive(self, max_bytes: int) -> bytes:
        del max_bytes
        return self._response

    def close(self) -> None:
        self.closed = True


class _LocalResolver:
    def __init__(self, handle: _LocalSocketHandle) -> None:
        self._handle = handle
        self.resolve_calls = 0

    def resolve(self, profile: LocalModelEndpointProfile) -> _LocalSocketHandle:
        del profile
        self.resolve_calls += 1
        return self._handle


class _FullPrivacyAudit:
    """A working in-memory ``PrivacyAuditPort`` limited to exactly the gateway's call surface.

    Every method the gateway must never call raises immediately, doubling as a network/authority
    spy: any accidental call to ``authorize``/``reserve``/``record_human_decision``/etc. fails the
    test loudly instead of silently succeeding.
    """

    def __init__(self) -> None:
        self._authorizations: dict[str, EgressAuthorization] = {}
        self._authorization_state: dict[str, str] = {}
        self._local_state: dict[str, str] = {}
        self.decision_receipts: list[tuple[str, EgressReceipt]] = []
        self.egress_receipts: list[tuple[str, EgressReceipt]] = []
        self.consume_local_calls: list[tuple[str, str]] = []

    def seed_authorized(self, authorization: EgressAuthorization) -> None:
        self._authorizations[authorization.authorization_id] = authorization
        self._authorization_state[authorization.authorization_id] = "authorized"

    def seed_local_reserved(self, privacy_proposal_id: str) -> None:
        self._local_state[privacy_proposal_id] = "reserved"

    def authorization_state(self, authorization_id: str) -> str | None:
        return self._authorization_state.get(authorization_id)

    async def consume(
        self, authorization_id: str, dispatch_id: str, now: datetime
    ) -> ConsumedAuthorization:
        if self._authorization_state.get(authorization_id) != "authorized":
            raise ValueError("privacy_audit_authorization_unavailable")
        self._authorization_state[authorization_id] = "consumed"
        return ConsumedAuthorization(self._authorizations[authorization_id], dispatch_id, now)

    async def complete_egress(self, dispatch_id: str, receipt: EgressReceipt) -> None:
        self.egress_receipts.append((dispatch_id, receipt))

    async def complete_decision(self, reservation_id: str, receipt: EgressReceipt) -> None:
        self.decision_receipts.append((reservation_id, receipt))

    async def consume_local(
        self, reservation_id: str, approved_case_digest: str, now: datetime
    ) -> ConsumedLocalDisclosure:
        if self._local_state.get(reservation_id) != "reserved":
            raise ValueError("privacy_local_reservation_unavailable")
        self._local_state[reservation_id] = "consumed"
        self.consume_local_calls.append((reservation_id, approved_case_digest))
        return ConsumedLocalDisclosure(
            reservation_id, approved_case_digest, LocalDisclosureSink.LOCAL_MODEL, now
        )

    async def complete_agent_projection(self, request: object, receipt: object) -> object:
        raise AssertionError("gateway must never call complete_agent_projection")

    async def prepare_disclosure_proposal(self, request: object) -> object:
        raise AssertionError("gateway must never call prepare_disclosure_proposal")

    async def reserve(self, subject: object) -> object:
        raise AssertionError("gateway must never call reserve")

    async def load(self, request_id: str, subject_digest: str) -> object:
        raise AssertionError("gateway must never call load")

    async def record_human_decision(self, reservation_id: str, decision: object) -> object:
        raise AssertionError("gateway must never call record_human_decision")

    async def authorize(
        self, reservation_id: str, approved_case_digest: str, now: datetime
    ) -> object:
        raise AssertionError("gateway must never call authorize")

    async def complete_local_disclosure(self, reservation_id: str, receipt: object) -> None:
        raise AssertionError("gateway must never call complete_local_disclosure")

    async def get_receipt(self, receipt_id: str, audience: object) -> object:
        raise AssertionError("gateway must never call get_receipt")

    async def list_receipts(self, query: object, audience: object) -> object:
        raise AssertionError("gateway must never call list_receipts")

    async def revoke_policy_generation(self, generation: int, reason: str) -> int:
        raise AssertionError("gateway must never call revoke_policy_generation")

    async def live_object_roots(self, task_id: str, route_identity_digest: str) -> object:
        raise AssertionError("gateway must never call live_object_roots")


def _provider_binding() -> ProviderBinding:
    return ProviderBinding(_PROVIDER_ID, _MODEL_ID, _ENDPOINT_ID, _ENDPOINT_VERSION, "external")


def _local_binding() -> ProviderBinding:
    return ProviderBinding("local-svc", "local-model-v1", "ep-local", "1.0.0", "local_af_unix")


def _scope() -> AuthorizationScope:
    return AuthorizationScope(AuthorizationScopeKind.TASK, _INSTALLATION, _WORKSPACE, _TASK)


def _channel(channel: EgressChannel, *, enabled: bool) -> ChannelPolicy:
    if not enabled:
        return ChannelPolicy(
            channel, False, (), (), None, (), AuthorizationScopeKind.MACHINE, False, 0, 0, 0
        )
    return ChannelPolicy(
        channel,
        True,
        (DataCategory.TASK_DESCRIPTION,),
        (DataClass.ORDINARY_USER_CONTENT,),
        _provider_binding(),
        ("selected-code-review",),
        AuthorizationScopeKind.TASK,
        False,
        65_536,
        4_096,
        3_600,
    )


def _policy(
    *, external_enabled: bool, local_enabled: bool, version: int = 1, digest: str = _DIGEST
) -> PrivacyPolicy:
    channels = tuple(
        _channel(channel, enabled=(channel is EgressChannel.LLM_INFERENCE and external_enabled))
        for channel in sorted(EgressChannel, key=lambda item: item.value)
    )
    return PrivacyPolicy(
        policy_id=_POLICY_ID,
        version=version,
        policy_digest=digest,
        profile=PrivacyProfile.TRUSTED_PROVIDER if external_enabled else PrivacyProfile.LOCAL_ONLY,
        review_context_profile=ReviewContextProfile.STRUCTURAL,
        review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.STRUCTURAL),
        require_current_provider_data_use_evidence=False,
        network_egress_permitted=external_enabled,
        effective_scope=_scope(),
        channel_policies=channels,
        local_model_enabled=local_enabled,
        local_model_binding=_local_binding() if local_enabled else None,
        local_model_categories=(DataCategory.TASK_DESCRIPTION,) if local_enabled else (),
        local_model_data_classes=(DataClass.ORDINARY_USER_CONTENT,) if local_enabled else (),
        agent_context_categories=(),
        agent_context_data_classes=(),
        trusted_human_control_categories=(),
        trusted_human_control_data_classes=(),
        created_at=_NOW,
        supersedes_policy_digest=None if version == 1 else _DIGEST,
    )


def _human_authority(*, available: bool, service_generation: int = 1) -> HumanAuthorityCapability:
    return HumanAuthorityCapability(
        source="os_user_presence" if available else "unavailable",
        capability_digest=_DIGEST,
        service_generation=service_generation,
        vault_mode="os_keyring",
        vault_generation=1,
        external_activation_allowed=available,
    )


def _authorization(
    *,
    authorization_id: str,
    policy_digest: str,
    service_generation: int,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> EgressAuthorization:
    return EgressAuthorization(
        authorization_id=authorization_id,
        privacy_proposal_id=_PROPOSAL,
        case_digest=_DIGEST,
        channel=EgressChannel.LLM_INFERENCE,
        provider_binding=_provider_binding(),
        purpose="selected-code-review",
        scope=_scope(),
        policy_version=1,
        policy_digest=policy_digest,
        max_bytes=65_536,
        max_tokens=4_096,
        consent_source=ConsentSource.BASELINE_POLICY,
        issued_at=issued_at or (_NOW - timedelta(minutes=10)),
        expires_at=expires_at or (_NOW + timedelta(minutes=5)),
        service_generation=service_generation,
    )


def _case(
    *, case_id: str, authorization: EgressAuthorization, payload: bytes
) -> ApprovedOutboundCase:
    return ApprovedOutboundCase(
        case_id=case_id,
        request_id=_REQUEST,
        payload=payload,
        media_type="application/json",
        schema_id="yoetz.outbound-case-payload",
        included_item_ids=("item-1",),
        approved_categories=(DataCategory.TASK_DESCRIPTION,),
        blocked_categories=(),
        byte_count=len(payload),
        token_count=8,
        provider_binding=authorization.provider_binding,
        purpose=authorization.purpose,
        authorization_id=authorization.authorization_id,
        policy_digest=authorization.policy_digest,
        case_digest=authorization.case_digest,
    )


def _deadline(clock: _Clock, *, remaining_seconds: float = 30.0) -> Deadline:
    return Deadline(_NOW + timedelta(minutes=5), clock.monotonic + remaining_seconds)


def _gateway(
    *,
    audit: _FullPrivacyAudit,
    clock: _Clock,
    credential_minter: _CredentialMinter | None = None,
    external_factory: object | None = None,
    local_registry: InstalledLocalModelProfileRegistry | None = None,
    local_resolver: _LocalResolver | None = None,
) -> PolicyEnforcingOutboundGateway:
    minter = credential_minter or _CredentialMinter()
    builders = (
        {_provider_binding(): (lambda: external_factory)} if external_factory is not None else {}
    )
    return PolicyEnforcingOutboundGateway(
        external_factory_builders=builders,  # type: ignore[arg-type]
        local_model_registry=local_registry or InstalledLocalModelProfileRegistry(),
        local_model_resolver=local_resolver,
        credential_minter=minter,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        classifier=LocalPrivacyEnforcer(),
        audit_mac=_AuditKey(),  # type: ignore[arg-type]
        clock=clock,  # type: ignore[arg-type]
        ids=_Ids(),  # type: ignore[arg-type]
    )


def _script_factory() -> FakeSemanticScript:
    return FakeSemanticScript((scripted_success(SemanticJudgment("no_material_discrepancy", ())),))


def test_reconcile_and_dispatch_external_semantic_succeeds() -> None:
    clock = _Clock()
    audit = _FullPrivacyAudit()
    minter = _CredentialMinter()
    factory = _FakeExternalFactory(_script_factory)
    gateway = _gateway(audit=audit, clock=clock, credential_minter=minter, external_factory=factory)
    policy = _policy(external_enabled=True, local_enabled=False)
    effective = EffectivePrivacyPolicy(policy, 1, policy.policy_digest)
    human = _human_authority(available=True)

    async def run() -> tuple[SemanticResult, EgressAuthorization]:
        reconciliation = await gateway.reconcile_policy(effective, human)
        assert reconciliation == ProviderReconciliation(1, 1, 0, ())

        authorization = _authorization(
            authorization_id="aut_60000000-0000-4000-8000-000000000010",
            policy_digest=policy.policy_digest,
            service_generation=human.service_generation,
        )
        audit.seed_authorized(authorization)
        payload = canonical_encode({"note": "hello"})
        case = _case(
            case_id="cas_60000000-0000-4000-8000-000000000011",
            authorization=authorization,
            payload=payload,
        )
        result = await gateway.dispatch_external_semantic(case, authorization, _deadline(clock))
        return result, authorization

    result, authorization = asyncio.run(run())

    assert type(result) is SemanticResultSuccess
    assert factory.render_calls == 1
    assert len(factory.built) == 1
    assert factory.built[0].evaluate_calls == 1
    assert len(minter.mint_calls) == 1
    assert audit.authorization_state(authorization.authorization_id) == "consumed"
    assert audit.decision_receipts == []
    assert len(audit.egress_receipts) == 1
    dispatch_id, receipt = audit.egress_receipts[0]
    assert receipt.outcome is PrivacyOutcome.COMPLETED
    assert receipt.safe_failure_reason is None
    assert receipt.authorization_id == authorization.authorization_id
    assert receipt.dispatch_id == dispatch_id
    assert dispatch_id.startswith("dsp_")
    assert receipt.request_commitment is not None
    rendered_body = factory.rendered_bodies[0]
    expected_commitment = privacy_request_commitment(rendered_body, _AuditKey())  # type: ignore[arg-type]
    assert receipt.request_commitment.commitment == expected_commitment
    assert receipt.request_commitment.algorithm == "hmac-sha256/yoetz-privacy-egress-request-v1"
    assert receipt.counts.request_body_bytes == len(rendered_body)


def test_dispatch_rejects_authorization_case_mismatch() -> None:
    clock = _Clock()
    audit = _FullPrivacyAudit()
    minter = _CredentialMinter()
    factory = _FakeExternalFactory(_script_factory)
    gateway = _gateway(audit=audit, clock=clock, credential_minter=minter, external_factory=factory)
    policy = _policy(external_enabled=True, local_enabled=False)
    effective = EffectivePrivacyPolicy(policy, 1, policy.policy_digest)
    human = _human_authority(available=True)

    async def run() -> tuple[SemanticResult, EgressAuthorization]:
        await gateway.reconcile_policy(effective, human)
        authorization = _authorization(
            authorization_id="aut_60000000-0000-4000-8000-000000000020",
            policy_digest=policy.policy_digest,
            service_generation=human.service_generation,
        )
        audit.seed_authorized(authorization)
        payload = canonical_encode({"note": "hello"})
        case = _case(
            case_id="cas_60000000-0000-4000-8000-000000000021",
            authorization=authorization,
            payload=payload,
        )
        mismatched = replace(case, authorization_id="aut_60000000-0000-4000-8000-000000000099")
        result = await gateway.dispatch_external_semantic(
            mismatched, authorization, _deadline(clock)
        )
        return result, authorization

    result, authorization = asyncio.run(run())

    assert type(result) is SemanticResultUnavailable
    assert minter.mint_calls == []
    assert factory.render_calls == 0
    assert audit.egress_receipts == []
    assert len(audit.decision_receipts) == 1
    _, receipt = audit.decision_receipts[0]
    assert receipt.outcome is PrivacyOutcome.APPROVAL_EXPIRED
    assert receipt.safe_failure_reason is PrivacyReason.AUTHORIZATION_REUSED
    assert receipt.dispatch_id is None
    assert receipt.authorization_id == authorization.authorization_id
    assert audit.authorization_state(authorization.authorization_id) == "authorized"


def test_final_body_scan_blocks_before_credential_mint() -> None:
    clock = _Clock()
    audit = _FullPrivacyAudit()
    minter = _CredentialMinter()
    factory = _ForbiddenBodyFactory(_script_factory)
    gateway = _gateway(audit=audit, clock=clock, credential_minter=minter, external_factory=factory)
    policy = _policy(external_enabled=True, local_enabled=False)
    effective = EffectivePrivacyPolicy(policy, 1, policy.policy_digest)
    human = _human_authority(available=True)

    async def run() -> tuple[SemanticResult, EgressAuthorization]:
        await gateway.reconcile_policy(effective, human)
        authorization = _authorization(
            authorization_id="aut_60000000-0000-4000-8000-000000000030",
            policy_digest=policy.policy_digest,
            service_generation=human.service_generation,
        )
        audit.seed_authorized(authorization)
        payload = canonical_encode({"note": "hello"})
        case = _case(
            case_id="cas_60000000-0000-4000-8000-000000000031",
            authorization=authorization,
            payload=payload,
        )
        result = await gateway.dispatch_external_semantic(case, authorization, _deadline(clock))
        return result, authorization

    result, authorization = asyncio.run(run())

    assert type(result) is SemanticResultUnavailable
    assert minter.mint_calls == []  # never reached credential minting
    assert factory.render_calls == 1
    assert audit.egress_receipts == []
    assert len(audit.decision_receipts) == 1
    _, receipt = audit.decision_receipts[0]
    assert receipt.outcome is PrivacyOutcome.BLOCKED_FORBIDDEN_DATA
    assert receipt.safe_failure_reason is PrivacyReason.NEVER_SEND_DETECTED
    assert receipt.authorization_id is None  # one of the 5 outcomes that never binds an authority
    assert receipt.dispatch_id is None
    assert receipt.consent_source is ConsentSource.NONE
    assert audit.authorization_state(authorization.authorization_id) == "authorized"


def test_expired_authorization_is_rejected_before_dispatch() -> None:
    clock = _Clock()
    audit = _FullPrivacyAudit()
    factory = _FakeExternalFactory(_script_factory)
    gateway = _gateway(audit=audit, clock=clock, external_factory=factory)
    policy = _policy(external_enabled=True, local_enabled=False)
    effective = EffectivePrivacyPolicy(policy, 1, policy.policy_digest)
    human = _human_authority(available=True)

    async def run() -> SemanticResult:
        await gateway.reconcile_policy(effective, human)
        authorization = _authorization(
            authorization_id="aut_60000000-0000-4000-8000-000000000040",
            policy_digest=policy.policy_digest,
            service_generation=human.service_generation,
            expires_at=_NOW - timedelta(seconds=1),
        )
        audit.seed_authorized(authorization)
        payload = canonical_encode({"note": "hello"})
        case = _case(
            case_id="cas_60000000-0000-4000-8000-000000000041",
            authorization=authorization,
            payload=payload,
        )
        return await gateway.dispatch_external_semantic(case, authorization, _deadline(clock))

    asyncio.run(run())

    assert factory.render_calls == 0
    assert len(audit.decision_receipts) == 1
    _, receipt = audit.decision_receipts[0]
    assert receipt.outcome is PrivacyOutcome.APPROVAL_EXPIRED
    assert receipt.safe_failure_reason is PrivacyReason.AUTHORIZATION_EXPIRED


def test_expired_deadline_is_rejected_before_dispatch() -> None:
    clock = _Clock(monotonic=100.0)
    audit = _FullPrivacyAudit()
    factory = _FakeExternalFactory(_script_factory)
    gateway = _gateway(audit=audit, clock=clock, external_factory=factory)
    policy = _policy(external_enabled=True, local_enabled=False)
    effective = EffectivePrivacyPolicy(policy, 1, policy.policy_digest)
    human = _human_authority(available=True)

    async def run() -> SemanticResult:
        await gateway.reconcile_policy(effective, human)
        authorization = _authorization(
            authorization_id="aut_60000000-0000-4000-8000-000000000042",
            policy_digest=policy.policy_digest,
            service_generation=human.service_generation,
        )
        audit.seed_authorized(authorization)
        payload = canonical_encode({"note": "hello"})
        case = _case(
            case_id="cas_60000000-0000-4000-8000-000000000043",
            authorization=authorization,
            payload=payload,
        )
        already_expired = Deadline(_NOW + timedelta(minutes=5), clock.monotonic - 1.0)
        return await gateway.dispatch_external_semantic(case, authorization, already_expired)

    asyncio.run(run())

    assert factory.render_calls == 0
    assert len(audit.decision_receipts) == 1
    _, receipt = audit.decision_receipts[0]
    assert receipt.outcome is PrivacyOutcome.TIMEOUT
    assert receipt.safe_failure_reason is PrivacyReason.DEADLINE_EXPIRED


def test_human_authority_unavailable_empties_external_registry() -> None:
    clock = _Clock()
    audit = _FullPrivacyAudit()
    factory = _FakeExternalFactory(_script_factory)
    gateway = _gateway(audit=audit, clock=clock, external_factory=factory)
    policy = _policy(external_enabled=True, local_enabled=False)
    effective = EffectivePrivacyPolicy(policy, 1, policy.policy_digest)
    human = _human_authority(available=False)

    async def run() -> tuple[ProviderReconciliation, SemanticResult]:
        reconciliation = await gateway.reconcile_policy(effective, human)
        authorization = _authorization(
            authorization_id="aut_60000000-0000-4000-8000-000000000050",
            policy_digest=policy.policy_digest,
            service_generation=human.service_generation,
        )
        audit.seed_authorized(authorization)
        payload = canonical_encode({"note": "hello"})
        case = _case(
            case_id="cas_60000000-0000-4000-8000-000000000051",
            authorization=authorization,
            payload=payload,
        )
        result = await gateway.dispatch_external_semantic(case, authorization, _deadline(clock))
        return reconciliation, result

    reconciliation, result = asyncio.run(run())

    assert reconciliation.activated_count == 0
    assert type(result) is SemanticResultUnavailable
    assert factory.render_calls == 0
    assert len(audit.decision_receipts) == 1
    _, receipt = audit.decision_receipts[0]
    assert receipt.outcome is PrivacyOutcome.CHANNEL_UNAVAILABLE
    assert receipt.authorization_id is None


def test_policy_tightening_closes_registry_and_denies_further_dispatch() -> None:
    clock = _Clock()
    audit = _FullPrivacyAudit()
    factory = _FakeExternalFactory(_script_factory)
    gateway = _gateway(audit=audit, clock=clock, external_factory=factory)
    wide = _policy(external_enabled=True, local_enabled=False)
    wide_effective = EffectivePrivacyPolicy(wide, 1, wide.policy_digest)
    tight = _policy(external_enabled=False, local_enabled=False, version=2, digest=_DIGEST_2)
    tight_effective = EffectivePrivacyPolicy(tight, 2, tight.policy_digest)
    human = _human_authority(available=True)

    async def run() -> tuple[SemanticResult, ProviderReconciliation, SemanticResult]:
        await gateway.reconcile_policy(wide_effective, human)
        authorization = _authorization(
            authorization_id="aut_60000000-0000-4000-8000-000000000060",
            policy_digest=wide.policy_digest,
            service_generation=human.service_generation,
        )
        audit.seed_authorized(authorization)
        payload = canonical_encode({"note": "hello"})
        case = _case(
            case_id="cas_60000000-0000-4000-8000-000000000061",
            authorization=authorization,
            payload=payload,
        )
        first = await gateway.dispatch_external_semantic(case, authorization, _deadline(clock))

        second_reconciliation = await gateway.reconcile_policy(tight_effective, human)

        stale_authorization = _authorization(
            authorization_id="aut_60000000-0000-4000-8000-000000000062",
            policy_digest=wide.policy_digest,
            service_generation=human.service_generation,
        )
        audit.seed_authorized(stale_authorization)
        stale_case = _case(
            case_id="cas_60000000-0000-4000-8000-000000000063",
            authorization=stale_authorization,
            payload=payload,
        )
        second = await gateway.dispatch_external_semantic(
            stale_case, stale_authorization, _deadline(clock)
        )
        return first, second_reconciliation, second

    first, second_reconciliation, second = asyncio.run(run())

    assert type(first) is SemanticResultSuccess
    assert second_reconciliation.deactivated_count == 1
    assert factory.closed is True
    assert type(second) is SemanticResultUnavailable
    assert factory.render_calls == 1  # the tightened attempt never rendered a second body


def test_close_is_idempotent_and_fences_new_work() -> None:
    clock = _Clock()
    audit = _FullPrivacyAudit()
    factory = _FakeExternalFactory(_script_factory)
    gateway = _gateway(audit=audit, clock=clock, external_factory=factory)
    policy = _policy(external_enabled=True, local_enabled=False)
    effective = EffectivePrivacyPolicy(policy, 1, policy.policy_digest)
    human = _human_authority(available=True)

    async def run() -> tuple[ProviderReconciliation, SemanticResult]:
        await gateway.reconcile_policy(effective, human)
        await asyncio.gather(gateway.close(), gateway.close())

        reconciliation = await gateway.reconcile_policy(effective, human)

        authorization = _authorization(
            authorization_id="aut_60000000-0000-4000-8000-000000000070",
            policy_digest=policy.policy_digest,
            service_generation=human.service_generation,
        )
        audit.seed_authorized(authorization)
        payload = canonical_encode({"note": "hello"})
        case = _case(
            case_id="cas_60000000-0000-4000-8000-000000000071",
            authorization=authorization,
            payload=payload,
        )
        result = await gateway.dispatch_external_semantic(case, authorization, _deadline(clock))
        return reconciliation, result

    reconciliation, result = asyncio.run(run())

    assert factory.closed is True
    assert reconciliation == ProviderReconciliation(1, 0, 0, ())
    assert type(result) is SemanticResultUnavailable
    assert factory.render_calls == 0  # close fences new work before any adapter I/O


def test_local_model_dispatch_consumes_reservation_and_calls_evaluator_once() -> None:
    clock = _Clock()
    audit = _FullPrivacyAudit()
    binding = _local_binding()
    profile = LocalModelEndpointProfile(
        profile_id="local-profile",
        profile_version="1.0.0",
        endpoint_profile_id=binding.endpoint_profile_id,
        endpoint_profile_version=binding.endpoint_profile_version,
        model=binding.model_id,
        protocol_version="1.0.0",
        judgment_schema_version="1.0.0",
        timeout_seconds=30,
        expected_service_identity="local-svc",
        expected_owner_uid=0,
        expected_peer_uid=0,
        expected_socket_mode=0o600,
        release_resource_digest=_DIGEST,
        capability_evidence_digest=_DIGEST,
    )
    local_registry = InstalledLocalModelProfileRegistry((profile,))
    handle = _LocalSocketHandle(
        1,
        _DIGEST,
        canonical_encode({"conclusion": "no_material_discrepancy", "reviewer_challenges": []}),
    )
    resolver = _LocalResolver(handle)
    gateway = _gateway(
        audit=audit, clock=clock, local_registry=local_registry, local_resolver=resolver
    )
    policy = _policy(external_enabled=False, local_enabled=True)
    effective = EffectivePrivacyPolicy(policy, 1, policy.policy_digest)
    human = _human_authority(available=True)

    async def run() -> tuple[ProviderReconciliation, SemanticResult]:
        reconciliation = await gateway.reconcile_policy(effective, human)
        audit.seed_local_reserved(_PROPOSAL)
        payload = canonical_encode({"note": "hello"})
        case = ApprovedLocalDisclosureCase(
            case_id="cas_60000000-0000-4000-8000-000000000080",
            request_id=_REQUEST,
            privacy_proposal_id=_PROPOSAL,
            payload=payload,
            media_type="application/json",
            included_item_ids=("item-1",),
            approved_categories=(DataCategory.TASK_DESCRIPTION,),
            blocked_categories=(),
            byte_count=len(payload),
            token_count=8,
            sink=LocalDisclosureSink.LOCAL_MODEL,
            binding=binding,
            purpose="local-check",
            policy_digest=policy.policy_digest,
            case_digest=_DIGEST,
        )
        result = await gateway.dispatch_local_semantic(case, _deadline(clock))
        return reconciliation, result

    reconciliation, result = asyncio.run(run())

    assert reconciliation.activated_count == 1
    assert type(result) is SemanticResultSuccess
    assert resolver.resolve_calls == 1
    assert len(handle.sent) == 1
    assert audit.consume_local_calls == [(_PROPOSAL, _DIGEST)]


def test_evaluator_exception_yields_transport_failed_without_reusable_authorization() -> None:
    clock = _Clock()
    audit = _FullPrivacyAudit()
    minter = _CredentialMinter()
    factory = _RaisingEvaluatorFactory(_script_factory)
    gateway = _gateway(audit=audit, clock=clock, credential_minter=minter, external_factory=factory)
    policy = _policy(external_enabled=True, local_enabled=False)
    effective = EffectivePrivacyPolicy(policy, 1, policy.policy_digest)
    human = _human_authority(available=True)

    async def run() -> tuple[SemanticResult, SemanticResult]:
        await gateway.reconcile_policy(effective, human)
        authorization = _authorization(
            authorization_id="aut_60000000-0000-4000-8000-000000000090",
            policy_digest=policy.policy_digest,
            service_generation=human.service_generation,
        )
        audit.seed_authorized(authorization)
        payload = canonical_encode({"note": "hello"})
        case = _case(
            case_id="cas_60000000-0000-4000-8000-000000000091",
            authorization=authorization,
            payload=payload,
        )
        deadline = _deadline(clock)
        first = await gateway.dispatch_external_semantic(case, authorization, deadline)
        # A retry with the exact same (now-consumed) authorization must be denied: the gateway
        # never accepts an already-consumed authority as proof of permission.
        second = await gateway.dispatch_external_semantic(case, authorization, deadline)
        return first, second

    first, second = asyncio.run(run())

    assert type(first) is SemanticResultUnavailable
    assert first.provenance.failure_class is SemanticFailureClass.TRANSPORT
    assert "10.0.0.7" not in repr(first)
    assert "native-provider-socket-reset" not in repr(first)
    assert len(audit.egress_receipts) == 1
    _, attempt_receipt = audit.egress_receipts[0]
    assert attempt_receipt.outcome is PrivacyOutcome.TRANSPORT_FAILED
    assert attempt_receipt.safe_failure_reason is PrivacyReason.OUTCOME_UNKNOWN

    assert type(second) is SemanticResultUnavailable
    assert len(audit.egress_receipts) == 1  # no second physical attempt was ever admitted
    assert len(audit.decision_receipts) == 1
    _, retry_receipt = audit.decision_receipts[0]
    assert retry_receipt.outcome is PrivacyOutcome.APPROVAL_EXPIRED
    assert retry_receipt.safe_failure_reason is PrivacyReason.AUTHORIZATION_REUSED
    # A fresh credential handle is minted for each attempt (never reused across dispatches), even
    # though the second attempt's transport is never actually invoked.
    assert len(minter.mint_calls) == 2
    assert factory.built == []  # the raising factory never registers a built evaluator list
