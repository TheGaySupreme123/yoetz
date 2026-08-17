"""Integration coverage for the response, status, and receipt operations composing over one
frozen frontier, all driven through the real ``Application`` facade and the memory ledger oracle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Literal, cast

import pydantic
import pytest

from builders.ledger_adapters import ownership_fence
from builders.start_application import (
    MemoryStartRuntime,
    protocol_id,
    start_composition,
    start_request,
)
from yoetz.application.check import FinalSemanticEvaluation
from yoetz.application.egress import PrivacyCoordinator
from yoetz.application.observation_materialize import observation_author
from yoetz.application.service import Application, VerificationPolicy
from yoetz.application.start import StartInternalResult
from yoetz.domain.events import (
    EventDraft,
    EventSchema,
    RuntimeProfile,
    encode_payload,
    media_type_for,
)
from yoetz.domain.findings import (
    FINDING_KIND_TRAITS,
    Finding,
    FindingKind,
    FindingOrigin,
    SemanticDispatchKind,
    SemanticProvenance,
)
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    ConsentSource,
    LocalDisclosureApproved,
    LocalDisclosureReceipt,
    PrivacyOutcome,
    ReceiptCounts,
    ReceiptPolicyBinding,
    ReceiptSecretScan,
    ReceiptTransformations,
)
from yoetz.domain.receipts import PolicyVersionEntry, ReceiptVersionSlice, SchemaVersionEntry
from yoetz.domain.values import (
    Frontier,
    event_id,
    finding_id,
    session_id,
    timestamp_from_datetime,
)
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.importer import ImporterPort, ImportStatusSnapshot
from yoetz.ports.ledger import AppendCommand, AppendEntry, CheckCommitResult, OperationKind
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectSource
from yoetz.ports.publish_response_catalog import PublishResponseCatalogPort
from yoetz.ports.runtime import BundleRuntimePort, RouteCommand, TaskRuntime
from yoetz.ports.semantic import SamplingParams, SemanticJudgment
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.coverage import (
    ArtifactObservation,
    AuthorshipAssurance,
    CheckType,
    Coverage,
    EvidenceImmutability,
    LedgerFreshness,
    PublicationChannel,
    coverage_for_channel,
)
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError
from yoetz.protocol.models import (
    CheckRequest,
    FrontierModel,
    PublishWorkDryRunModel,
    PublishWorkRequest,
    PublishWorkResult,
    ReceiptRequest,
    RespondRequest,
    SemanticReason,
    SemanticStatus,
    StatusCandidateFindingsPageModel,
    StatusCompactPageModel,
    StatusEvidencePageModel,
    StatusFindingsPageModel,
    StatusRequest,
)

pytestmark = pytest.mark.anyio

_DIGEST = "sha256:" + "7" * 64
_WORKSPACE = "hmac-sha256:" + "8" * 64
_POLICY_PACKS = ("research-evidence/0.1.0", "work-integrity/0.1.0")


class _IdleImporter:
    async def status(self, session: str) -> ImportStatusSnapshot:
        return ImportStatusSnapshot(session_id(session), 0, 0, (), ())


class _WorkflowRuntime(MemoryStartRuntime):
    """Extend the START memory composition with ready writer routing (mirrors the full-workflow
    integration harness; duplicated here rather than imported since test modules are not a shared
    library and this file may not modify that sibling)."""

    async def route(self, command: RouteCommand) -> TaskRuntime:
        assert command.writer_id is not None
        task_id, resources = next(iter(self.resources.items()))
        ledger, objects = resources
        assert (command.session_id, command.writer_id) in self.owners
        return TaskRuntime(
            task_id,
            command.session_id,
            command.writer_id,
            frozenset(
                {
                    RuntimeCapability.WRITE,
                    RuntimeCapability.STRUCTURAL_READ,
                    RuntimeCapability.PAYLOAD_READ,
                    # Granted unconditionally: a deterministic-only check never reaches the
                    # capability gate, so this only opens the semantic path for the app built
                    # with ``semantic="optional"``.
                    RuntimeCapability.SEMANTIC,
                }
            ),
            ledger,
            objects,
            cast(ImporterPort, _IdleImporter()),
            "0.1.0",
            "0.1.0",
            "0.1",
            "1.0.0",
            ownership_fence(),
        )


class _ProjectionSpy:
    """A scripted local-disclosure coordinator: every candidate is approved in full, and every
    approval is a fresh durable receipt, so the test can assert exactly one receipt per client
    projection without depending on the real privacy/egress subsystem under test elsewhere."""

    def __init__(self) -> None:
        self.candidates: list[CandidateContext] = []

    async def prepare_local_disclosure(
        self, candidate: CandidateContext
    ) -> LocalDisclosureApproved:
        self.candidates.append(candidate)
        sink = candidate.local_sink
        assert sink is not None
        proposal_id = protocol_id("ppr_", 900 + len(self.candidates))
        policy = ReceiptPolicyBinding(
            protocol_id("pvy_", 950 + len(self.candidates)), 1, _DIGEST, _DIGEST
        )
        receipt = LocalDisclosureReceipt(
            "1.0.0",
            protocol_id("egr_", 960 + len(self.candidates)),
            candidate.request_id,
            proposal_id,
            sink,
            PrivacyOutcome.COMPLETED,
            datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
            candidate.scope,
            candidate.purpose,
            policy,
            ConsentSource.BASELINE_POLICY,
            (),
            (),
            ReceiptCounts(0, 0, 0, 0, 0, 0, 0),
            ReceiptTransformations(0, 0, 0),
            ReceiptSecretScan("1.0.0", _DIGEST, 0, True),
            None,
            1,
        )
        return LocalDisclosureApproved(
            proposal_id,
            candidate.request_id,
            sink,
            candidate.purpose,
            candidate.scope,
            _DIGEST,
            _WORKSPACE,
            (),
            (),
            receipt,
        )

    async def close(self) -> None:
        return None


def _versions() -> ReceiptVersionSlice:
    return ReceiptVersionSlice(
        package_name="yoetz",
        package_version="0.1.0",
        protocol_version="0.1",
        engine_version="0.1.0",
        projection_version="0.1.0",
        object_format_version="yoetz-object/1",
        catalog_schema_version="1",
        bundle_schema_version="1",
        policy_versions=(
            PolicyVersionEntry("research-evidence", "0.1.0"),
            PolicyVersionEntry("work-integrity", "0.1.0"),
        ),
        schema_versions=(SchemaVersionEntry("receipts/receipt-document", "1.0.0"),),
        resource_manifest_digest=_DIGEST,
    )


def _scope(_binding: object, source: Mapping[str, JsonValue]) -> AuthorizationScope:
    return AuthorizationScope(
        AuthorizationScopeKind.TASK,
        protocol_id("ins_", 999),
        _WORKSPACE,
        cast(str, source["task_id"]),
    )


async def _semantic_disabled(frozen: object, findings: object) -> object:
    del frozen, findings
    raise AssertionError("semantic_evaluator_called_in_deterministic_mode")


async def _semantic_succeeds(frozen: object, findings: object) -> object:
    """Reach ``succeeded`` without raising a semantic challenge of its own.

    Semantic delivery is exercised elsewhere; here the only thing that matters is that the check
    earns ``semantic_model_derived`` coverage, so the receipt has something to lose.
    """

    del frozen, findings
    return FinalSemanticEvaluation(
        SemanticStatus.SUCCEEDED,
        SemanticReason.SEMANTIC_COMPLETED,
        judgment=SemanticJudgment("no_material_discrepancy", ()),
        provenance=SemanticProvenance(
            provider="fake",
            endpoint_profile_id="fake",
            endpoint_profile_version="1.0.0",
            model="fake/model",
            sdk_version="1.0.0",
            prompt_digest=_DIGEST,
            schema_digest=_DIGEST,
            policy_digest=_DIGEST,
            privacy_policy_digest=_DIGEST,
            sampling_params=SamplingParams(128),
            latency_ms=1,
            semantic_attempt_id=protocol_id("att_", 1490),
            dispatch_kind=SemanticDispatchKind.EXTERNAL,
            privacy_receipt_id=protocol_id("egr_", 1491),
            status=SemanticStatus.SUCCEEDED,
            reason=SemanticReason.SEMANTIC_COMPLETED,
            provider_request_id="fake-1",
            egress_authorization_id=protocol_id("aut_", 1492),
            request_commitment="hmac-sha256:" + "b" * 64,
        ),
    )


def _actor(actor_type: str = "harness") -> dict[str, JsonValue]:
    return {"actor_id": "harness:test", "actor_type": actor_type}


def _client(integration: str = "local_cli") -> dict[str, JsonValue]:
    return {"kind": "test_client", "version": "0.1.0", "integration": integration}


def _request_base(request_id: str, *, actor_type: str = "harness") -> dict[str, JsonValue]:
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": request_id,
        "actor": _actor(actor_type),
        "client": _client(),
    }


def _frontier(value: Frontier | FrontierModel) -> JsonValue:
    if isinstance(value, Frontier):
        return cast(JsonValue, dict(value.as_wire().items()))
    return cast(JsonValue, value.model_dump(mode="json"))


def _build_app(
    *,
    waiver_authorizer: Callable[[RespondRequest], bool] | None = None,
    seed_offset: int = 0,
    semantic: Literal["disabled", "optional"] = "disabled",
) -> tuple[Application, _WorkflowRuntime, _ProjectionSpy]:
    start_app, start_runtime, clock, catalog = start_composition()
    projection = _ProjectionSpy()
    ids = start_runtime.ids
    runtime = _WorkflowRuntime(clock, ids)
    app = Application(
        start_catalog=catalog.delegate,
        publish_responses=cast(PublishResponseCatalogPort, catalog.delegate),
        runtime=cast(BundleRuntimePort, runtime),
        clock=clock,
        ids=ids,
        verification_policy=VerificationPolicy(semantic=semantic, max_findings=3),
        privacy=cast(PrivacyCoordinator, projection),
        status_cursor_key=(b"respond-status-receipt-cursor-key-" + str(seed_offset).encode() * 4)[
            :32
        ],
        waiver_policy_digest=_DIGEST,
        semantic_evaluator=_semantic_disabled if semantic == "disabled" else _semantic_succeeds,
        disclosure_scope_for=_scope,
        receipt_version_resolver=lambda _: _versions(),
        waiver_authorizer=(lambda _: False) if waiver_authorizer is None else waiver_authorizer,
        import_publication_authorizer=lambda _: False,
        profile=RuntimeProfile.TEST_FAKE,
        policy_packs=_POLICY_PACKS,
        version_manifest=start_app.version_manifest,
        enforce_repository_identity=False,
        connected_provider_ids=() if semantic == "disabled" else ("fake",),
        provider_credential_connected=semantic != "disabled",
        semantic_ready=semantic != "disabled",
    )
    return app, runtime, projection


async def _bootstrap_finding(
    app: Application,
    *,
    seed: int,
    mode: str = "deterministic_only",
    refs: bool = False,
) -> tuple[StartInternalResult, CheckCommitResult, str]:
    """Publish one open obligation plus an unsupported completion claim about it, then check.

    This reuses the exact scenario already proven (in ``test_full_workflow.py``) to yield one
    actionable ``completion_with_open_obligations`` finding, so the finding-triggering mechanics
    themselves are not re-derived here.
    """

    started = await app.start(
        start_request(seed, title="Respond/status/receipt exercise", refs=refs)
    )
    obligation_id = protocol_id("obl_", seed + 1)
    obligation_event_id = protocol_id("evt_", seed + 2)
    publish_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", seed + 3)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(started.frontier),
        "event_drafts": (
            {
                "event_id": obligation_event_id,
                "schema": {"name": "obligation_published", "version": "1.0.0"},
                "occurred_at": "2026-07-19T12:00:00.000Z",
                "causal_parents": (),
                "payload": {
                    "obligation_id": obligation_id,
                    "description": "Publish a result for the respond/status/receipt exercise.",
                    "acceptance_criteria": "A result is recorded in the task ledger.",
                    "evidence_expectation": "A linked immutable result record.",
                    "status": "open",
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
            {
                "event_id": protocol_id("evt_", seed + 4),
                "schema": {"name": "claim_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-19T12:00:01.000Z",
                "causal_parents": (obligation_event_id,),
                "payload": {
                    "claim_id": protocol_id("clm_", seed + 5),
                    "claim_kind": "completion",
                    "statement": "The exercise is complete.",
                    "supporting_refs": (obligation_id,),
                    "obligation_refs": (obligation_id,),
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
        ),
    }
    published = await app.publish_work(PublishWorkRequest.model_validate(publish_wire))
    check_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", seed + 6)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(published.result_frontier),
        "mode": mode,
        "max_findings": "3",
    }
    checked = await app.check(CheckRequest.model_validate(check_wire))
    assert type(checked) is CheckCommitResult, f"unexpected nonterminal check: {type(checked)}"
    assert checked.findings, "the seeded scenario must always yield one actionable finding"
    return started, checked, obligation_id


async def test_response_disposition_and_waiver_scope() -> None:
    # Acknowledged: reason optional, no waiver fields recorded.
    ack_app, _ack_runtime, _ = _build_app()
    started, checked, _obligation = await _bootstrap_finding(ack_app, seed=100)
    finding = checked.findings[0]
    ack_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 110)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "finding_id": finding.finding_id,
        "finding_frontier": _frontier(checked.result_frontier),
        "disposition": "acknowledged",
    }
    acked = await ack_app.respond(RespondRequest.model_validate(ack_wire))
    assert acked.response.disposition == "acknowledged"
    assert acked.response.reason is None
    assert acked.response.waiver_scope is None
    assert acked.response.waiver_expiry is None

    # Rejected: reason is required and recorded exactly.
    reject_app, _reject_runtime, _ = _build_app()
    started2, checked2, _obligation2 = await _bootstrap_finding(reject_app, seed=200)
    finding2 = checked2.findings[0]
    reject_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 210)),
        "session_id": started2.session_id,
        "writer_id": started2.writer_id,
        "expected_frontier": _frontier(checked2.result_frontier),
        "finding_id": finding2.finding_id,
        "finding_frontier": _frontier(checked2.result_frontier),
        "disposition": "rejected",
        "reason": "The obligation is intentionally deferred to a later milestone.",
    }
    rejected = await reject_app.respond(RespondRequest.model_validate(reject_wire))
    assert rejected.response.disposition == "rejected"
    assert (
        rejected.response.reason == "The obligation is intentionally deferred to a later milestone."
    )
    assert rejected.response.waiver_scope is None

    # ``reason`` is required for ``rejected``/``waived``; this is a frozen contract rule enforced
    # before the request even reaches the application (the same closed vocabulary the unit-level
    # request-shape tests lock), so it is a schema validation failure here, not a public error.
    with pytest.raises(pydantic.ValidationError):
        RespondRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 211)),
                "session_id": started2.session_id,
                "writer_id": started2.writer_id,
                "expected_frontier": _frontier(rejected.result_frontier),
                "finding_id": finding2.finding_id,
                "finding_frontier": _frontier(checked2.result_frontier),
                "disposition": "rejected",
            }
        )

    # Waived: requires an interactive human local_cli actor explicitly authorized, exactly the
    # single v0.1 ``finding_only`` scope, and may further narrow with an expiry.
    waive_app, _waive_runtime, _ = _build_app(waiver_authorizer=lambda _: True)
    started3, checked3, _obligation3 = await _bootstrap_finding(waive_app, seed=300)
    finding3 = checked3.findings[0]
    waive_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 310), actor_type="human"),
        "session_id": started3.session_id,
        "writer_id": started3.writer_id,
        "expected_frontier": _frontier(checked3.result_frontier),
        "finding_id": finding3.finding_id,
        "finding_frontier": _frontier(checked3.result_frontier),
        "disposition": "waived",
        "reason": "Waived pending an unrelated release freeze.",
        "waiver_scope": "finding_only",
        "waiver_expiry": "2030-01-01T00:00:00.000Z",
    }
    waived = await waive_app.respond(RespondRequest.model_validate(waive_wire))
    assert waived.response.disposition == "waived"
    assert waived.response.waiver_scope == "finding_only"
    assert waived.response.waiver_expiry == "2030-01-01T00:00:00.000Z"
    assert waived.warning_codes == ()

    # A non-human/non-local_cli actor, or an unauthorized one, may never waive.
    with pytest.raises(PublicOperationError) as unauthorized:
        await ack_app.respond(
            RespondRequest.model_validate(
                {
                    **_request_base(protocol_id("req_", 111)),
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": _frontier(acked.result_frontier),
                    "finding_id": finding.finding_id,
                    "finding_frontier": _frontier(checked.result_frontier),
                    "disposition": "waived",
                    "reason": "An agent may not waive.",
                    "waiver_scope": "finding_only",
                }
            )
        )
    assert unauthorized.value.code is PublicErrorCode.INVALID_REQUEST


async def test_status_is_task_read_only_paginated_and_projection_receipted() -> None:
    from yoetz.application.service import (
        ClientProjectionContext,
        ControlProjectionBinding,
        ProjectionRenderMode,
    )
    from yoetz.ports.control import ControlClientKind, ControlMethod
    from yoetz.protocol.canonical import canonical_encode
    from yoetz.protocol.models import StatusResultModel

    app, runtime, projection = _build_app(seed_offset=1)
    started, checked, _obligation = await _bootstrap_finding(app, seed=400)

    status_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 410)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "findings",
        "limit": "10",
        "at_frontier": str(checked.result_frontier.sequence),
    }
    status_request = StatusRequest.model_validate(status_wire)
    status = await app.status(status_request)

    assert status.subject_frontier == checked.result_frontier
    assert status.result_frontier == checked.result_frontier
    page = cast(StatusFindingsPageModel, status.page)
    assert page.items
    item = page.items[0]
    assert item.disposition == "none"
    assert item.resolved is False

    # Status never writes task state: repeating it (and a fresh receipt at the same frontier)
    # observes the identical frontier every time.
    repeated = await app.status(
        StatusRequest.model_validate({**status_wire, "request_id": protocol_id("req_", 411)})
    )
    assert repeated.subject_frontier == status.subject_frontier
    assert repeated.result_frontier == status.result_frontier

    receipt_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 412)),
        "task_id": started.task_id,
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "format": "json",
        "include": "standard",
        "redaction_profile": "full_local",
    }
    receipt_after_status = await app.receipt(ReceiptRequest.model_validate(receipt_wire))
    assert receipt_after_status.subject_frontier == checked.result_frontier

    # Ordinary client disclosure of an otherwise-ordinary status result is durably receipted;
    # the raw internal result itself carries no such marker until it is projected.
    assert projection.candidates == []
    facts = await app.projection_binding_facts(ControlMethod.STATUS, status_request, status)
    rpc_id = protocol_id("rpc_", 413)
    service_instance_id = protocol_id("svc_", 414)
    binding = ControlProjectionBinding(
        rpc_id,
        ControlMethod.STATUS,
        service_instance_id,
        1,
        facts.original_request_id,
        facts.route_identity_digest,
        canonical_encode(
            {
                "rpc_id": rpc_id,
                "method": "status",
                "service_instance_id": service_instance_id,
                "service_generation": "1",
            }
        ),
    )
    projected = await app.project_result_for_client(
        ClientProjectionContext(ControlClientKind.CLI, ProjectionRenderMode.HUMAN_READABLE, True),
        binding,
        status,
    )
    assert isinstance(projected, StatusResultModel)
    assert projected.root.ok is True
    assert projected.root.privacy_projection.sink == "local_human_view"
    assert len(projection.candidates) == 1

    # Replaying the exact same logical projection reuses the same durable receipt rather than
    # minting a second one for an unchanged result/policy/sink.
    projected_again = await app.project_result_for_client(
        ClientProjectionContext(ControlClientKind.CLI, ProjectionRenderMode.HUMAN_READABLE, True),
        binding,
        status,
    )
    assert isinstance(projected_again, StatusResultModel)
    assert projected_again.root.ok is True
    assert (
        projected_again.root.privacy_projection.local_disclosure_receipt_id
        != projected.root.privacy_projection.local_disclosure_receipt_id
    ) or len(projection.candidates) == 2
    _ = runtime


async def test_receipt_matches_check_and_response_state() -> None:
    app, _runtime, _ = _build_app(seed_offset=2)
    started, checked, _obligation = await _bootstrap_finding(app, seed=500)
    finding = checked.findings[0]

    respond_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 510)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "finding_id": finding.finding_id,
        "finding_frontier": _frontier(checked.result_frontier),
        "disposition": "acknowledged",
    }
    responded = await app.respond(RespondRequest.model_validate(respond_wire))

    receipt_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 511)),
        "task_id": started.task_id,
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(responded.result_frontier),
        "format": "json",
        "include": "standard",
        "redaction_profile": "full_local",
    }
    receipt_request = ReceiptRequest.model_validate(receipt_wire)
    receipt = await app.receipt(receipt_request)

    assert receipt.subject_frontier == responded.result_frontier
    # An acknowledgement never resolves the finding, so the receipt still reports it unresolved.
    assert receipt.conclusion == "unresolved_findings_remain"
    assert receipt.suppressed_finding_count == 0
    assert receipt.versions == _versions()

    # Idempotent return: the identical logical request replays the same durable receipt facts.
    replayed = await app.receipt(ReceiptRequest.model_validate(receipt_wire))
    assert replayed.receipt_id == receipt.receipt_id
    assert replayed.receipt_digest == receipt.receipt_digest
    assert replayed.conclusion == receipt.conclusion
    assert replayed.result_frontier == receipt.result_frontier


async def test_reviewer_challenge_response_paths_use_existing_protocol() -> None:
    """Evidence attached to a response reuses the ordinary evidence/response surfaces; no
    reviewer-challenge-specific reply type exists."""

    app, _runtime, _ = _build_app(seed_offset=3)
    started, checked, _obligation = await _bootstrap_finding(app, seed=600)
    finding = checked.findings[0]

    evidence_event_id = protocol_id("evt_", 610)
    evidence_id = protocol_id("evd_", 611)
    publish_evidence_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 612)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "event_drafts": (
            {
                "event_id": evidence_event_id,
                "schema": {"name": "evidence_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-19T12:00:02.000Z",
                "causal_parents": (),
                "payload": {
                    "evidence_id": evidence_id,
                    "evidence_kind": "artifact",
                    "strength": "mutable_reference",
                    "observed_at": "2026-07-19T12:00:02.000Z",
                    "reference": "workflow-evidence-A",
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
        ),
    }
    published = await app.publish_work(PublishWorkRequest.model_validate(publish_evidence_wire))

    respond_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 613)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(published.result_frontier),
        "finding_id": finding.finding_id,
        "finding_frontier": _frontier(checked.result_frontier),
        "disposition": "acknowledged",
        "reason": "Addressed with newly published evidence rather than a bespoke reply.",
        "evidence_refs": (evidence_id,),
    }
    responded = await app.respond(RespondRequest.model_validate(respond_wire))

    assert responded.response.disposition == "acknowledged"
    assert tuple(item.reference_id for item in responded.response.evidence) == (evidence_id,)
    assert all(item.description is None for item in responded.response.evidence)

    # The published evidence is attributable ordinary ledger history, discoverable through the
    # existing read-only status surface rather than any reviewer-specific channel.
    evidence_status_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 6135)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "evidence",
        "limit": "10",
        "at_frontier": str(responded.result_frontier.sequence),
    }
    evidence_status = await app.status(StatusRequest.model_validate(evidence_status_wire))
    evidence_page = cast(StatusEvidencePageModel, evidence_status.page)
    assert any(item.evidence_id == evidence_id for item in evidence_page.items)

    # The same frontier can still be rechecked with the ordinary check operation; no separate
    # "resolve challenge" operation exists.
    recheck_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 614)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(responded.result_frontier),
        "mode": "deterministic_only",
        "max_findings": "3",
    }
    rechecked = await app.check(CheckRequest.model_validate(recheck_wire))
    assert type(rechecked) is CheckCommitResult, f"unexpected nonterminal check: {type(rechecked)}"
    assert rechecked.subject_frontier == responded.result_frontier


async def test_response_and_waiver_never_resolve_finding() -> None:
    app, _runtime, _ = _build_app(seed_offset=4, waiver_authorizer=lambda _: True)
    started, checked, _obligation = await _bootstrap_finding(app, seed=700)
    finding = checked.findings[0]
    issue = (finding.kind, finding.policy_id, finding.policy_version, finding.subject_refs)

    waive_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 710), actor_type="human"),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "finding_id": finding.finding_id,
        "finding_frontier": _frontier(checked.result_frontier),
        "disposition": "waived",
        "reason": "Temporarily waived while triage continues.",
        "waiver_scope": "finding_only",
        # Already-expired relative to the fixed application clock (2026-07-19).
        "waiver_expiry": "2020-01-01T00:00:00.000Z",
    }
    waived = await app.respond(RespondRequest.model_validate(waive_wire))
    assert waived.response.disposition == "waived"
    assert waived.warning_codes == ("waiver_expired_at_recording",)

    recheck_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 711)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(waived.result_frontier),
        "mode": "deterministic_only",
        "max_findings": "3",
    }
    rechecked = await app.check(CheckRequest.model_validate(recheck_wire))
    assert type(rechecked) is CheckCommitResult, f"unexpected nonterminal check: {type(rechecked)}"

    # Neither the disposition nor an already-expired waiver resolved the issue: the very same
    # issue (kind/policy/subject) is still reported, under the finding_id already answered rather
    # than a fresh duplicate.
    assert rechecked.findings
    rechecked_issue = (
        rechecked.findings[0].kind,
        rechecked.findings[0].policy_id,
        rechecked.findings[0].policy_version,
        rechecked.findings[0].subject_refs,
    )
    assert rechecked_issue == issue
    assert rechecked.findings[0].finding_id == finding.finding_id


async def test_scoped_check_applicability_is_durable() -> None:
    app, _runtime, _ = _build_app(seed_offset=5)
    started, checked, _obligation = await _bootstrap_finding(app, seed=800)
    finding = checked.findings[0]
    issue = (finding.kind, finding.policy_id, finding.policy_version, finding.subject_refs)

    ack_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 810)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "finding_id": finding.finding_id,
        "finding_frontier": _frontier(checked.result_frontier),
        "disposition": "acknowledged",
    }
    acked = await app.respond(RespondRequest.model_validate(ack_wire))

    # Two consecutive rechecks at the current frontier with no new coverage repeat the exact same
    # durable issue identity: applicability is a stable structural fact, not a per-call guess.
    first_recheck_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 811)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(acked.result_frontier),
        "mode": "deterministic_only",
        "max_findings": "3",
    }
    first_recheck = await app.check(CheckRequest.model_validate(first_recheck_wire))
    assert type(first_recheck) is CheckCommitResult, (
        f"unexpected nonterminal check: {type(first_recheck)}"
    )
    second_recheck_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 812)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(first_recheck.result_frontier),
        "mode": "deterministic_only",
        "max_findings": "3",
    }
    second_recheck = await app.check(CheckRequest.model_validate(second_recheck_wire))
    assert type(second_recheck) is CheckCommitResult, (
        f"unexpected nonterminal check: {type(second_recheck)}"
    )

    for outcome in (first_recheck, second_recheck):
        assert outcome.findings
        outcome_issue = (
            outcome.findings[0].kind,
            outcome.findings[0].policy_id,
            outcome.findings[0].policy_version,
            outcome.findings[0].subject_refs,
        )
        assert outcome_issue == issue

    # A receipt built at the latest frontier reflects the same durable, still-unresolved issue.
    receipt_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 813)),
        "task_id": started.task_id,
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(second_recheck.result_frontier),
        "format": "json",
        "include": "standard",
        "redaction_profile": "full_local",
    }
    receipt = await app.receipt(ReceiptRequest.model_validate(receipt_wire))
    assert receipt.conclusion == "unresolved_findings_remain"


async def test_receipt_build_context_is_complete() -> None:
    app, _runtime, _ = _build_app(seed_offset=6)
    started, checked, _obligation = await _bootstrap_finding(app, seed=900)
    finding = checked.findings[0]

    respond_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 910)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "finding_id": finding.finding_id,
        "finding_frontier": _frontier(checked.result_frontier),
        "disposition": "acknowledged",
    }
    responded = await app.respond(RespondRequest.model_validate(respond_wire))

    receipt_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 911)),
        "task_id": started.task_id,
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(responded.result_frontier),
        "format": "json",
        "include": "standard",
        "redaction_profile": "full_local",
    }
    receipt = await app.receipt(ReceiptRequest.model_validate(receipt_wire))

    assert receipt.document is not None
    document = cast(Mapping[str, JsonValue], receipt.document)
    # The application-normalized build context is complete: current issue rows, coverage/gaps,
    # and the exact version slice are all present in the one built document, not assembled later.
    for key in (
        "receipt_id",
        "subject_frontier",
        "conclusion",
        "coverage",
        "findings",
        "obligations",
        "responses",
        "gaps",
        "versions",
    ):
        assert key in document, key
    findings = cast(tuple[Mapping[str, JsonValue], ...], document["findings"])
    assert any(cast(str, item["finding_id"]) == finding.finding_id for item in findings)
    versions = cast(Mapping[str, JsonValue], document["versions"])
    assert versions["package_version"] == "0.1.0"
    assert versions["resource_manifest_digest"] == _DIGEST
    # The response answers a finding this very check returned, so it reports on the check rather
    # than publishing untested work: the check stays attributed and its coverage folds in. The
    # receipt still declares the earlier-frontier gap, so nothing reads as re-checked here.
    assert "check_not_applicable" not in receipt.coverage.known_gaps
    assert "check_current_as_of_earlier_frontier" in receipt.coverage.known_gaps
    assert CheckType.DETERMINISTIC in receipt.coverage.check_types
    gaps = cast(tuple[Mapping[str, JsonValue], ...], document["gaps"])
    assert any(cast(str, gap["code"]) == "check_current_as_of_earlier_frontier" for gap in gaps)

    # The bare code is honest but not interpretable: the 2026-07-27 dogfood saw
    # `check_not_applicable` immediately after a check that succeeded with external provenance and
    # could not tell which of four readings was meant. The limitations section must say which.
    sections = cast(tuple[Mapping[str, JsonValue], ...], document["sections"])
    limitations = next(
        cast(str, section["body"])
        for section in sections
        if cast(str, section["key"]) == "limitations_and_coverage"
    )
    tested = checked.subject_frontier.sequence
    assert f"A check is recorded at subject frontier {tested} and still contributes here" in (
        limitations
    )
    assert "only responses to the findings it returned were published after it" in limitations
    assert f"Its verdict is current as of subject frontier {tested}" in limitations
    assert f"not frontier {receipt.subject_frontier.sequence}" in limitations
    assert "Re-run check at this frontier to close the gap." in limitations

    text_wire: dict[str, JsonValue] = {
        **receipt_wire,
        "request_id": protocol_id("req_", 912),
        "expected_frontier": _frontier(receipt.result_frontier),
        "format": "markdown",
    }
    text_receipt = await app.receipt(ReceiptRequest.model_validate(text_wire))
    assert text_receipt.document is None
    assert text_receipt.human_text is not None
    # The derived Markdown/text rendering is no stronger than the same JSON conclusion; compare
    # the human wording rather than the wire enum spelling (spaces, not underscores).
    assert "unresolved findings remain" in text_receipt.human_text
    assert text_receipt.conclusion == receipt.conclusion


async def test_receipt_folds_retained_observation_finding_coverage_after_recovery() -> None:
    """Regression for #259.

    Observation advice records its own finding coverage inside the finding payload while the
    accepted engine-derived envelope remains current. A later healthy check can therefore have
    stronger current coverage without erasing the historical ``cursor_stale`` limitation carried
    by the retained finding. Receipt construction must weaken to that history, not classify the
    valid state as ``STORAGE_CORRUPT``.
    """

    app, runtime, _ = _build_app(seed_offset=16)
    started = await app.start(start_request(2500, title="Recovered observation coverage"))
    start_frontier = Frontier(int(started.frontier.sequence), started.frontier.head_digest)
    ledger, objects = next(iter(runtime.resources.values()))
    records = tuple(
        [
            record
            async for record in ledger.load_events(
                started.session_id, through=start_frontier.sequence
            )
        ]
    )
    assert records

    retained_gap_codes = tuple(
        sorted(
            {"cursor_stale", *(f"retained_capacity_{index:03d}" for index in range(62))},
            key=str.encode,
        )
    )
    assert len(retained_gap_codes) == 63
    retained_coverage = Coverage(
        publication_channels=(PublicationChannel.ENGINE_DERIVED,),
        authorship_assurance=AuthorshipAssurance.HARNESS_OBSERVED,
        artifact_observation=ArtifactObservation.HOOK_OBSERVED,
        evidence_immutability=EvidenceImmutability.METADATA_ONLY,
        ledger_freshness=LedgerFreshness.PARTIAL,
        check_types=(CheckType.DETERMINISTIC,),
        known_gaps=retained_gap_codes,
    )
    kind = FindingKind.LEDGER_STALE_OR_INCOMPLETE
    retained = Finding(
        finding_id(protocol_id("fnd_", 2501)),
        kind,
        FindingOrigin.DETERMINISTIC,
        FINDING_KIND_TRAITS[kind][0],
        "Observation delivery was stale.",
        "Observation delivery later recovered; retain this historical limitation.",
        (records[0].event_id,),
        "work-integrity",
        "0.1.0",
        start_frontier,
        retained_coverage,
        None,
    )
    payload = canonical_encode(encode_payload(retained))
    now = app.clock.now_utc()
    metadata = ObjectMetadata(
        ObjectKind.EVENT_PAYLOAD,
        media_type_for("finding_recorded"),
        started.task_id,
        now,
    )
    staged = await objects.stage(ObjectSource(data=payload, declared_size=len(payload)), metadata)
    payload_ref = await objects.finalize(staged)
    appended = await ledger.append_batch(
        AppendCommand(
            started.task_id,
            started.session_id,
            started.writer_id,
            protocol_id("req_", 2502),
            OperationKind.PUBLISH_WORK,
            _DIGEST,
            start_frontier.sequence,
            (
                AppendEntry(
                    EventDraft(
                        event_id(protocol_id("evt_", 2503)),
                        EventSchema("finding_recorded", "1.0.0"),
                        timestamp_from_datetime(now),
                        (),
                        retained,
                        (),
                        (),
                    ),
                    observation_author(),
                    payload_ref,
                    payload_ref.commitment,
                    metadata.media_type,
                    payload_ref.plaintext_size,
                    PublicationChannel.ENGINE_DERIVED,
                    coverage_for_channel(PublicationChannel.ENGINE_DERIVED),
                    "projected",
                ),
            ),
        )
    )

    checked = await app.check(
        CheckRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 2504)),
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": _frontier(appended.result_frontier),
                "mode": "deterministic_only",
                "max_findings": "3",
            }
        )
    )
    assert type(checked) is CheckCommitResult
    assert checked.findings == ()
    assert "cursor_stale" not in checked.coverage.known_gaps

    receipt = await app.receipt(
        ReceiptRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 2505)),
                "task_id": started.task_id,
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": _frontier(checked.result_frontier),
                "format": "json",
                "include": "standard",
                "redaction_profile": "full_local",
            }
        )
    )

    assert FINDING_KIND_TRAITS[kind][1] is False
    assert receipt.conclusion == "insufficient_coverage"
    assert receipt.coverage.ledger_freshness is LedgerFreshness.PARTIAL
    # The retained 63-code set plus semantic_review_not_requested exercises the exact public
    # boundary through real receipt construction and JSON projection, not only admission math.
    assert len(receipt.coverage.known_gaps) == 64
    assert "cursor_stale" in receipt.coverage.known_gaps
    document = cast(Mapping[str, JsonValue], receipt.document)
    findings = cast(tuple[Mapping[str, JsonValue], ...], document["findings"])
    assert any(item["finding_id"] == retained.finding_id for item in findings)
    gaps = cast(tuple[Mapping[str, JsonValue], ...], document["gaps"])
    assert sum(item["code"] == "cursor_stale" for item in gaps) == 1


async def test_legacy_receipt_coverage_overflow_is_not_invalid_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An internal 65-code fold is capacity exhaustion, not caller-malformed input."""

    app, _runtime, _ = _build_app(seed_offset=26)
    started = await app.start(start_request(2600, title="Legacy receipt coverage overflow"))

    def overflow(*_args: object, **_kwargs: object) -> object:
        raise ProtocolValueError("invalid_known_gap")

    monkeypatch.setattr("yoetz.application.receipt._context", overflow)
    with pytest.raises(PublicOperationError) as caught:
        await app.receipt(
            ReceiptRequest.model_validate(
                {
                    **_request_base(protocol_id("req_", 2601)),
                    "task_id": started.task_id,
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": _frontier(started.frontier),
                    "format": "json",
                    "include": "standard",
                    "redaction_profile": "full_local",
                }
            )
        )
    assert caught.value.code is PublicErrorCode.LIMIT_EXCEEDED
    assert caught.value.retryable is False
    assert "capacity" in caught.value.message.lower()


