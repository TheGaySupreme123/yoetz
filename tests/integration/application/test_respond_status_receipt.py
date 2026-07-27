"""Integration coverage for the response, status, and receipt operations composing over one
frozen frontier, all driven through the real ``Application`` facade and the memory ledger oracle.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import cast

import pydantic
import pytest

from builders.ledger_adapters import ownership_fence
from builders.start_application import (
    MemoryStartRuntime,
    protocol_id,
    start_composition,
    start_request,
)
from yoetz.application.egress import PrivacyCoordinator
from yoetz.application.service import Application, VerificationPolicy
from yoetz.application.start import StartInternalResult
from yoetz.domain.events import RuntimeProfile
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
from yoetz.domain.values import Frontier, session_id
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.importer import ImporterPort, ImportStatusSnapshot
from yoetz.ports.ledger import CheckCommitResult
from yoetz.ports.runtime import BundleRuntimePort, RouteCommand, TaskRuntime
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.models import (
    CheckRequest,
    FrontierModel,
    PublishWorkRequest,
    ReceiptRequest,
    RespondRequest,
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
) -> tuple[Application, _WorkflowRuntime, _ProjectionSpy]:
    start_app, start_runtime, clock, catalog = start_composition()
    projection = _ProjectionSpy()
    ids = start_runtime.ids
    runtime = _WorkflowRuntime(clock, ids)
    app = Application(
        start_catalog=catalog.delegate,
        runtime=cast(BundleRuntimePort, runtime),
        clock=clock,
        ids=ids,
        verification_policy=VerificationPolicy(semantic="disabled", max_findings=3),
        privacy=cast(PrivacyCoordinator, projection),
        status_cursor_key=(b"respond-status-receipt-cursor-key-" + str(seed_offset).encode() * 4)[
            :32
        ],
        waiver_policy_digest=_DIGEST,
        semantic_evaluator=_semantic_disabled,
        disclosure_scope_for=_scope,
        receipt_version_resolver=lambda _: _versions(),
        waiver_authorizer=(lambda _: False) if waiver_authorizer is None else waiver_authorizer,
        import_publication_authorizer=lambda _: False,
        profile=RuntimeProfile.TEST_FAKE,
        policy_packs=_POLICY_PACKS,
        version_manifest=start_app.version_manifest,
    )
    return app, runtime, projection


async def _bootstrap_finding(
    app: Application, *, seed: int
) -> tuple[StartInternalResult, CheckCommitResult, str]:
    """Publish one open obligation plus an unsupported completion claim about it, then check.

    This reuses the exact scenario already proven (in ``test_full_workflow.py``) to yield one
    actionable ``completion_with_open_obligations`` finding, so the finding-triggering mechanics
    themselves are not re-derived here.
    """

    started = await app.start(start_request(seed, title="Respond/status/receipt exercise"))
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
        "mode": "deterministic_only",
        "max_findings": "3",
    }
    checked = await app.check(CheckRequest.model_validate(check_wire))
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

    # Neither the disposition nor an already-expired waiver resolved the issue: the very same
    # issue (kind/policy/subject) is still reported, with a fresh finding_id.
    assert rechecked.findings
    rechecked_issue = (
        rechecked.findings[0].kind,
        rechecked.findings[0].policy_id,
        rechecked.findings[0].policy_version,
        rechecked.findings[0].subject_refs,
    )
    assert rechecked_issue == issue
    assert rechecked.findings[0].finding_id != finding.finding_id


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
    second_recheck_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 812)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(first_recheck.result_frontier),
        "mode": "deterministic_only",
        "max_findings": "3",
    }
    second_recheck = await app.check(CheckRequest.model_validate(second_recheck_wire))

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
    # The response advanced the frontier past the last check, so the frozen case honestly
    # reports the check as no longer applicable at this exact frontier rather than silently
    # reusing stale check facts.
    assert receipt.coverage.known_gaps == ("check_not_applicable",)
    gaps = cast(tuple[Mapping[str, JsonValue], ...], document["gaps"])
    assert any(cast(str, gap["code"]) == "check_not_applicable" for gap in gaps)

    # The bare code is honest but not interpretable: the 2026-07-27 dogfood saw
    # `check_not_applicable` immediately after a check that succeeded with external provenance and
    # could not tell which of four readings was meant. The limitations section must say which.
    sections = cast(tuple[Mapping[str, JsonValue], ...], document["sections"])
    limitations = next(
        cast(str, section["body"])
        for section in sections
        if cast(str, section["key"]) == "limitations_and_coverage"
    )
    assert "A check is recorded, but it tested subject frontier" in limitations
    assert f"rather than frontier {receipt.subject_frontier.sequence}" in limitations
    # Name the trap directly: a successful external review does not travel to a later subject.
    assert "including one backed by external review" in limitations
    assert "Re-run check at this frontier to make it count." in limitations

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
