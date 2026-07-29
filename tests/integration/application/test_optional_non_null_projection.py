"""Unset optional non-null leaves must project as absence, never as JSON null.

2026-07-28 run 4 dogfood: an obligation published without ``acceptance_criteria`` broke the
default ``status view=compact`` and ``status view=obligations``. The ledger already omitted the
unset key, but ``StatusInternalResult.as_json`` dumped the page with defaulted Nones reintroduced,
and ``_public_model`` re-validated that null into the closed wire models, which reject it
(``optional_field_must_not_be_null``). The same class previously hit accepted-event ``summary``
(PR #50) and respond reason/waiver fields.

These cases pin the obligation defect end to end, keep the three content states distinguishable
(text / omission marker / total absence), reject explicit nulls at the model boundary, and walk
every public *result* model that declares ``optional_non_null_fields`` so a new member of the class
is visibly missing from the inventory table rather than silently unswept.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError

import yoetz.protocol.models as protocol_models
from builders.projection_workflow import (
    ProjectionCase,
    build_projection_application,
    frontier_json,
    project_case,
    request_base,
)
from builders.start_application import protocol_id, start_request
from yoetz.application.publish_work import PublishWorkInternalResult
from yoetz.application.respond import RespondInternalResult
from yoetz.application.service import (
    Application,
    ClientProjectionContext,
    ControlProjectionBinding,
    ProjectionRenderMode,
)
from yoetz.domain.privacy import (
    CandidateContext,
    ConsentSource,
    LocalDisclosureApproved,
    LocalDisclosureBlocked,
    LocalDisclosureOmission,
    LocalDisclosureReceipt,
    PrivacyOutcome,
    ReceiptCounts,
    ReceiptPolicyBinding,
    ReceiptSecretScan,
    ReceiptTransformations,
)
from yoetz.ports.control import ControlClientKind, ControlMethod
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.models import (
    DataCategory,
    PublicErrorModel,
    PublishWorkAcceptedEventModel,
    PublishWorkRequest,
    RespondEvidenceSummaryModel,
    RespondResponseModel,
    StatusCompactObligationModel,
    StatusObligationItemModel,
    StatusRequest,
    StatusStructuralSubjectStateModel,
    public_model_to_wire,
)

pytestmark = pytest.mark.anyio

# Private base is not re-exported; resolve by getattr like other protocol tests.
_CLOSED_MODEL = cast(type[BaseModel], getattr(protocol_models, "_ClosedModel"))

_DIGEST = "sha256:" + "a" * 64
_WORKSPACE = "hmac-sha256:" + "8" * 64

# Every public *result* model that declares ``optional_non_null_fields``. Request and filter models
# are caller-supplied and already reject null at parse time; they are intentionally absent here.
# A new result model that joins the set without a row in this table fails the inventory test.
_RESULT_OPTIONAL_NON_NULL: tuple[tuple[type[BaseModel], frozenset[str]], ...] = (
    (PublishWorkAcceptedEventModel, frozenset({"summary"})),
    (PublicErrorModel, frozenset({"safe_details"})),
    (RespondEvidenceSummaryModel, frozenset({"description"})),
    (RespondResponseModel, frozenset({"reason", "waiver_scope", "waiver_expiry"})),
    (StatusCompactObligationModel, frozenset({"acceptance_criteria"})),
    (StatusObligationItemModel, frozenset({"acceptance_criteria"})),
    (StatusStructuralSubjectStateModel, frozenset({"tree_digest", "diff_digest"})),
)

# Models that appear only on the request/filter surface — not projected result bodies.
_REQUEST_SIDE_OPTIONAL_NON_NULL = frozenset(
    {
        "ActorAssertionModel",
        "SubjectStateRefModel",
        "StartRequestModel",
        "StartRequest",
        "PublishWorkRequestModel",
        "PublishWorkRequest",
        "CheckRequestModel",
        "CheckRequest",
        "RespondRequestModel",
        "RespondRequest",
        "StatusRequestModel",
        "StatusRequest",
        "StatusAssignmentFilterModel",
        "StatusCandidateFindingsFilterModel",
        "StatusEvidenceFilterModel",
        "StatusFindingsFilterModel",
        "StatusHistoryFilterModel",
        "StatusObligationsFilterModel",
    }
)


class _BlockEverything:
    """Refuse every content leaf, exactly as an unauthorized local disclosure does."""

    async def prepare_local_disclosure(
        self, candidate: CandidateContext
    ) -> LocalDisclosureApproved | LocalDisclosureBlocked:
        """Block actual content while approving candidates with no content leaves."""

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
        receipt = LocalDisclosureReceipt(
            "1.0.0",
            protocol_id("egr_", 803),
            candidate.request_id,
            proposal_id,
            sink,
            PrivacyOutcome.COMPLETED,
            datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
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
        if not omissions:
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
        """Close the stand-in privacy coordinator."""

        return None


def _obligation_draft(
    *,
    event_id: str,
    obligation_id: str,
    acceptance_criteria: str | None,
) -> dict[str, JsonValue]:
    """Build one open obligation_published draft, optionally with acceptance criteria."""

    payload: dict[str, JsonValue] = {
        "obligation_id": obligation_id,
        "description": "Publish without manufacturing a null acceptance_criteria leaf.",
        "evidence_expectation": "A linked immutable result record.",
        "status": "open",
    }
    if acceptance_criteria is not None:
        payload["acceptance_criteria"] = acceptance_criteria
    return {
        "event_id": event_id,
        "schema": {"name": "obligation_published", "version": "1.0.0"},
        "occurred_at": "2026-07-28T12:00:00.000Z",
        "causal_parents": [],
        "payload": payload,
        "artifact_refs": [],
        "evidence_refs": [],
    }


async def _publish_obligation(
    seed: int,
    *,
    acceptance_criteria: str | None,
    evidence_subject_state: Mapping[str, str] | None = None,
) -> tuple[Application, str, str]:
    """Start a task and publish one obligation (optionally plus evidence with subject state)."""

    app, _policy = await build_projection_application(seed=seed)
    started = await app.start(start_request(seed + 1, title="Optional non-null projection"))
    obligation_id = protocol_id("obl_", seed + 2)
    event_id = protocol_id("evt_", seed + 3)
    drafts: list[JsonValue] = [
        _obligation_draft(
            event_id=event_id,
            obligation_id=obligation_id,
            acceptance_criteria=acceptance_criteria,
        )
    ]
    if evidence_subject_state is not None:
        drafts.append(
            {
                "event_id": protocol_id("evt_", seed + 4),
                "schema": {"name": "evidence_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-28T12:00:01.000Z",
                "causal_parents": [],
                "payload": {
                    "evidence_id": protocol_id("evd_", seed + 5),
                    "evidence_kind": "test_result",
                    "strength": "metadata_only",
                    "observed_at": "2026-07-28T12:00:01.000Z",
                    "reference": "subject-state fixture",
                    "subject_state": dict(evidence_subject_state),
                },
                "artifact_refs": [],
                "evidence_refs": [],
            }
        )
    publish_body: dict[str, JsonValue] = {
        **request_base(protocol_id("req_", seed + 6)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": frontier_json(started.frontier),
        "event_drafts": drafts,
    }
    published = await app.publish_work(PublishWorkRequest.model_validate(publish_body))
    assert type(published) is PublishWorkInternalResult
    return app, started.session_id, started.writer_id


async def _project_status(
    app: Application,
    *,
    session_id: str,
    writer_id: str,
    view: str,
    seed: int,
) -> Mapping[str, JsonValue]:
    """Run status for *view* through the daemon projection boundary."""

    status_body: dict[str, JsonValue] = {
        **request_base(protocol_id("req_", seed)),
        "session_id": session_id,
        "writer_id": writer_id,
        "view": view,
        "limit": "10",
    }
    status = await app.status(StatusRequest.model_validate(status_body))
    return await project_case(
        app,
        ProjectionCase(f"status/{view}", ControlMethod.STATUS, status_body, status),
        seed + 10,
    )


def _obligation_from_projected(
    projected: Mapping[str, JsonValue], view: str
) -> Mapping[str, JsonValue]:
    """Pull the single obligation row out of a compact or obligations status body."""

    page = cast(Mapping[str, JsonValue], projected["page"])
    items = cast(list[Mapping[str, JsonValue]], page["items"])
    assert items, f"{view} projected empty"
    if view == "compact":
        open_obligations = cast(list[Mapping[str, JsonValue]], items[0]["open_obligations"])
        assert open_obligations, "compact open_obligations empty"
        return open_obligations[0]
    return items[0]


@pytest.mark.parametrize("view", ("compact", "obligations"))
async def test_obligation_without_acceptance_criteria_projects(view: str) -> None:
    """An obligation published without acceptance_criteria projects on both status views."""

    app, session_id, writer_id = await _publish_obligation(2100, acceptance_criteria=None)
    projected = await _project_status(
        app, session_id=session_id, writer_id=writer_id, view=view, seed=2110
    )
    assert projected["ok"] is True
    obligation = _obligation_from_projected(projected, view)
    assert "acceptance_criteria" not in obligation
    assert obligation["description"] == (
        "Publish without manufacturing a null acceptance_criteria leaf."
    )
    assert obligation["evidence_expectation"] == "A linked immutable result record."
    if view == "obligations":
        # Required nullable keys that were set to null must still project as null.
        assert obligation["revision_event_id"] is None


@pytest.mark.parametrize("view", ("compact", "obligations"))
async def test_obligation_with_acceptance_criteria_keeps_text(view: str) -> None:
    """When acceptance_criteria is set, the text survives projection intact."""

    text = "A linked issue exists and is referenced from the result."
    app, session_id, writer_id = await _publish_obligation(2200, acceptance_criteria=text)
    projected = await _project_status(
        app, session_id=session_id, writer_id=writer_id, view=view, seed=2210
    )
    assert projected["ok"] is True
    obligation = _obligation_from_projected(projected, view)
    assert obligation["acceptance_criteria"] == text


@pytest.mark.parametrize("view", ("compact", "obligations"))
async def test_obligation_acceptance_criteria_policy_omission_is_distinct(view: str) -> None:
    """Policy-omitted acceptance_criteria is an omission marker — not absence and not null."""

    text = "Criteria present so disclosure has a real leaf to omit."
    app, session_id, writer_id = await _publish_obligation(2300, acceptance_criteria=text)
    object.__setattr__(app, "privacy", _BlockEverything())

    status_body: dict[str, JsonValue] = {
        **request_base(protocol_id("req_", 2320)),
        "session_id": session_id,
        "writer_id": writer_id,
        "view": view,
        "limit": "10",
    }
    status = await app.status(StatusRequest.model_validate(status_body))
    facts = await app.projection_binding_facts(ControlMethod.STATUS, status_body, status)
    rpc_id = protocol_id("rpc_", 2330)
    service_instance_id = protocol_id("svc_", 2331)
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
                "method": ControlMethod.STATUS.value,
                "service_instance_id": service_instance_id,
                "service_generation": "1",
            }
        ),
    )
    projected = public_model_to_wire(
        await app.project_result_for_client(
            ClientProjectionContext(
                ControlClientKind.MCP_BRIDGE, ProjectionRenderMode.MACHINE_READABLE, False
            ),
            binding,
            status,
        )
    )
    assert projected["ok"] is True
    obligation = _obligation_from_projected(projected, view)
    assert "acceptance_criteria" in obligation
    criteria = obligation["acceptance_criteria"]
    assert isinstance(criteria, Mapping)
    assert criteria["omitted"] is True
    assert criteria["category"] == DataCategory.OBLIGATION_TEXT.value
    assert criteria is not None


async def test_publish_accepted_events_omit_unset_summary() -> None:
    """PublishWorkAcceptedEventModel.summary stays absent when never populated (PR #50)."""

    app, _policy = await build_projection_application(seed=2400)
    started = await app.start(start_request(2401, title="Summary absence"))
    body: dict[str, JsonValue] = {
        **request_base(protocol_id("req_", 2402)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": frontier_json(started.frontier),
        "event_drafts": [
            _obligation_draft(
                event_id=protocol_id("evt_", 2403),
                obligation_id=protocol_id("obl_", 2404),
                acceptance_criteria=None,
            )
        ],
    }
    internal = await app.publish_work(PublishWorkRequest.model_validate(body))
    assert type(internal) is PublishWorkInternalResult
    projected = await project_case(
        app,
        ProjectionCase("publish_work", ControlMethod.PUBLISH_WORK, body, internal),
        2410,
    )
    assert projected["ok"] is True
    events = cast(list[Mapping[str, JsonValue]], projected["accepted_events"])
    assert events
    for event in events:
        assert "summary" not in event


async def test_respond_omits_unset_optional_response_fields() -> None:
    """RespondResponseModel reason/waiver fields and evidence description stay absent when unset."""

    from yoetz.protocol.models import CheckRequest, RespondRequest

    # Dedicated path: acknowledge without reason/waiver, and reference evidence that has no
    # description, so every optional_non_null leaf on the respond result stays unset.
    app, _policy = await build_projection_application(seed=2520)
    started = await app.start(start_request(2521, title="Respond without optionals"))
    obligation_id = protocol_id("obl_", 2522)
    evidence_id = protocol_id("evd_", 2528)
    publish_body: dict[str, JsonValue] = {
        **request_base(protocol_id("req_", 2523)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": frontier_json(started.frontier),
        "event_drafts": [
            {
                "event_id": protocol_id("evt_", 2524),
                "schema": {"name": "obligation_published", "version": "1.0.0"},
                "occurred_at": "2026-07-28T12:00:00.000Z",
                "causal_parents": [],
                "payload": {
                    "obligation_id": obligation_id,
                    "description": "An open obligation that check will flag.",
                    "evidence_expectation": "A linked result.",
                    "requested_items": [{"item_kind": "change", "value": "unset-optional"}],
                    "status": "open",
                },
                "artifact_refs": [],
                "evidence_refs": [],
            },
            {
                "event_id": protocol_id("evt_", 2525),
                "schema": {"name": "claim_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-28T12:00:01.000Z",
                "causal_parents": [],
                "payload": {
                    "claim_id": protocol_id("clm_", 2526),
                    "claim_kind": "completion",
                    "statement": "Work is complete without meeting the obligation.",
                    "supporting_refs": [obligation_id],
                    "obligation_refs": [obligation_id],
                },
                "artifact_refs": [],
                "evidence_refs": [],
            },
            {
                "event_id": protocol_id("evt_", 2530),
                "schema": {"name": "evidence_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-28T12:00:02.000Z",
                "causal_parents": [],
                "payload": {
                    "evidence_id": evidence_id,
                    "evidence_kind": "test_result",
                    "strength": "metadata_only",
                    "observed_at": "2026-07-28T12:00:02.000Z",
                    "reference": "respond-without-optionals",
                },
                "artifact_refs": [],
                "evidence_refs": [],
            },
        ],
    }
    published = await app.publish_work(PublishWorkRequest.model_validate(publish_body))
    assert type(published) is PublishWorkInternalResult
    check_body: dict[str, JsonValue] = {
        **request_base(protocol_id("req_", 2527)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": frontier_json(published.result_frontier),
        "mode": "deterministic_only",
        "max_findings": "3",
    }
    checked = await app.check(CheckRequest.model_validate(check_body))
    assert checked.findings
    respond_body: dict[str, JsonValue] = {
        **request_base(protocol_id("req_", 2531)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": frontier_json(checked.result_frontier),
        "finding_id": checked.findings[0].finding_id,
        "finding_frontier": frontier_json(checked.result_frontier),
        "disposition": "acknowledged",
        "evidence_refs": [evidence_id],
    }
    responded = await app.respond(RespondRequest.model_validate(respond_body))
    assert type(responded) is RespondInternalResult
    projected = await project_case(
        app,
        ProjectionCase("respond", ControlMethod.RESPOND, respond_body, responded),
        2540,
    )
    assert projected["ok"] is True
    response = cast(Mapping[str, JsonValue], projected["response"])
    assert "reason" not in response
    assert "waiver_scope" not in response
    assert "waiver_expiry" not in response
    evidence = cast(list[Mapping[str, JsonValue]], response["evidence"])
    assert evidence
    for item in evidence:
        assert "description" not in item


@pytest.mark.parametrize(
    ("present", "absent"),
    (
        ("tree_digest", "diff_digest"),
        ("diff_digest", "tree_digest"),
    ),
)
async def test_status_evidence_omits_unset_structural_digest(present: str, absent: str) -> None:
    """StatusStructuralSubjectStateModel projects with only the set digest, never a null sibling."""

    seed = 2600 if present == "tree_digest" else 2650
    app, session_id, writer_id = await _publish_obligation(
        seed,
        acceptance_criteria=None,
        evidence_subject_state={present: _DIGEST},
    )
    projected = await _project_status(
        app, session_id=session_id, writer_id=writer_id, view="evidence", seed=seed + 10
    )
    assert projected["ok"] is True
    items = cast(
        list[Mapping[str, JsonValue]],
        cast(Mapping[str, JsonValue], projected["page"])["items"],
    )
    assert items
    subject_state = items[0].get("subject_state")
    assert isinstance(subject_state, Mapping)
    assert subject_state[present] == _DIGEST
    assert absent not in subject_state


async def test_public_error_omits_unset_safe_details() -> None:
    """PublicErrorModel.safe_details is absent when the error carries no details."""

    model = PublicErrorModel.model_validate(
        {
            "code": "INVALID_REQUEST",
            "message": "The request is invalid.",
            "retryable": False,
            "correlation_id": protocol_id("err_", 2700),
        }
    )
    dumped = model.model_dump(mode="json", exclude_unset=True)
    assert "safe_details" not in dumped
    again = PublicErrorModel.model_validate(dumped)
    assert again.safe_details is None
    assert "safe_details" not in again.model_dump(mode="json", exclude_unset=True)


@pytest.mark.parametrize(
    ("model_type", "payload"),
    (
        (
            StatusCompactObligationModel,
            {
                "obligation_id": protocol_id("obl_", 2801),
                "description": "d",
                "evidence_expectation": "e",
                "acceptance_criteria": None,
            },
        ),
        (
            StatusObligationItemModel,
            {
                "obligation_id": protocol_id("obl_", 2802),
                "status": "open",
                "description": "d",
                "evidence_expectation": "e",
                "source_refs": [],
                "assigned_actor_ids": [],
                "evidence_refs": [],
                "revision_event_id": None,
                "acceptance_criteria": None,
            },
        ),
        (
            PublishWorkAcceptedEventModel,
            {
                "event_id": protocol_id("evt_", 2803),
                "schema_name": "obligation_published",
                "schema_version": "1.0.0",
                "writer_sequence": "1",
                "ingestion_sequence": "1",
                "accepted_at": "2026-07-28T12:00:00.000Z",
                "predecessor_digest": "genesis",
                "entry_digest": "sha256:" + "1" * 64,
                "projection_status": "projected",
                "summary": None,
            },
        ),
        (
            StatusStructuralSubjectStateModel,
            {"tree_digest": _DIGEST, "diff_digest": None},
        ),
        (
            RespondEvidenceSummaryModel,
            {"reference_id": protocol_id("evd_", 2804), "description": None},
        ),
        (
            PublicErrorModel,
            {
                "code": "INVALID_REQUEST",
                "message": "The request is invalid.",
                "retryable": False,
                "correlation_id": protocol_id("err_", 2805),
                "safe_details": None,
            },
        ),
        (
            RespondResponseModel,
            {
                "response_event_id": protocol_id("evt_", 2806),
                "finding_id": protocol_id("fnd_", 2807),
                "finding_frontier": {
                    "sequence": "1",
                    "head_digest": "sha256:" + "0" * 64,
                },
                "disposition": "acknowledged",
                "evidence": [],
                "reason": None,
            },
        ),
    ),
)
def test_closed_model_still_rejects_explicit_null(
    model_type: type[BaseModel], payload: Mapping[str, object]
) -> None:
    """Producers omit unset optionals; the closed models still refuse an explicit null."""

    with pytest.raises(ValidationError, match="optional_field_must_not_be_null"):
        model_type.model_validate(payload)


def test_result_optional_non_null_inventory_is_complete() -> None:
    """Every result model declaring optional_non_null_fields is listed in the inventory table."""

    empty_fields: frozenset[str] = frozenset()
    declared: dict[str, frozenset[str]] = {}
    for name in dir(protocol_models):
        obj = getattr(protocol_models, name)
        if not isinstance(obj, type) or not issubclass(obj, _CLOSED_MODEL) or obj is _CLOSED_MODEL:
            continue
        fields = cast(frozenset[str], getattr(obj, "optional_non_null_fields", empty_fields))
        if not fields or name in _REQUEST_SIDE_OPTIONAL_NON_NULL:
            continue
        declared[name] = fields

    inventoried = {model_type.__name__: fields for model_type, fields in _RESULT_OPTIONAL_NON_NULL}
    assert inventoried == declared, (
        "result optional_non_null inventory drifted: "
        f"missing={declared.keys() - inventoried.keys()} "
        f"extra={inventoried.keys() - declared.keys()} "
        f"field_mismatches="
        f"{
            {
                key: (inventoried.get(key), declared.get(key))
                for key in inventoried.keys() | declared.keys()
                if inventoried.get(key) != declared.get(key)
            }
        }"
    )


def test_every_result_optional_non_null_field_has_an_unset_projection_case() -> None:
    """Each inventoried field is covered by at least one end-to-end unset projection case.

    The mapping is the living index: add a model to ``_RESULT_OPTIONAL_NON_NULL`` and this test
    requires a coverage entry naming the test that projects the field unset.
    """

    covered: dict[tuple[str, str], str] = {
        ("PublishWorkAcceptedEventModel", "summary"): (
            "test_publish_accepted_events_omit_unset_summary"
        ),
        ("PublicErrorModel", "safe_details"): "test_public_error_omits_unset_safe_details",
        ("RespondEvidenceSummaryModel", "description"): (
            "test_respond_omits_unset_optional_response_fields"
        ),
        ("RespondResponseModel", "reason"): "test_respond_omits_unset_optional_response_fields",
        ("RespondResponseModel", "waiver_scope"): (
            "test_respond_omits_unset_optional_response_fields"
        ),
        ("RespondResponseModel", "waiver_expiry"): (
            "test_respond_omits_unset_optional_response_fields"
        ),
        ("StatusCompactObligationModel", "acceptance_criteria"): (
            "test_obligation_without_acceptance_criteria_projects"
        ),
        ("StatusObligationItemModel", "acceptance_criteria"): (
            "test_obligation_without_acceptance_criteria_projects"
        ),
        ("StatusStructuralSubjectStateModel", "diff_digest"): (
            "test_status_evidence_omits_unset_structural_digest"
        ),
        ("StatusStructuralSubjectStateModel", "tree_digest"): (
            "test_status_evidence_omits_unset_structural_digest"
        ),
    }
    expected = {
        (model_type.__name__, field)
        for model_type, fields in _RESULT_OPTIONAL_NON_NULL
        for field in fields
    }
    assert set(covered) == expected, (
        f"unset-projection coverage drifted: missing={expected - set(covered)} "
        f"extra={set(covered) - expected}"
    )