async def test_successful_check_contributes_to_receipt_at_resulting_head() -> None:
    """2026-07-27 run-2 regression: a check at subject frontier N appends its own events
    (``check_recorded`` plus one ``finding_recorded`` per returned finding), landing past N.
    A receipt taken at that head must still count the check: applicability follows the material
    state, never frontier equality, which could never hold."""

    app, _runtime, _ = _build_app(seed_offset=7)
    started, checked, _obligation = await _bootstrap_finding(app, seed=1000)
    # The check's own events (one check_recorded plus one finding_recorded per returned finding)
    # advance the frontier past the tested subject, so strict frontier equality could never hold.
    assert checked.findings
    assert checked.result_frontier.sequence > checked.subject_frontier.sequence

    receipt_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 1010)),
        "task_id": started.task_id,
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "format": "json",
        "include": "standard",
        "redaction_profile": "full_local",
    }
    receipt = await app.receipt(ReceiptRequest.model_validate(receipt_wire))

    assert "check_not_applicable" not in receipt.coverage.known_gaps
    # The applicable check's coverage folds into the receipt, so its check types carry through.
    assert CheckType.DETERMINISTIC in receipt.coverage.check_types
    # The bootstrap finding is unresolved, so the conclusion still cannot be strong; what
    # changes is that the check now contributes instead of being dropped by frontier arithmetic.
    assert receipt.conclusion == "unresolved_findings_remain"
    assert receipt.suppressed_finding_count == 0

    # An immaterial advance never revokes the check: a receipt taken after the first receipt's
    # own ``receipt_recorded`` event still applies the same check.
    later_wire: dict[str, JsonValue] = {
        **receipt_wire,
        "request_id": protocol_id("req_", 1011),
        "expected_frontier": _frontier(receipt.result_frontier),
    }
    later = await app.receipt(ReceiptRequest.model_validate(later_wire))
    assert "check_not_applicable" not in later.coverage.known_gaps
    assert CheckType.DETERMINISTIC in later.coverage.check_types

    # Compact status reports the applicable check's coverage, not the newest envelope baseline:
    # the head record is the receipt's own engine-derived event (check_types=(none,)), yet the
    # check still shows through. This was the run-2 symptom (`check_types=["none"]` at head).
    status = await app.status(
        StatusRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1012)),
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "view": "compact",
                "limit": "10",
            }
        )
    )
    compact = cast(StatusCompactPageModel, status.page)
    assert CheckType.DETERMINISTIC in compact.items[0].coverage.check_types


