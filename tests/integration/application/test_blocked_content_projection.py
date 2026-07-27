"""Blocked content must project to omission markers, never to a failed operation.

The 2026-07-27 Codex dogfood ran with local disclosure unauthorized, so every content leaf was
blocked. A four-event publication and a compact ``status`` read both returned
``response_projection_failed`` while the ledger had already advanced. These cases pin the
end-to-end projection of blocked content for the exact event families and views from that run.
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
from yoetz.application.publish_work import PublishWorkInternalResult
from yoetz.application.service import (
    Application,
    ClientProjectionContext,
    ControlProjectionBinding,
    ProjectionRenderMode,
    UnprojectedControlBody,
    VerificationPolicy,
)
from yoetz.domain.events import RuntimeProfile
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    ConsentSource,
    LocalDisclosureBlocked,
    LocalDisclosureOmission,
    LocalDisclosureReceipt,
    PrivacyOutcome,
    ReceiptCounts,
    ReceiptPolicyBinding,
    ReceiptSecretScan,
    ReceiptTransformations,
)
from yoetz.domain.receipts import (
    PolicyVersionEntry,
    ReceiptVersionSlice,
    SchemaVersionEntry,
)
from yoetz.domain.values import Frontier
from yoetz.ports.control import ControlClientKind, ControlMethod
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.importer import ImporterPort, ImportStatusSnapshot
from yoetz.ports.publish_response_catalog import PublishResponseCatalogPort
from yoetz.ports.runtime import BundleRuntimePort, RouteCommand, TaskRuntime
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.models import (
    FrontierModel,
    PublishWorkRequest,
    StatusRequest,
    public_model_to_wire,
)

pytestmark = pytest.mark.anyio

_DIGEST = "sha256:" + "7" * 64
_WORKSPACE = "hmac-sha256:" + "8" * 64


class _IdleImporter:
    async def status(self, session: str) -> ImportStatusSnapshot:
        from yoetz.domain.values import session_id

        return ImportStatusSnapshot(session_id(session), 0, 0, (), ())


_CAPABILITIES = frozenset(
    {
        RuntimeCapability.WRITE,
        RuntimeCapability.STRUCTURAL_READ,
        RuntimeCapability.PAYLOAD_READ,
    }
)


class _BlockedRuntime(MemoryStartRuntime):
    """Extend the START memory composition with ready writer routing."""

    async def route(self, command: RouteCommand) -> TaskRuntime:
        assert command.writer_id is not None
        assert command.required_capabilities <= _CAPABILITIES
        task_id, resources = next(iter(self.resources.items()))
        ledger, objects = resources
        assert (command.session_id, command.writer_id) in self.owners
        return TaskRuntime(
            task_id,
            command.session_id,
            command.writer_id,
            _CAPABILITIES,
            ledger,
            objects,
            cast(ImporterPort, _IdleImporter()),
            "0.1.0",
            "0.1.0",
            "0.1",
            "1.0.0",
            ownership_fence(),
        )


class _BlockEverything:
    """Refuse every content leaf, exactly as an unauthorized local disclosure does."""

    def __init__(self) -> None:
        self.blocked_categories: set[str] = set()

    async def prepare_local_disclosure(self, candidate: CandidateContext) -> LocalDisclosureBlocked:
        sink = candidate.local_sink
        assert sink is not None
        proposal_id = protocol_id("ppr_", 801)
        policy = ReceiptPolicyBinding(protocol_id("pvy_", 802), 1, _DIGEST, _DIGEST)
        omissions = tuple(
            sorted(
                (
                    LocalDisclosureOmission(
                        item.origin_ref,
                        item.category,
                        "local_disclosure_not_authorized",
                    )
                    for item in candidate.items
                ),
                key=lambda item: item.json_pointer.encode(),
            )
        )
        blocked = tuple(
            sorted({item.category for item in omissions}, key=lambda value: value.value)
        )
        self.blocked_categories.update(item.value for item in blocked)
        receipt = LocalDisclosureReceipt(
            "1.0.0",
            protocol_id("egr_", 803),
            candidate.request_id,
            proposal_id,
            sink,
            PrivacyOutcome.COMPLETED,
            datetime(2026, 7, 27, 12, 0, tzinfo=UTC),
            candidate.scope,
            candidate.purpose,
            policy,
            ConsentSource.BASELINE_POLICY,
            (),
            blocked,
            ReceiptCounts(0, 0, 0, 0, 0, 0, 0),
            ReceiptTransformations(0, 0, 0),
            ReceiptSecretScan("1.0.0", _DIGEST, 0, True),
            None,
            1,
        )
        return LocalDisclosureBlocked(
            proposal_id,
            candidate.request_id,
            sink,
            candidate.purpose,
            candidate.scope,
            _DIGEST,
            _WORKSPACE,
            omissions,
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


def _scope(_: ControlProjectionBinding, source: Mapping[str, JsonValue]) -> AuthorizationScope:
    return AuthorizationScope(
        AuthorizationScopeKind.TASK,
        protocol_id("ins_", 804),
        _WORKSPACE,
        cast(str, source["task_id"]),
    )


async def _semantic_disabled(frozen: object, findings: object) -> object:
    del frozen, findings
    raise AssertionError("semantic_evaluator_called_in_deterministic_mode")


def _request_base(request_id: str) -> dict[str, JsonValue]:
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": request_id,
        "actor": {"actor_id": "harness:test", "actor_type": "harness"},
        "client": {"kind": "test_client", "version": "0.1.0", "integration": "local_cli"},
    }


def _frontier(value: Frontier | FrontierModel) -> JsonValue:
    if isinstance(value, Frontier):
        return cast(JsonValue, dict(value.as_wire().items()))
    return cast(JsonValue, value.model_dump(mode="json"))


def _application() -> tuple[Application, _BlockEverything]:
    start_app, start_runtime, clock, catalog = start_composition()
    privacy = _BlockEverything()
    ids = start_runtime.ids
    runtime = _BlockedRuntime(clock, ids)
    app = Application(
        start_catalog=catalog.delegate,
        publish_responses=cast(PublishResponseCatalogPort, catalog.delegate),
        runtime=cast(BundleRuntimePort, runtime),
        clock=clock,
        ids=ids,
        verification_policy=VerificationPolicy(semantic="disabled", max_findings=3),
        privacy=cast(PrivacyCoordinator, privacy),
        status_cursor_key=b"blocked-projection-cursor-key",
        waiver_policy_digest=_DIGEST,
        semantic_evaluator=_semantic_disabled,
        disclosure_scope_for=_scope,
        receipt_version_resolver=lambda _: _versions(),
        waiver_authorizer=lambda _: False,
        import_publication_authorizer=lambda _: False,
        profile=RuntimeProfile.TEST_FAKE,
        policy_packs=("research-evidence/0.1.0", "work-integrity/0.1.0"),
        version_manifest=start_app.version_manifest,
    )
    return app, privacy


async def _project(
    app: Application,
    method: ControlMethod,
    request_body: Mapping[str, JsonValue],
    internal: UnprojectedControlBody,
    seed: int,
) -> Mapping[str, JsonValue]:
    facts = await app.projection_binding_facts(method, request_body, internal)
    rpc_id = protocol_id("rpc_", seed)
    service_instance_id = protocol_id("svc_", seed + 1)
    binding = ControlProjectionBinding(
        rpc_id,
        method,
        service_instance_id,
        1,
        facts.original_request_id,
        facts.route_identity_digest,
        canonical_encode(
            {
                "rpc_id": rpc_id,
                "method": method.value,
                "service_instance_id": service_instance_id,
                "service_generation": "1",
            }
        ),
    )
    context = ClientProjectionContext(
        ControlClientKind.MCP_BRIDGE, ProjectionRenderMode.MACHINE_READABLE, False
    )
    projected = await app.project_result_for_client(context, binding, internal)
    return public_model_to_wire(projected)


def _omission_categories(projected: Mapping[str, JsonValue]) -> set[str]:
    projection = cast(Mapping[str, JsonValue], projected["privacy_projection"])
    return set(cast(list[str], projection["blocked_categories"]))


async def test_blocked_content_projects_for_every_event_family_and_the_compact_view() -> None:
    app, privacy = _application()
    started = await app.start(start_request(910, title="Blocked projection"))
    obligation_id = protocol_id("obl_", 911)

    plan_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 913)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(started.frontier),
        "event_drafts": (
            {
                "event_id": protocol_id("evt_", 914),
                "schema": {"name": "plan_published", "version": "1.0.0"},
                "occurred_at": "2026-07-27T12:00:00.000Z",
                "causal_parents": (),
                "payload": {
                    "plan_version": 1,
                    "summary": "Narrow the remaining shortcut gap.",
                    "obligation_refs": (obligation_id,),
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
            {
                "event_id": protocol_id("evt_", 915),
                "schema": {"name": "obligation_published", "version": "1.0.0"},
                "occurred_at": "2026-07-27T12:00:01.000Z",
                "causal_parents": (),
                "payload": {
                    "obligation_id": obligation_id,
                    "description": "Open the required issue before editing product code.",
                    "acceptance_criteria": "A linked issue exists.",
                    "evidence_expectation": "The issue number.",
                    "requested_items": ({"item_kind": "change", "value": "issue"},),
                    "status": "open",
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
        ),
    }
    plan = await app.publish_work(PublishWorkRequest.model_validate(plan_wire))
    assert type(plan) is PublishWorkInternalResult
    projected_plan = await _project(app, ControlMethod.PUBLISH_WORK, plan_wire, plan, 920)

    # Blocked summaries are replaced by omission markers rather than leaking or failing.
    events = cast(list[Mapping[str, JsonValue]], projected_plan["accepted_events"])
    assert len(events) == 2
    for event in events:
        assert cast(Mapping[str, JsonValue], event["summary"])["omitted"] is True
    assert _omission_categories(projected_plan) == {"task_description"}

    action_id = protocol_id("act_", 930)
    work_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 931)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(plan.result_frontier),
        "event_drafts": (
            {
                "event_id": protocol_id("evt_", 932),
                "schema": {"name": "action_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-27T12:00:02.000Z",
                "causal_parents": (),
                "payload": {
                    "action_id": action_id,
                    "action_kind": "review",
                    "description": "Inspected provider setup, binding, and dispatch.",
                    "obligation_refs": (obligation_id,),
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
            {
                "event_id": protocol_id("evt_", 933),
                "schema": {"name": "result_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-27T12:00:03.000Z",
                "causal_parents": (),
                "payload": {
                    "result_id": protocol_id("res_", 934),
                    "action_id": action_id,
                    "outcome": "partial",
                    "summary": "Structural support already exists.",
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
            {
                "event_id": protocol_id("evt_", 935),
                "schema": {"name": "evidence_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-27T12:00:04.000Z",
                "causal_parents": (),
                "payload": {
                    "evidence_id": protocol_id("evd_", 936),
                    "evidence_kind": "test_result",
                    "strength": "metadata_only",
                    "observed_at": "2026-07-27T12:00:04.000Z",
                    "description": "Read-only source inspection.",
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
            {
                "event_id": protocol_id("evt_", 937),
                "schema": {"name": "claim_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-27T12:00:05.000Z",
                "causal_parents": (),
                "payload": {
                    "claim_id": protocol_id("clm_", 938),
                    "claim_kind": "completion",
                    "statement": "The gap is narrowed.",
                    "supporting_refs": (obligation_id,),
                    "obligation_refs": (obligation_id,),
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
        ),
    }
    work = await app.publish_work(PublishWorkRequest.model_validate(work_wire))
    assert type(work) is PublishWorkInternalResult
    projected_work = await _project(app, ControlMethod.PUBLISH_WORK, work_wire, work, 940)

    # The exact four families of the run's second publication, spanning three data categories
    # the first publication never exercised.
    assert len(cast(list[JsonValue], projected_work["accepted_events"])) == 4
    assert _omission_categories(projected_work) == {
        "command_metadata",
        "evidence_excerpt",
        "finding_summary",
    }

    status_wire: dict[str, JsonValue] = {
        **_request_base(protocol_id("req_", 950)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "view": "compact",
        "limit": "10",
    }
    status = await app.status(StatusRequest.model_validate(status_wire))
    projected_status = await _project(app, ControlMethod.STATUS, status_wire, status, 960)

    assert projected_status["view"] == "compact"
    page = cast(Mapping[str, JsonValue], projected_status["page"])
    item = cast(list[Mapping[str, JsonValue]], page["items"])[0]
    assert cast(Mapping[str, JsonValue], item["task_title"])["omitted"] is True
    assert "obligation_text" in _omission_categories(projected_status)

    # Together the three projections cover every category the run touched.
    assert {
        "task_description",
        "obligation_text",
        "command_metadata",
        "evidence_excerpt",
        "finding_summary",
    } <= privacy.blocked_categories
