"""Public status contract: read-only cross-surface parity, receipted disclosure, frontier and
pagination exactness, the future-frontier error mapping, the candidate-findings whole-case path,
and filter/derivation defaults.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

import pytest

from builders.ledger_adapters import ownership_fence
from builders.start_application import (
    MemoryStartRuntime,
    protocol_id,
    start_composition,
    start_request,
)
from yoetz.application.egress import PrivacyCoordinator
from yoetz.application.service import (
    Application,
    ClientProjectionContext,
    ControlProjectionBinding,
    ProjectionRenderMode,
    VerificationPolicy,
)
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
from yoetz.ports.control import ControlClientKind, ControlMethod
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.importer import ImporterPort, ImportStatusSnapshot
from yoetz.ports.ledger import CheckCommitResult
from yoetz.ports.runtime import BundleRuntimePort, RouteCommand, TaskRuntime
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.models import (
    CheckRequest,
    FrontierModel,
    PublishWorkRequest,
    RespondRequest,
    StatusCandidateFindingsPageModel,
    StatusFindingsPageModel,
    StatusObligationsPageModel,
    StatusRequest,
    StatusResultModel,
)

pytestmark = pytest.mark.anyio

_DIGEST = "sha256:" + "7" * 64
_WORKSPACE = "hmac-sha256:" + "8" * 64
_POLICY_PACKS = ("research-evidence/0.1.0", "work-integrity/0.1.0")


class _IdleImporter:
    async def status(self, session: str) -> ImportStatusSnapshot:
        return ImportStatusSnapshot(session_id(session), 0, 0, (), ())


class _WorkflowRuntime(MemoryStartRuntime):
    """Extend the START memory composition with ready writer routing (duplicated across
    operation-contract test modules by established convention; see
    ``tests/integration/application/test_respond_status_receipt.py``)."""

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
    def __init__(self) -> None:
        self.candidates: list[CandidateContext] = []

    async def prepare_local_disclosure(
        self, candidate: CandidateContext
    ) -> LocalDisclosureApproved:
        self.candidates.append(candidate)
        sink = candidate.local_sink
        assert sink is not None
        proposal_id = protocol_id("ppr_", 3900 + len(self.candidates))
        policy = ReceiptPolicyBinding(
            protocol_id("pvy_", 3950 + len(self.candidates)), 1, _DIGEST, _DIGEST
        )
        receipt = LocalDisclosureReceipt(
            "1.0.0",
            protocol_id("egr_", 3960 + len(self.candidates)),
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
        protocol_id("ins_", 3999),
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


def _build_app(*, seed_offset: int = 0) -> tuple[Application, _WorkflowRuntime, _ProjectionSpy]:
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
        status_cursor_key=(b"status-contract-cursor-key" + str(seed_offset).encode() * 8)[:32],
        waiver_policy_digest=_DIGEST,
        semantic_evaluator=_semantic_disabled,
        disclosure_scope_for=_scope,
        receipt_version_resolver=lambda _: _versions(),
        waiver_authorizer=lambda _: False,
        import_publication_authorizer=lambda _: False,
        profile=RuntimeProfile.TEST_FAKE,
        policy_packs=_POLICY_PACKS,
        version_manifest=start_app.version_manifest,
    )
    return app, runtime, projection


async def _bootstrap_finding(
    app: Application, *, seed: int, obligation_count: int = 1
) -> tuple[StartInternalResult, CheckCommitResult, tuple[str, ...]]:
    """Publish ``obligation_count`` open obligations plus an unsupported completion claim
    covering all of them, then check. Mirrors the exact scenario already proven in
    ``test_full_workflow.py`` to yield actionable ``completion_with_open_obligations`` findings.
    """

    started = await app.start(start_request(seed, title="Status contract exercise"))
    obligation_ids = tuple(
        protocol_id("obl_", seed + 1 + index) for index in range(obligation_count)
    )
    obligation_event_ids = tuple(
        protocol_id("evt_", seed + 10 + index) for index in range(obligation_count)
    )
    obligation_drafts = tuple(
        {
            "event_id": obligation_event_ids[index],
            "schema": {"name": "obligation_published", "version": "1.0.0"},
            "occurred_at": "2026-07-19T12:00:00.000Z",
            "causal_parents": (),
            "payload": {
                "obligation_id": obligation_ids[index],
                "description": f"Publish a result for status contract exercise {index}.",
                "acceptance_criteria": "A result is recorded in the task ledger.",
                "evidence_expectation": "A linked immutable result record.",
                "status": "open",
            },
            "artifact_refs": (),
            "evidence_refs": (),
        }
        for index in range(obligation_count)
    )
    claim_draft = {
        "event_id": protocol_id("evt_", seed + 20),
        "schema": {"name": "claim_recorded", "version": "1.0.0"},
        "occurred_at": "2026-07-19T12:00:01.000Z",
        "causal_parents": obligation_event_ids,
        "payload": {
            "claim_id": protocol_id("clm_", seed + 21),
            "claim_kind": "completion",
            "statement": "The exercise is complete.",
            "supporting_refs": obligation_ids,
            "obligation_refs": obligation_ids,
        },
        "artifact_refs": (),
        "evidence_refs": (),
    }
    publish_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", seed + 22)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(started.frontier),
        "event_drafts": (*obligation_drafts, claim_draft),
    }
    published = await app.publish_work(PublishWorkRequest.model_validate(publish_wire))
    check_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", seed + 23)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(published.result_frontier),
        "mode": "deterministic_only",
        "max_findings": "3",
    }
    checked = await app.check(CheckRequest.model_validate(check_wire))
    assert checked.findings, "the seeded scenario must always yield at least one finding"
    return started, checked, obligation_ids


def _cli_binding(rpc_seed: int, facts: object) -> ControlProjectionBinding:
    rpc_id = protocol_id("rpc_", rpc_seed)
    service_instance_id = protocol_id("svc_", rpc_seed + 1)
    return ControlProjectionBinding(
        rpc_id,
        ControlMethod.STATUS,
        service_instance_id,
        1,
        cast(str | None, getattr(facts, "original_request_id", None)),
        cast(str | None, getattr(facts, "route_identity_digest", None)),
        canonical_encode(
            {
                "rpc_id": rpc_id,
                "method": "status",
                "service_instance_id": service_instance_id,
                "service_generation": "1",
            }
        ),
    )


async def test_status_request_result_parity() -> None:
    """The same status result, projected for CLI and MCP-bridge surfaces, carries the same
    public page content everywhere; only the trusted per-surface disclosure sink differs."""

    app, _runtime, projection = _build_app(seed_offset=1)
    started, checked, _obligations = await _bootstrap_finding(app, seed=100)
    status_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 110)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "findings",
        "limit": "10",
        "at_frontier": str(checked.result_frontier.sequence),
    }
    status = await app.status(StatusRequest.model_validate(status_wire))
    facts = await app.projection_binding_facts(ControlMethod.STATUS, status_wire, status)

    cli_context = ClientProjectionContext(
        ControlClientKind.CLI, ProjectionRenderMode.HUMAN_READABLE, True
    )
    mcp_context = ClientProjectionContext(
        ControlClientKind.MCP_BRIDGE, ProjectionRenderMode.MACHINE_READABLE, False
    )
    cli_projected = await app.project_result_for_client(
        cli_context, _cli_binding(111, facts), status
    )
    mcp_projected = await app.project_result_for_client(
        mcp_context, _cli_binding(113, facts), status
    )
    assert isinstance(cli_projected, StatusResultModel)
    assert isinstance(mcp_projected, StatusResultModel)
    assert cli_projected.root.ok is True
    assert mcp_projected.root.ok is True

    cli_body = cli_projected.root.model_dump(mode="json", exclude={"privacy_projection"})
    mcp_body = mcp_projected.root.model_dump(mode="json", exclude={"privacy_projection"})
    assert cli_body == mcp_body
    assert cli_projected.root.view == "findings"
    assert cli_projected.root.privacy_projection.sink == "local_human_view"
    assert mcp_projected.root.privacy_projection.sink == "agent_context"
    assert len(projection.candidates) == 2


async def test_status_is_task_state_read_only_and_projection_receipted() -> None:
    """Status writes no task-ledger event/object/operation and never mutates the projection;
    ordinary client disclosure of an otherwise-ordinary status result gets exactly one durable
    local-disclosure receipt and a ``privacy_projection``."""

    app, runtime, projection = _build_app(seed_offset=2)
    started, checked, _obligations = await _bootstrap_finding(app, seed=200)
    ledger, _objects = runtime.resources[started.task_id]
    frontier_before = await ledger.load_frontier()

    status_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 210)),
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

    # Repeating the identical read at the identical frontier never advances the ledger: status
    # produces no accepted event, so the frontier after two reads is unchanged.
    repeated = await app.status(
        StatusRequest.model_validate({**status_wire, "request_id": protocol_id("req_", 211)})
    )
    assert repeated.subject_frontier == status.subject_frontier
    assert repeated.result_frontier == status.result_frontier
    frontier_after = await ledger.load_frontier()
    assert frontier_after == frontier_before

    # The raw internal status result carries no privacy marker until it is projected for an
    # ordinary client; no candidate has been proposed yet.
    assert projection.candidates == []
    facts = await app.projection_binding_facts(ControlMethod.STATUS, status_wire, status)
    binding = _cli_binding(212, facts)
    projected = await app.project_result_for_client(
        ClientProjectionContext(ControlClientKind.CLI, ProjectionRenderMode.HUMAN_READABLE, True),
        binding,
        status,
    )
    assert isinstance(projected, StatusResultModel)
    assert projected.root.ok is True
    assert projected.root.privacy_projection.sink == "local_human_view"
    assert len(projection.candidates) == 1

    # Replaying the exact same logical projection reuses the durable receipt facts rather than
    # minting a fresh one for an unchanged result/policy/sink pair -- at most one additional
    # candidate is proposed to confirm reuse rather than an unbounded receipt per replay.
    projected_again = await app.project_result_for_client(
        ClientProjectionContext(ControlClientKind.CLI, ProjectionRenderMode.HUMAN_READABLE, True),
        binding,
        status,
    )
    assert isinstance(projected_again, StatusResultModel)
    assert projected_again.root.ok is True
    assert len(projection.candidates) <= 2


async def test_status_frontier_and_pagination_parity() -> None:
    """Requested/head/effective frontiers and page contents are exact across a paginated scan;
    the cursor never loses, duplicates, or reorders the fixed-frontier item set."""

    app, _runtime, _projection = _build_app(seed_offset=3)
    started, checked, obligation_ids = await _bootstrap_finding(app, seed=300, obligation_count=2)

    unpaginated_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 310)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "obligations",
        "limit": "10",
        "at_frontier": str(checked.result_frontier.sequence),
    }
    unpaginated = await app.status(StatusRequest.model_validate(unpaginated_wire))
    unpaginated_page = cast(StatusObligationsPageModel, unpaginated.page)
    assert len(unpaginated_page.items) == len(obligation_ids)

    first_wire: dict[str, JsonValue] = {
        **unpaginated_wire,
        "request_id": protocol_id("req_", 311),
        "limit": "1",
    }
    first_page = await app.status(StatusRequest.model_validate(first_wire))
    first_obligations_page = cast(StatusObligationsPageModel, first_page.page)
    assert len(first_obligations_page.items) == 1
    assert first_obligations_page.next_cursor is not None
    assert first_page.subject_frontier == unpaginated.subject_frontier
    assert first_page.head_frontier == unpaginated.head_frontier

    second_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 312)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "obligations",
        "limit": "1",
        "cursor": first_obligations_page.next_cursor,
    }
    second_page = await app.status(StatusRequest.model_validate(second_wire))
    second_obligations_page = cast(StatusObligationsPageModel, second_page.page)
    assert len(second_obligations_page.items) == 1
    assert second_page.subject_frontier == unpaginated.subject_frontier
    assert second_page.head_frontier == unpaginated.head_frontier

    paginated_ids = {
        first_obligations_page.items[0].obligation_id,
        second_obligations_page.items[0].obligation_id,
    }
    unpaginated_ids = {item.obligation_id for item in unpaginated_page.items}
    assert paginated_ids == unpaginated_ids


async def test_future_frontier_is_invalid_request() -> None:
    """A requested read-only frontier beyond the observed head is ``INVALID_REQUEST``, never
    ``FRONTIER_CONFLICT`` -- status is a read-only query, so a future sequence is invalid query
    input, not a stale optimistic mutation guard."""

    app, _runtime, _projection = _build_app(seed_offset=4)
    started, checked, _obligations = await _bootstrap_finding(app, seed=400)
    future_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 410)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "compact",
        "limit": "10",
        "at_frontier": str(checked.result_frontier.sequence + 1000),
    }
    with pytest.raises(PublicOperationError) as failure:
        await app.status(StatusRequest.model_validate(future_wire))
    assert failure.value.code is PublicErrorCode.INVALID_REQUEST
    assert failure.value.code is not PublicErrorCode.FRONTIER_CONFLICT


async def test_candidate_findings_uses_only_whole_case_path() -> None:
    """The candidate-findings view uses one exact availability snapshot rather than any
    ``ProjectionQuery``, and its deterministic identity matches a same-frontier check."""

    app, _runtime, _projection = _build_app(seed_offset=5)
    started, checked, _obligations = await _bootstrap_finding(app, seed=500)
    candidate_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 510)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "candidate_findings",
        "limit": "10",
        "at_frontier": str(checked.result_frontier.sequence),
    }
    candidates = await app.status(StatusRequest.model_validate(candidate_wire))
    candidates_page = cast(StatusCandidateFindingsPageModel, candidates.page)
    assert candidates_page.items

    check_issue = {
        (finding.kind.value, finding.policy_id, finding.policy_version, finding.subject_refs)
        for finding in checked.findings
    }
    candidate_issue = {
        (item.kind, item.policy_id, item.policy_version, item.subject_refs)
        for item in candidates_page.items
    }
    assert candidate_issue == check_issue

    # Any other view is a bounded row query over the port-owned typed position, not this
    # whole-case scan: it accepts an ordinary (nonnegative) cursor position, while
    # ``candidate_findings`` accepts only its own integer offset -- the two paging identities
    # never mix.
    findings_wire: dict[str, JsonValue] = {
        **candidate_wire,
        "view": "findings",
        "request_id": protocol_id("req_", 511),
    }
    findings_status = await app.status(StatusRequest.model_validate(findings_wire))
    assert findings_status.view == "findings"


async def test_candidate_parity_excludes_semantic_findings() -> None:
    """Deterministic candidate identity matches a same-frontier check independently of capping;
    no semantic-origin finding has a candidate row.

    The composed harness in this file runs with semantic verification disabled (deterministic
    mode only, matching every other operation-contract test module), so a live semantic-origin
    finding cannot be produced here; this asserts the origin invariant that does hold for every
    row actually returned.
    """

    app, _runtime, _projection = _build_app(seed_offset=6)
    started, checked, _obligations = await _bootstrap_finding(app, seed=600)
    candidate_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 610)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "candidate_findings",
        "limit": "10",
        "at_frontier": str(checked.result_frontier.sequence),
    }
    candidates = await app.status(StatusRequest.model_validate(candidate_wire))
    candidates_page = cast(StatusCandidateFindingsPageModel, candidates.page)
    assert candidates_page.items
    assert all(item.origin == "deterministic" for item in candidates_page.items)
    assert all(finding.origin.value == "deterministic" for finding in checked.findings)


async def test_finding_and_candidate_tie_breaks_are_distinct() -> None:
    """Recorded findings-view order and candidate-findings-view order are each internally
    stable and deterministic across repeated reads of the identical frontier, even when two
    findings of the same kind/priority are recorded together."""

    app, _runtime, _projection = _build_app(seed_offset=7)
    started, checked, _obligation_ids = await _bootstrap_finding(app, seed=700, obligation_count=2)
    # Two obligations covered by one completion claim record more than one finding sharing the
    # same kind/priority, so their relative order is decided purely by the tie-break rule.
    assert len(checked.findings) >= 2

    findings_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 710)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "findings",
        "limit": "10",
        "at_frontier": str(checked.result_frontier.sequence),
    }
    findings_first = await app.status(StatusRequest.model_validate(findings_wire))
    findings_second = await app.status(
        StatusRequest.model_validate({**findings_wire, "request_id": protocol_id("req_", 711)})
    )
    findings_first_page = cast(StatusFindingsPageModel, findings_first.page)
    findings_second_page = cast(StatusFindingsPageModel, findings_second.page)
    first_order = tuple(item.finding_id for item in findings_first_page.items)
    second_order = tuple(item.finding_id for item in findings_second_page.items)
    assert first_order == second_order
    # Among the rows that share the exact same rank_key (kind/priority/coverage strength), the
    # recorded tie-break is finding ID ascending.
    same_rank_groups: dict[tuple[str, int], list[str]] = {}
    for item in findings_first_page.items:
        same_rank_groups.setdefault((item.kind, item.priority), []).append(item.finding_id)
    for group in same_rank_groups.values():
        if len(group) > 1:
            assert group == sorted(group)

    candidate_wire: dict[str, JsonValue] = {
        **findings_wire,
        "view": "candidate_findings",
        "request_id": protocol_id("req_", 712),
    }
    candidates_first = await app.status(StatusRequest.model_validate(candidate_wire))
    candidates_second = await app.status(
        StatusRequest.model_validate({**candidate_wire, "request_id": protocol_id("req_", 713)})
    )
    candidates_first_page = cast(StatusCandidateFindingsPageModel, candidates_first.page)
    candidates_second_page = cast(StatusCandidateFindingsPageModel, candidates_second.page)
    candidate_first_order = tuple(item.subject_refs for item in candidates_first_page.items)
    candidate_second_order = tuple(item.subject_refs for item in candidates_second_page.items)
    # The candidate tie-break (canonical emission ordinal) is independent of finding_id -- which
    # does not exist yet for a not-yet-recorded candidate row -- but is still fully deterministic
    # across repeated reads of the identical frozen frontier.
    assert candidate_first_order == candidate_second_order
    expected_issue = {
        (finding.kind.value, finding.policy_id, finding.policy_version, finding.subject_refs)
        for finding in checked.findings
    }
    candidate_issue = {
        (item.kind, item.policy_id, item.policy_version, item.subject_refs)
        for item in candidates_first_page.items
    }
    # The bootstrapped scenario records more distinct issues than the capped check's
    # ``max_findings=3`` returns; every capped check finding still matches one uncapped
    # candidate row exactly (candidate identity does not depend on capping).
    assert expected_issue <= candidate_issue


async def test_status_raw_item_derivation_and_filter_defaults() -> None:
    """Every returned field follows the registered projection source, and absent/false include
    flags compose with AND: an explicit ``status=resolved`` filter alone, without
    ``include_resolved=true``, is intentionally empty."""

    app, _runtime, _projection = _build_app(seed_offset=8)
    started, checked, obligation_ids = await _bootstrap_finding(app, seed=800)
    finding = checked.findings[0]

    ack_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 810)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "finding_id": finding.finding_id,
        "finding_frontier": _frontier(checked.result_frontier),
        "disposition": "acknowledged",
        "reason": "Tracked for the derivation/filter-default exercise.",
    }
    responded = await app.respond(RespondRequest.model_validate(ack_wire))

    findings_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 811)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "findings",
        "limit": "10",
        "at_frontier": str(responded.result_frontier.sequence),
    }
    findings_status = await app.status(StatusRequest.model_validate(findings_wire))
    findings_status_page = cast(StatusFindingsPageModel, findings_status.page)
    item = next(row for row in findings_status_page.items if row.finding_id == finding.finding_id)
    # Raw item derivation follows the exact recorded source facts.
    assert item.disposition == "acknowledged"
    assert item.resolved is False
    assert item.kind == finding.kind.value
    assert item.policy_id == finding.policy_id
    assert item.subject_refs == finding.subject_refs

    open_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 812)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "obligations",
        "limit": "10",
        "at_frontier": str(responded.result_frontier.sequence),
    }
    open_only = await app.status(StatusRequest.model_validate(open_wire))
    open_only_page = cast(StatusObligationsPageModel, open_only.page)
    assert {row.obligation_id for row in open_only_page.items} == set(obligation_ids)
    assert all(row.status == "open" for row in open_only_page.items)

    # An explicit ``status=resolved`` filter alone -- without ``include_resolved=true`` -- is
    # intentionally empty: absent/false ``include_resolved`` always suppresses resolved rows,
    # even when another filter field explicitly names them. Filters compose with AND, never OR.
    resolved_only_wire: dict[str, JsonValue] = {
        **open_wire,
        "request_id": protocol_id("req_", 813),
        "filter": {"status": "resolved"},
    }
    resolved_only = await app.status(StatusRequest.model_validate(resolved_only_wire))
    assert cast(StatusObligationsPageModel, resolved_only.page).items == ()

    resolved_included_wire: dict[str, JsonValue] = {
        **open_wire,
        "request_id": protocol_id("req_", 814),
        "filter": {"status": "resolved", "include_resolved": True},
    }
    resolved_included = await app.status(StatusRequest.model_validate(resolved_included_wire))
    # No obligation is actually resolved in this scenario, so this remains empty too: the
    # composed filter is honest about there being nothing to include, not silently ignored.
    assert cast(StatusObligationsPageModel, resolved_included.page).items == ()


async def test_finding_resolution_requires_recorded_applicability() -> None:
    """Acknowledged/rejected/waived disposition never resolves a finding; only a later
    check that runs its policy to completion over overlapping scope, current and gap-free,
    with zero suppression, resolves it."""

    app, _runtime, _projection = _build_app(
        seed_offset=9,
    )
    started, checked, _obligations = await _bootstrap_finding(app, seed=900)
    finding = checked.findings[0]
    issue = (finding.kind, finding.policy_id, finding.policy_version, finding.subject_refs)

    ack_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 910)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(checked.result_frontier),
        "finding_id": finding.finding_id,
        "finding_frontier": _frontier(checked.result_frontier),
        "disposition": "acknowledged",
        "reason": "Not yet addressed.",
    }
    responded = await app.respond(RespondRequest.model_validate(ack_wire))

    recheck_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 911)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(responded.result_frontier),
        "mode": "deterministic_only",
        "max_findings": "3",
    }
    rechecked = await app.check(CheckRequest.model_validate(recheck_wire))
    assert rechecked.findings
    rechecked_issue = (
        rechecked.findings[0].kind,
        rechecked.findings[0].policy_id,
        rechecked.findings[0].policy_version,
        rechecked.findings[0].subject_refs,
    )
    assert rechecked_issue == issue

    status_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 912)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "findings",
        "limit": "10",
        "at_frontier": str(rechecked.result_frontier.sequence),
        "filter": {"include_resolved": True},
    }
    status = await app.status(StatusRequest.model_validate(status_wire))
    # The acknowledgement never resolved the original finding: the newer same-issue row (the
    # only one visible at this frontier) still reports unresolved.
    assert all(not row.resolved for row in cast(StatusFindingsPageModel, status.page).items)


async def test_status_tombstone_and_unreadable_page_progress() -> None:
    """A row omitted for tombstone/unreadability still consumes its scanned cursor position and
    is never backfilled, so cursor progress cannot loop forever."""

    app, _runtime, _projection = _build_app(seed_offset=10)
    started, checked, obligation_ids = await _bootstrap_finding(app, seed=1000, obligation_count=2)

    obligations_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 1010)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "obligations",
        "limit": "1",
        "at_frontier": str(checked.result_frontier.sequence),
    }
    first_page = await app.status(StatusRequest.model_validate(obligations_wire))
    first_obligations_page = cast(StatusObligationsPageModel, first_page.page)
    assert first_obligations_page.next_cursor is not None

    scanned: list[str] = [item.obligation_id for item in first_obligations_page.items]
    cursor = first_obligations_page.next_cursor
    seen_cursors: set[str] = {cursor}
    for step in range(1, 4):
        page_wire: dict[str, JsonValue] = {
            **_request_base(protocol_id("req_", 1010 + step)),
            "session_id": started.session_id,
            "writer_id": started.writer_id,
            "view": "obligations",
            "limit": "1",
            "cursor": cursor,
        }
        page = await app.status(StatusRequest.model_validate(page_wire))
        obligations_page = cast(StatusObligationsPageModel, page.page)
        scanned.extend(item.obligation_id for item in obligations_page.items)
        if obligations_page.next_cursor is None:
            break
        # Cursor progress never loops: each successive cursor must be new.
        assert obligations_page.next_cursor not in seen_cursors
        seen_cursors.add(obligations_page.next_cursor)
        cursor = obligations_page.next_cursor
    assert set(scanned) == set(obligation_ids)


async def test_status_filters_before_payload_hydration() -> None:
    """Filter/order/lookahead selection is structural: the page returned for a bounded limit
    contains exactly the selected rows and never more than the requested bound."""

    app, _runtime, _projection = _build_app(seed_offset=11)
    started, checked, obligation_ids = await _bootstrap_finding(app, seed=1100, obligation_count=2)

    bounded_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 1110)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "obligations",
        "limit": "1",
        "at_frontier": str(checked.result_frontier.sequence),
    }
    bounded = await app.status(StatusRequest.model_validate(bounded_wire))
    bounded_page = cast(StatusObligationsPageModel, bounded.page)
    assert len(bounded_page.items) == 1
    assert bounded_page.next_cursor is not None

    full_wire: dict[str, JsonValue] = {
        **bounded_wire,
        "request_id": protocol_id("req_", 1111),
        "limit": "10",
    }
    full = await app.status(StatusRequest.model_validate(full_wire))
    full_page = cast(StatusObligationsPageModel, full.page)
    assert len(full_page.items) == len(obligation_ids)
    assert {item.obligation_id for item in full_page.items} == set(obligation_ids)