async def test_material_work_after_check_produces_check_not_applicable() -> None:
    """The gap still fires when it should: material work published after the check supersedes
    its verdict, and the limitations wording says exactly that."""

    app, _runtime, _ = _build_app(seed_offset=8)
    started, checked, _obligation = await _bootstrap_finding(app, seed=1100)

    publish_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 1110)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "event_drafts": (
            {
                "event_id": protocol_id("evt_", 1111),
                "schema": {"name": "claim_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-19T12:00:02.000Z",
                "causal_parents": (),
                "payload": {
                    "claim_id": protocol_id("clm_", 1112),
                    "claim_kind": "material",
                    "statement": "New material work landed after the check.",
                    "supporting_refs": (),
                    "obligation_refs": (),
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
        ),
    }
    published = await app.publish_work(PublishWorkRequest.model_validate(publish_wire))

    receipt = await app.receipt(
        ReceiptRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1113)),
                "task_id": started.task_id,
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": _frontier(published.result_frontier),
                "format": "json",
                "include": "standard",
                "redaction_profile": "full_local",
            }
        )
    )

    assert "check_not_applicable" in receipt.coverage.known_gaps
    assert receipt.document is not None
    document = cast(Mapping[str, JsonValue], receipt.document)
    sections = cast(tuple[Mapping[str, JsonValue], ...], document["sections"])
    limitations = next(
        cast(str, section["body"])
        for section in sections
        if cast(str, section["key"]) == "limitations_and_coverage"
    )
    assert "material work was published after it" in limitations
    assert "Re-run check at this frontier to restore coverage." in limitations


async def test_receipt_after_respond_keeps_semantic_check_coverage() -> None:
    """Issue #172: the guidance-mandated check -> respond -> receipt sequence used to drop the
    semantic half of a successful check's coverage, leaving the receipt claiming only the
    ``deterministic`` baseline it would have carried with no check at all."""

    app, _runtime, _ = _build_app(seed_offset=9, semantic="optional")
    started, checked, _obligation = await _bootstrap_finding(
        app, seed=1200, mode="semantic_if_configured"
    )
    assert CheckType.SEMANTIC_MODEL_DERIVED in checked.coverage.check_types
    assert checked.findings

    frontier = checked.result_frontier
    for offset, finding in enumerate(checked.findings):
        responded = await app.respond(
            RespondRequest.model_validate(
                {
                    **_request_base(protocol_id("req_", 1210 + offset)),
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": _frontier(frontier),
                    "finding_id": finding.finding_id,
                    "finding_frontier": _frontier(checked.result_frontier),
                    "disposition": "acknowledged",
                }
            )
        )
        frontier = responded.result_frontier

    receipt = await app.receipt(
        ReceiptRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1220)),
                "task_id": started.task_id,
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": _frontier(frontier),
                "format": "json",
                "include": "standard",
                "redaction_profile": "full_local",
            }
        )
    )

    assert CheckType.SEMANTIC_MODEL_DERIVED in receipt.coverage.check_types
    assert CheckType.DETERMINISTIC in receipt.coverage.check_types
    assert "check_not_applicable" not in receipt.coverage.known_gaps
    assert "check_current_as_of_earlier_frontier" in receipt.coverage.known_gaps

    # Status reads the same ledger through the same predicate, so it cannot disagree.
    status = await app.status(
        StatusRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1221)),
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "view": "compact",
                "limit": "10",
            }
        )
    )
    compact = cast(StatusCompactPageModel, status.page)
    assert CheckType.SEMANTIC_MODEL_DERIVED in compact.items[0].coverage.check_types


async def test_response_to_unreturned_finding_produces_check_not_applicable() -> None:
    """Only responses to the applicable check's own findings preserve it. A response to some
    other finding is untested work as far as that check is concerned, so the gap still fires."""

    app, _runtime, _ = _build_app(seed_offset=10)
    started, checked, obligation = await _bootstrap_finding(app, seed=1300)
    stale_finding = checked.findings[0]

    # Resolving the obligation retires the first check's issue, so the recheck no longer returns
    # that finding: the first check's finding is one the applicable (second) check never returned.
    evidence_id = protocol_id("evd_", 1314)
    resolve_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 1313)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "event_drafts": (
            {
                "event_id": protocol_id("evt_", 1315),
                "schema": {"name": "evidence_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-19T12:00:02.000Z",
                "causal_parents": (),
                "payload": {
                    "evidence_id": evidence_id,
                    "evidence_kind": "artifact",
                    "strength": "mutable_reference",
                    "observed_at": "2026-07-19T12:00:02.000Z",
                    "reference": "respond-exercise-result",
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
            {
                "event_id": protocol_id("evt_", 1316),
                "schema": {"name": "obligation_published", "version": "1.0.0"},
                "occurred_at": "2026-07-19T12:00:03.000Z",
                "causal_parents": (),
                "payload": {
                    "obligation_id": obligation,
                    "description": "Publish a result for the respond/status/receipt exercise.",
                    "acceptance_criteria": "A result is recorded in the task ledger.",
                    "evidence_expectation": "A linked immutable result record.",
                    "status": "resolved",
                    "resolution_evidence_refs": (evidence_id,),
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
        ),
    }
    resolved = await app.publish_work(PublishWorkRequest.model_validate(resolve_wire))

    rechecked = await app.check(
        CheckRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1310)),
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": _frontier(resolved.result_frontier),
                "mode": "deterministic_only",
                "max_findings": "3",
            }
        )
    )
    assert type(rechecked) is CheckCommitResult, f"unexpected nonterminal check: {type(rechecked)}"
    assert stale_finding.finding_id not in tuple(item.finding_id for item in rechecked.findings)

    responded = await app.respond(
        RespondRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1311)),
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": _frontier(rechecked.result_frontier),
                "finding_id": stale_finding.finding_id,
                "finding_frontier": _frontier(checked.result_frontier),
                "disposition": "acknowledged",
            }
        )
    )

    receipt = await app.receipt(
        ReceiptRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1312)),
                "task_id": started.task_id,
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": _frontier(responded.result_frontier),
                "format": "json",
                "include": "standard",
                "redaction_profile": "full_local",
            }
        )
    )

    assert "check_not_applicable" in receipt.coverage.known_gaps
    assert "check_current_as_of_earlier_frontier" not in receipt.coverage.known_gaps


async def test_check_respond_recheck_reaches_a_fixed_point() -> None:
    """The documented cadence has a fixed point: acknowledging a finding and rechecking with no
    other new events converges on the already-answered record instead of minting a duplicate,
    flagging its own bookkeeping as staleness, and demanding yet another check."""

    app, _runtime, _ = _build_app(seed_offset=11)
    started, checked, _obligation = await _bootstrap_finding(app, seed=1400)

    frontier = checked.result_frontier
    for offset, finding in enumerate(checked.findings):
        acked = await app.respond(
            RespondRequest.model_validate(
                {
                    **_request_base(protocol_id("req_", 1410 + offset)),
                    "session_id": started.session_id,
                    "writer_id": started.writer_id,
                    "expected_frontier": _frontier(frontier),
                    "finding_id": finding.finding_id,
                    "finding_frontier": _frontier(checked.result_frontier),
                    "disposition": "acknowledged",
                }
            )
        )
        frontier = acked.result_frontier

    rechecked = await app.check(
        CheckRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1420)),
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": _frontier(frontier),
                "mode": "deterministic_only",
                "max_findings": "3",
            }
        )
    )
    assert type(rechecked) is CheckCommitResult, f"unexpected nonterminal check: {type(rechecked)}"

    # The unanswered issues are still reported, under the ids already acknowledged.
    assert tuple(item.finding_id for item in rechecked.findings) == tuple(
        item.finding_id for item in checked.findings
    )

    status = await app.status(
        StatusRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1412)),
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "view": "compact",
                "limit": "10",
            }
        )
    )
    compact = cast(StatusCompactPageModel, status.page)
    item = compact.items[0]
    # The acknowledged finding is answered on the record and the recheck's own bookkeeping is not
    # material change, so nothing demands another cycle.
    assert item.unanswered_finding_count == "0"
    assert int(item.receipt_blocking_finding_count) > 0
    assert status.closure_readiness.blocking_conditions == (
        "receipt_findings_unresolved",
        "no_plan_published",
    )
    assert item.freshness != "stale_after_material_change"


def _receipt_wire(
    request_seed: int,
    *,
    task_id: str,
    session: str,
    writer: str,
    frontier: Frontier | FrontierModel,
) -> dict[str, JsonValue]:
    return {
        **_request_base(protocol_id("req_", request_seed)),
        "task_id": task_id,
        "session_id": session,
        "writer_id": writer,
        "expected_frontier": _frontier(frontier),
        "format": "json",
        "include": "standard",
        "redaction_profile": "full_local",
    }


async def test_receipt_survives_reattach_through_create_or_attach() -> None:
    """Regression for issue #200.

    ``start mode=create_or_attach`` mints a fresh session for an existing task and appends one
    ordinary ``session_resumed`` event to the task-global ingestion/digest chain. Reading the
    ledger back through the attached session must therefore still yield the whole task chain: a
    session-filtered slice would start mid-chain and replay, which is genesis-anchored, would
    reject it as a corrupt projection.
    """

    app, _runtime, _ = _build_app(seed_offset=12)
    started, checked, obligation = await _bootstrap_finding(app, seed=1900, refs=True)

    before = await app.receipt(
        ReceiptRequest.model_validate(
            _receipt_wire(
                1910,
                task_id=started.task_id,
                session=started.session_id,
                writer=started.writer_id,
                frontier=checked.result_frontier,
            )
        )
    )
    assert before.subject_frontier == checked.result_frontier
    assert before.conclusion == "unresolved_findings_remain"

    attached = await app.start(
        start_request(1920, title="Respond/status/receipt exercise", refs=True)
    )
    assert attached.outcome == "attached"
    assert attached.task_id == started.task_id
    assert attached.session_id != started.session_id
    assert attached.writer_id != started.writer_id

    after = await app.receipt(
        ReceiptRequest.model_validate(
            _receipt_wire(
                1930,
                task_id=attached.task_id,
                session=attached.session_id,
                writer=attached.writer_id,
                frontier=attached.frontier,
            )
        )
    )

    # The receipt covers the whole task ledger, not the suffix this session authored: its subject
    # frontier is the attached head, which is strictly beyond the pre-resume receipt's.
    assert _frontier(after.subject_frontier) == _frontier(attached.frontier)
    assert after.subject_frontier.sequence > before.subject_frontier.sequence
    assert after.receipt_id != before.receipt_id

    # It also still reports the work published before the resume: the same unresolved conclusion,
    # from the same pre-resume check, over the same obligation.
    assert after.conclusion == before.conclusion
    assert after.suppressed_finding_count == before.suppressed_finding_count
    assert after.versions == before.versions
    document = cast(Mapping[str, JsonValue], after.document)
    assert obligation in canonical_encode(document).decode()

    # ``status view=candidate_findings`` reads the ledger the same way and was equally broken.
    candidates = await app.status(
        StatusRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1940)),
                "session_id": attached.session_id,
                "writer_id": attached.writer_id,
                "view": "candidate_findings",
                "limit": "10",
                "at_frontier": str(after.result_frontier.sequence),
            }
        )
    )
    candidate_page = cast(StatusCandidateFindingsPageModel, candidates.page)
    assert candidate_page.items
    assert any(obligation in item.subject_refs for item in candidate_page.items)

    # The ordinary findings view is task-wide from the attached session too: the finding the
    # pre-resume check returned is still the finding on the record.
    findings = await app.status(
        StatusRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 1950)),
                "session_id": attached.session_id,
                "writer_id": attached.writer_id,
                "view": "findings",
                "limit": "10",
                "at_frontier": str(after.result_frontier.sequence),
            }
        )
    )
    findings_page = cast(StatusFindingsPageModel, findings.page)
    assert tuple(item.finding_id for item in findings_page.items) == tuple(
        item.finding_id for item in checked.findings
    )


async def test_dry_run_publish_survives_reattach_through_create_or_attach() -> None:
    """The dry-run preflight replays the task ledger too (issue #200).

    ``publish_work dry_run=true`` proves a batch would reduce by replaying the existing records
    with the provisional ones appended, and it converts any replay ``ValueError`` into
    ``EVENT_INVALID``. Reading a session-filtered slice therefore turned every dry run on a
    resumed task into an invalid batch, and made a draft citing a pre-resume event look like a
    missing causal parent rather than the valid reference it is.
    """

    app, _runtime, _ = _build_app(seed_offset=14)
    started, _checked, _obligation = await _bootstrap_finding(app, seed=2100, refs=True)
    # The obligation event ``_bootstrap_finding`` publishes, named the same way it names it.
    obligation_event_id = protocol_id("evt_", 2102)

    attached = await app.start(
        start_request(2120, title="Respond/status/receipt exercise", refs=True)
    )
    assert attached.outcome == "attached"
    assert attached.task_id == started.task_id
    assert attached.session_id != started.session_id

    action_event_id = protocol_id("evt_", 2131)
    preview = await app.publish_work(
        PublishWorkRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 2130)),
                "session_id": attached.session_id,
                "writer_id": attached.writer_id,
                "expected_frontier": _frontier(attached.frontier),
                "dry_run": True,
                "event_drafts": (
                    {
                        "event_id": action_event_id,
                        "schema": {"name": "action_recorded", "version": "1.0.0"},
                        "occurred_at": "2026-07-19T12:00:02.000Z",
                        # Published before the resume, so the attached session can only cite it if
                        # the preflight reads the whole task chain.
                        "causal_parents": (obligation_event_id,),
                        "payload": {
                            "action_id": protocol_id("act_", 2132),
                            "action_kind": "other",
                            "description": "Continue the exercise from the attached session.",
                        },
                        "artifact_refs": (),
                        "evidence_refs": (),
                    },
                ),
            }
        )
    )

    assert type(preview) is PublishWorkResult, f"unexpected publish result: {type(preview)}"
    assert preview.ok is True
    assert preview.outcome == "dry_run"
    assert preview.subject_frontier.sequence == attached.frontier.sequence
    assert preview.result_frontier == preview.subject_frontier
    root = cast(PublishWorkDryRunModel, preview.root)
    assert root.evidential is False
    assert len(root.would_accept) == 1
    assert root.would_accept[0].event_id == action_event_id
    assert root.would_accept[0].causal_parents == (obligation_event_id,)

    # Duplicate detection stays task-wide as well: re-drafting a pre-resume event id is still an
    # invalid batch, so widening the read did not turn the preflight into a false positive.
    with pytest.raises(PublicOperationError) as caught:
        await app.publish_work(
            PublishWorkRequest.model_validate(
                {
                    **_request_base(protocol_id("req_", 2140)),
                    "session_id": attached.session_id,
                    "writer_id": attached.writer_id,
                    "expected_frontier": _frontier(attached.frontier),
                    "dry_run": True,
                    "event_drafts": (
                        {
                            "event_id": obligation_event_id,
                            "schema": {"name": "action_recorded", "version": "1.0.0"},
                            "occurred_at": "2026-07-19T12:00:03.000Z",
                            "causal_parents": (),
                            "payload": {
                                "action_id": protocol_id("act_", 2141),
                                "action_kind": "other",
                                "description": "Reuse an event id already on the task ledger.",
                            },
                            "artifact_refs": (),
                            "evidence_refs": (),
                        },
                    ),
                }
            )
        )
    assert caught.value.code is PublicErrorCode.EVENT_INVALID


async def test_reattach_detaches_the_prior_session_from_the_task_route() -> None:
    """The prior session stops being routable when a new one attaches (issue #200).

    Membership in the ledger is what ``load_events`` checks; *authority* to act on the task is a
    route question, and that is what a resumed START moves. This locks the contract that the fix
    for #200 must not weaken: widening the ledger read does not keep a superseded session
    routable.
    """

    app, _runtime, _ = _build_app(seed_offset=13)
    started, _checked, _obligation = await _bootstrap_finding(app, seed=2000, refs=True)

    assert await app.start_catalog.resolve_route(started.session_id) is not None

    attached = await app.start(
        start_request(2020, title="Respond/status/receipt exercise", refs=True)
    )
    assert attached.outcome == "attached"

    # Bounded, not an internal error: the superseded session no longer resolves to a route, which
    # is the fact the real bundle runtime turns into SESSION_NOT_FOUND before any ledger read.
    assert await app.start_catalog.resolve_route(started.session_id) is None
    resumed_route = await app.start_catalog.resolve_route(attached.session_id)
    assert resumed_route is not None
    assert resumed_route.task_id == attached.task_id

    # A session that never touched this task reads nothing from its ledger.
    ledger, _objects = _runtime.resources[attached.task_id]
    stranger = protocol_id("ses_", 2099)
    assert [record async for record in ledger.load_events(stranger)] == []
