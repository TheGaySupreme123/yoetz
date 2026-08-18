"""Crash-safe create, attach, and resume orchestration for ``Application.start``."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol, cast

from yoetz.application.unit_of_work import (
    CatalogCompletion,
    CatalogPhaseAdvance,
    CatalogQuarantine,
    PreparedMutation,
    run_catalog_transition,
    run_prepared_append,
)
from yoetz.domain.events import (
    SCHEMA_VERSION,
    EventDraft,
    EventSchema,
    RuntimeProfile,
    SessionOpenedPayload,
    SessionResumedPayload,
    encode_payload,
    media_type_for,
)
from yoetz.domain.values import (
    Actor,
    ActorType,
    Frontier,
    actor_id,
    event_id,
    timestamp_from_datetime,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.ledger import (
    AppendCommand,
    AppendEntry,
    AppendResult,
    OperationKind,
    ProjectionView,
)
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectSource
from yoetz.ports.runtime import (
    BundleProvisionCommand,
    BundleProvisionMode,
    BundleRuntimePort,
    StartCompletionEvidence,
    StartMilestone,
    StartMilestoneExpectation,
    TaskRuntime,
)
from yoetz.ports.start_catalog import (
    EncryptedResultRef,
    SafeReason,
    StartAllocation,
    StartCatalogPort,
    StartCommand,
    StartIdentityInput,
    StartMode,
    StartPhase,
)
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.coverage import (
    AuthorshipAssurance,
    PublicationChannel,
    coverage_for_channel,
)
from yoetz.protocol.errors import ProtocolValueError, PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, validate_id
from yoetz.protocol.models import (
    FrontierModel,
    IntegrationKind,
    StartCompactViewModel,
    StartRequest,
    StartVersionSliceModel,
    StatusCompactItemModel,
)

__all__ = ["StartInternalResult", "execute_start", "start_projection_wire"]

_START_RESULT_MEDIA_TYPE = "application/vnd.yoetz.start_result+json"
_LEGACY_RECEIPT_BLOCKING_COUNT_UNKNOWN = "legacy_receipt_blocking_count_unknown"
_ENGINE_ACTOR_ID = "yoetz.engine"


class _StartApplication(Protocol):
    start_catalog: StartCatalogPort
    runtime: BundleRuntimePort
    clock: ClockPort
    profile: RuntimeProfile
    policy_packs: tuple[str, ...]
    version_manifest: Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class StartInternalResult:
    """Closed structural START success before any client-specific privacy projection."""

    protocol_version: Literal["0.1"]
    schema_version: Literal["1.0.0"]
    request_id: str
    ok: Literal[True]
    outcome: Literal["attached", "created", "replayed"]
    task_id: str
    session_id: str
    writer_id: str
    frontier: FrontierModel
    compact: StartCompactViewModel
    versions: StartVersionSliceModel

    def __post_init__(self) -> None:
        if (
            self.protocol_version != "0.1"
            or self.schema_version != "1.0.0"
            or self.ok is not True
            or self.outcome not in {"attached", "created", "replayed"}
            or type(self.frontier) is not FrontierModel
            or type(self.compact) is not StartCompactViewModel
            or type(self.versions) is not StartVersionSliceModel
        ):
            raise ValueError("invalid_start_internal_result")
        validate_id(IdKind.REQUEST, self.request_id)
        validate_id(IdKind.TASK, self.task_id)
        validate_id(IdKind.SESSION, self.session_id)
        validate_id(IdKind.WRITER, self.writer_id)

    def as_wire(self) -> dict[str, JsonValue]:
        return {
            "compact": cast(JsonValue, self.compact.model_dump(mode="json", exclude_none=False)),
            "frontier": cast(JsonValue, self.frontier.model_dump(mode="json")),
            "ok": True,
            "outcome": self.outcome,
            "protocol_version": self.protocol_version,
            "request_id": self.request_id,
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "versions": cast(JsonValue, self.versions.model_dump(mode="json")),
            "writer_id": self.writer_id,
        }


def start_projection_wire(result: StartInternalResult) -> dict[str, JsonValue]:
    """Add the bound authoring scaffold only at the public projection boundary.

    The durable start-result object intentionally retains its established byte shape. Replays
    decode that legacy object and derive this deterministic scaffold from the same committed
    session, writer, and frontier bindings as a fresh result.
    """

    if type(result) is not StartInternalResult:
        raise TypeError("start_internal_result_invalid")

    def empty_draft_spine() -> dict[str, JsonValue]:
        return {
            "event_id": "",
            "occurred_at": "",
            "causal_parents": [],
            "artifact_refs": [],
            "evidence_refs": [],
        }

    request: dict[str, JsonValue] = {
        "protocol_version": result.protocol_version,
        "schema_version": result.schema_version,
        "request_id": "",
        "actor": {"actor_id": "", "actor_type": ""},
        "client": {"kind": "", "version": "", "integration": ""},
        "session_id": result.session_id,
        "writer_id": result.writer_id,
        "expected_frontier": cast(JsonValue, result.frontier.model_dump(mode="json")),
        "event_drafts": [
            {
                **empty_draft_spine(),
                "schema": {"name": "plan_published", "version": "1.0.0"},
                "payload": {
                    "plan_version": 1,
                    "summary": "",
                    "obligation_refs": [""],
                },
            },
            {
                **empty_draft_spine(),
                "schema": {"name": "obligation_published", "version": "1.0.0"},
                "payload": {
                    "obligation_id": "",
                    "description": "",
                    "acceptance_criteria": "",
                    "evidence_expectation": "",
                    "status": "open",
                },
            },
        ],
    }
    return {
        **result.as_wire(),
        "next_request_template": {
            "evidential": False,
            "operation": "publish_work",
            "arguments": request,
        },
    }


class _StartContradiction(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _error(
    code: PublicErrorCode,
    message: str,
    *,
    retryable: bool = False,
    safe_details: object | None = None,
) -> PublicOperationError:
    return PublicOperationError(code, message, retryable, safe_details=safe_details)


def _invalid_request() -> PublicOperationError:
    return _error(
        PublicErrorCode.INVALID_REQUEST,
        (
            "The start request is invalid. Use the tool input schema; client must include "
            "kind, version, and integration with exact allowed values."
        ),
    )


def _storage_corrupt(code: str) -> PublicOperationError:
    return _error(
        PublicErrorCode.STORAGE_CORRUPT,
        "The local start state is inconsistent.",
        safe_details={"reason_code": code},
    )


def _storage_unsafe() -> PublicOperationError:
    """Keep transient START-result I/O retryable without quarantining the catalog row."""

    return _error(
        PublicErrorCode.STORAGE_UNSAFE,
        "The start result is temporarily unavailable.",
        retryable=True,
    )


def _request_digest(request: StartRequest, command: StartCommand) -> str:
    commitments = command.identity_commitments
    return canonical_digest(
        {
            "actor": cast(
                JsonValue,
                request.actor.model_dump(mode="json", exclude_none=False),
            ),
            "client": cast(JsonValue, request.client.model_dump(mode="json")),
            "identity_commitments": {
                "external_ref": commitments.external_ref_commitment,
                "task_title": commitments.title_commitment,
                "workspace_ref": commitments.workspace_ref_commitment,
            },
            "mode": request.mode,
            "protocol_version": request.protocol_version,
            "requested_view": request.requested_view,
            "repository_privacy_commitment": command.repository_privacy_commitment,
            "schema_version": request.schema_version,
            "session_id": request.session_id,
        }
    )


async def _command(
    app: _StartApplication,
    request: StartRequest,
    repository_privacy_commitment: str | None,
) -> StartCommand:
    try:
        identity = StartIdentityInput(
            request.task_title,
            request.workspace_ref,
            request.external_ref,
        )
        if (
            request.mode == "attach"
            and request.session_id is None
            and request.workspace_ref is None
        ):
            raise _invalid_request()
        commitments = await app.start_catalog.commit_identity(identity)
        provisional = StartCommand(
            operation_id=request.request_id,
            request_digest="sha256:" + "0" * 64,
            mode=StartMode(request.mode),
            identity_input=identity,
            identity_commitments=commitments,
            session_id=request.session_id,
            repository_privacy_commitment=repository_privacy_commitment,
        )
        return StartCommand(
            operation_id=request.request_id,
            request_digest=_request_digest(request, provisional),
            mode=provisional.mode,
            identity_input=identity,
            identity_commitments=commitments,
            session_id=request.session_id,
            repository_privacy_commitment=repository_privacy_commitment,
        )
    except PublicOperationError:
        raise
    except (ProtocolValueError, TypeError, ValueError) as exc:
        raise _invalid_request() from exc


async def _reserve(catalog: StartCatalogPort, command: StartCommand) -> StartAllocation:
    return await run_catalog_transition(catalog, command)


def _version(app: _StartApplication, key: str) -> str:
    value = app.version_manifest.get(key)
    if type(value) is not str:
        raise _StartContradiction("start_bundle_invalid")
    return value


def _provision_command(
    app: _StartApplication, allocation: StartAllocation
) -> BundleProvisionCommand:
    lease = allocation.lease
    if lease is None:
        raise _StartContradiction("start_allocation_ambiguous")
    return BundleProvisionCommand(
        mode=(
            BundleProvisionMode.CREATED
            if allocation.route_action == "created"
            else BundleProvisionMode.ATTACHED
        ),
        task_id=allocation.task_id,
        session_id=allocation.session_id,
        writer_id=allocation.writer_id,
        lifecycle_event_id=allocation.lifecycle_event_id,
        bundle_relpath=allocation.bundle_relpath,
        route_generation=allocation.route_generation,
        route_identity_digest=allocation.route_identity_digest,
        phase=allocation.phase.value,
        response_object_id=allocation.response_object_id,
        owner_generation=lease.owner_generation,
        lease_owner_id=lease.lease_owner_id,
        lease_generation=lease.lease_generation,
        lease_expires_at=lease.lease_expires_at,
        protocol_version=_version(app, "protocol_version"),
        engine_version=_version(app, "engine_version"),
        projection_version=_version(app, "projection_version"),
        bundle_schema_version=_version(app, "bundle_schema_version"),
    )


def _expectation(
    allocation: StartAllocation,
    milestone: StartMilestone,
    result: EncryptedResultRef | None = None,
) -> StartMilestoneExpectation:
    return StartMilestoneExpectation(
        milestone=milestone,
        task_id=allocation.task_id,
        session_id=allocation.session_id,
        writer_id=allocation.writer_id,
        lifecycle_event_id=allocation.lifecycle_event_id,
        route_generation=allocation.route_generation,
        route_identity_digest=allocation.route_identity_digest,
        response_object_id=None if result is None else result.response_object_id,
        response_envelope_digest=None if result is None else result.envelope_digest,
        result_digest=None if result is None else result.result_digest,
    )


async def _advance(
    catalog: StartCatalogPort,
    allocation: StartAllocation,
    phase: StartPhase,
    result: EncryptedResultRef | None = None,
) -> StartAllocation:
    return await run_catalog_transition(catalog, CatalogPhaseAdvance(allocation, phase, result))


def _publication_channel(integration: IntegrationKind) -> PublicationChannel:
    return {
        IntegrationKind.COOPERATIVE_MCP: PublicationChannel.COOPERATIVE_MCP,
        IntegrationKind.LOCAL_CLI: PublicationChannel.LOCAL_CLI,
        IntegrationKind.CODEX_JSONL_IMPORT: PublicationChannel.CODEX_JSONL_IMPORT,
    }[integration]


async def _current_frontier(task: TaskRuntime) -> Frontier:
    return await task.ledger.load_frontier()


async def _lifecycle_append(
    app: _StartApplication,
    request: StartRequest,
    command: StartCommand,
    allocation: StartAllocation,
    task: TaskRuntime,
) -> AppendResult:
    current = await _current_frontier(task)
    if allocation.route_action == "created":
        payload = SessionOpenedPayload(
            task_title=request.task_title,
            client_kind=request.client.kind,
            client_version=request.client.version,
            integration=request.client.integration,
            profile=app.profile,
            external_ref=request.external_ref,
            workspace_ref=request.workspace_ref,
        )
        schema = EventSchema("session_opened", SCHEMA_VERSION)
    else:
        payload = SessionResumedPayload(
            client_kind=request.client.kind,
            client_version=request.client.version,
            integration=request.client.integration,
            profile=app.profile,
            resumed_frontier=current,
        )
        schema = EventSchema("session_resumed", SCHEMA_VERSION)
    payload_bytes = canonical_encode(encode_payload(payload))
    metadata = ObjectMetadata(
        ObjectKind.EVENT_PAYLOAD,
        media_type_for(schema.name),
        allocation.task_id,
        app.clock.now_utc(),
    )
    payload_ref = await task.objects.finalize(
        await task.objects.stage(ObjectSource(data=payload_bytes), metadata)
    )
    draft = EventDraft(
        event_id=event_id(allocation.lifecycle_event_id),
        schema=schema,
        occurred_at=timestamp_from_datetime(app.clock.now_utc()),
        causal_parents=(),
        payload=payload,
        artifact_refs=(),
        evidence_refs=(),
    )
    channel = _publication_channel(request.client.integration)
    entry = AppendEntry(
        draft=draft,
        author=Actor(
            actor_id(_ENGINE_ACTOR_ID),
            ActorType.YOETZ_ENGINE,
            AuthorshipAssurance.SERVICE_AUTHENTICATED,
        ),
        payload_object=payload_ref,
        payload_commitment=payload_ref.commitment,
        media_type=metadata.media_type,
        plaintext_size=payload_ref.plaintext_size,
        publication_channel=channel,
        coverage=coverage_for_channel(channel),
        projection_status="projected",
    )
    append = AppendCommand(
        task_id=allocation.task_id,
        session_id=allocation.session_id,
        writer_id=allocation.writer_id,
        operation_id=request.request_id,
        operation_kind=OperationKind.START,
        request_digest=command.request_digest,
        expected_frontier=current.sequence,
        entries=(entry,),
    )
    return await run_prepared_append(
        task.ledger,
        PreparedMutation(
            writer_id=allocation.writer_id,
            operation_id=request.request_id,
            request_digest=command.request_digest,
            expected_frontier=current.sequence,
            finalized_object_refs=(payload_ref,),
            command=append,
        ),
    )


def _compact_item(value: object, allocation: StartAllocation) -> StatusCompactItemModel:
    if type(value) is not tuple:
        raise _StartContradiction("start_lifecycle_contradiction")
    items = cast(tuple[object, ...], value)
    if len(items) != 1 or type(items[0]) is not StatusCompactItemModel:
        raise _StartContradiction("start_lifecycle_contradiction")
    item = items[0]
    if item.task_id != allocation.task_id or item.session_id != allocation.session_id:
        raise _StartContradiction("start_route_contradiction")
    return item


async def _build_result(
    app: _StartApplication,
    request: StartRequest,
    allocation: StartAllocation,
    task: TaskRuntime,
    frontier: Frontier,
) -> StartInternalResult:
    stored = await task.ledger.load_projection(allocation.session_id, ProjectionView.COMPACT)
    if stored is None or stored.frontier != frontier or stored.lag != 0 or stored.rebuild_required:
        raise _StartContradiction("start_lifecycle_contradiction")
    item = _compact_item(stored.state, allocation)
    compact = StartCompactViewModel.model_validate(
        {
            "coverage": item.coverage.model_dump(mode="json"),
            "current_plan_event_id": item.current_plan_event_id,
            "gaps": list(item.gaps),
            "ledger_freshness": item.freshness,
            "open_obligation_count": item.open_obligation_count,
            "unanswered_finding_count": item.unanswered_finding_count,
            "receipt_blocking_finding_count": item.receipt_blocking_finding_count,
        }
    )
    versions = StartVersionSliceModel.model_validate(
        {
            "engine_version": task.engine_version,
            "policy_packs": list(app.policy_packs),
            "projection_version": task.projection_version,
            "protocol_version": task.protocol_version,
        }
    )
    return StartInternalResult(
        protocol_version="0.1",
        schema_version="1.0.0",
        request_id=request.request_id,
        ok=True,
        outcome=allocation.route_action,
        task_id=allocation.task_id,
        session_id=allocation.session_id,
        writer_id=allocation.writer_id,
        frontier=FrontierModel.model_validate(dict(frontier.as_wire())),
        compact=compact,
        versions=versions,
    )


async def _publish_result(
    app: _StartApplication,
    allocation: StartAllocation,
    task: TaskRuntime,
    result: StartInternalResult,
) -> EncryptedResultRef:
    canonical = canonical_encode(cast(JsonValue, result.as_wire()))
    metadata = ObjectMetadata(
        ObjectKind.START_RESULT,
        _START_RESULT_MEDIA_TYPE,
        allocation.task_id,
        app.clock.now_utc(),
    )
    ref = await task.objects.finalize(
        await task.objects.stage(ObjectSource(data=canonical), metadata)
    )
    return EncryptedResultRef(
        response_object_id=ref.object_id,
        envelope_digest=ref.envelope_digest,
        result_canonical=canonical,
        result_digest=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
    )


def _decode_compact(value: object, outcome: object) -> StartCompactViewModel:
    if not isinstance(value, Mapping):
        raise ProtocolValueError("invalid_start_internal_result")
    compact = dict(cast(Mapping[str, object], value))
    legacy_count = compact.pop("unresolved_finding_count", None)
    if legacy_count is not None:
        if "unanswered_finding_count" in compact or "receipt_blocking_finding_count" in compact:
            raise ProtocolValueError("invalid_start_internal_result")
        compact["unanswered_finding_count"] = legacy_count
        if outcome == "created":
            # A create result is frozen at the first lifecycle event, before any finding can exist.
            compact["receipt_blocking_finding_count"] = "0"
        else:
            # An old attach result retained only the unanswered count. Responded actionable
            # findings cannot be reconstructed from that durable object, so preserve the replay
            # with an explicit unknown rather than manufacturing zero or declaring corruption.
            compact["receipt_blocking_finding_count"] = None
            gaps = compact.get("gaps")
            if not isinstance(gaps, list | tuple):
                raise ProtocolValueError("invalid_start_internal_result")
            compact["gaps"] = sorted(
                {*cast(list[str] | tuple[str, ...], gaps), _LEGACY_RECEIPT_BLOCKING_COUNT_UNKNOWN},
                key=str.encode,
            )
    return StartCompactViewModel.model_validate(compact)


def _decode_result(canonical: bytes) -> StartInternalResult:
    try:
        value = strict_json_parse(canonical)
        if canonical_encode(value) != canonical:
            raise ProtocolValueError("noncanonical_json")
        if not isinstance(value, Mapping) or set(value) != {
            "compact",
            "frontier",
            "ok",
            "outcome",
            "protocol_version",
            "request_id",
            "schema_version",
            "session_id",
            "task_id",
            "versions",
            "writer_id",
        }:
            raise ProtocolValueError("invalid_start_internal_result")
        source = cast(Mapping[str, object], value)
        protocol_version = source["protocol_version"]
        schema_version = source["schema_version"]
        outcome = source["outcome"]
        ok = source["ok"]
        if (
            protocol_version != "0.1"
            or schema_version != "1.0.0"
            or ok is not True
            or outcome not in {"attached", "created", "replayed"}
        ):
            raise ProtocolValueError("invalid_start_internal_result")
        return StartInternalResult(
            protocol_version="0.1",
            schema_version="1.0.0",
            request_id=cast(str, source["request_id"]),
            ok=True,
            outcome=cast(Literal["attached", "created", "replayed"], outcome),
            task_id=cast(str, source["task_id"]),
            session_id=cast(str, source["session_id"]),
            writer_id=cast(str, source["writer_id"]),
            frontier=FrontierModel.model_validate(source["frontier"]),
            compact=_decode_compact(source["compact"], outcome),
            versions=StartVersionSliceModel.model_validate(source["versions"]),
        )
    except (ProtocolValueError, TypeError, ValueError) as exc:
        raise _StartContradiction("start_result_object_missing") from exc


async def _quarantine(
    catalog: StartCatalogPort,
    allocation: StartAllocation,
    contradiction: _StartContradiction,
) -> None:
    await run_catalog_transition(
        catalog,
        CatalogQuarantine(allocation, SafeReason(contradiction.code)),
    )


async def _reopen_result(
    allocation: StartAllocation,
    task: TaskRuntime,
) -> tuple[StartInternalResult, EncryptedResultRef]:
    response_object_id = allocation.response_object_id
    envelope_digest = allocation.response_envelope_digest
    canonical = allocation.response_result_canonical
    result_digest = allocation.response_result_digest
    if (
        response_object_id is None
        or envelope_digest is None
        or canonical is None
        or result_digest is None
    ):
        raise _StartContradiction("start_result_object_missing")
    try:
        ref = await task.objects.resolve_verified(response_object_id, envelope_digest)
    except OSError as exc:
        # An environmental read fault leaves the result-published catalog row resumable. Do not
        # turn it into a contradiction/quarantine: the same request can retry after the storage
        # transient clears.
        raise _storage_unsafe() from exc
    except (ProtocolValueError, PublicOperationError, TypeError, ValueError) as exc:
        raise _StartContradiction("start_result_object_missing") from exc
    if (
        ref.object_id != response_object_id
        or ref.envelope_digest != envelope_digest
        or ref.metadata.kind is not ObjectKind.START_RESULT
        or ref.metadata.task_id != allocation.task_id
        or ref.metadata.media_type != _START_RESULT_MEDIA_TYPE
        or ref.plaintext_size != len(canonical)
    ):
        raise _StartContradiction("start_result_object_missing")
    chunks: list[bytes] = []
    size = 0
    try:
        async for chunk in task.objects.open_verified(ref):
            if type(chunk) is not bytes:
                raise _StartContradiction("start_result_object_missing")
            size += len(chunk)
            if size > len(canonical):
                raise _StartContradiction("start_result_object_missing")
            chunks.append(chunk)
    except _StartContradiction:
        raise
    except OSError as exc:
        raise _storage_unsafe() from exc
    except (ProtocolValueError, PublicOperationError, TypeError, ValueError) as exc:
        raise _StartContradiction("start_result_object_missing") from exc
    observed = b"".join(chunks)
    observed_digest = f"sha256:{hashlib.sha256(observed).hexdigest()}"
    if observed != canonical or observed_digest != result_digest:
        raise _StartContradiction("start_result_object_missing")
    result_ref = EncryptedResultRef(
        response_object_id=response_object_id,
        envelope_digest=envelope_digest,
        result_canonical=canonical,
        result_digest=result_digest,
    )
    return _decode_result(canonical), result_ref


async def execute_start(
    app: _StartApplication,
    request: StartRequest,
    *,
    repository_privacy_commitment: str | None = None,
) -> StartInternalResult:
    """Execute one seven-step start operation against service-owned ports."""

    command = await _command(app, request, repository_privacy_commitment)
    allocation = await _reserve(app.start_catalog, command)
    if allocation.outcome == "replayed":
        if allocation.replayed_result is None:
            raise _storage_corrupt("start_catalog_integrity")
        try:
            return _decode_result(allocation.replayed_result)
        except _StartContradiction as exc:
            raise _storage_corrupt(exc.code) from exc

    task: TaskRuntime | None = None
    try:
        task = await app.runtime.provision_start(_provision_command(app, allocation))
        await app.runtime.verify_start(
            task,
            _expectation(allocation, StartMilestone.BUNDLE_READY),
        )
        if allocation.phase is StartPhase.ROUTE_RESERVED:
            allocation = await _advance(
                app.start_catalog,
                allocation,
                StartPhase.BUNDLE_READY,
            )

        appended = await _lifecycle_append(app, request, command, allocation, task)
        if (
            len(appended.accepted) != 1
            or appended.accepted[0].event_id != allocation.lifecycle_event_id
        ):
            raise _StartContradiction("start_lifecycle_contradiction")
        await app.runtime.verify_start(
            task,
            _expectation(allocation, StartMilestone.LIFECYCLE_COMMITTED),
        )
        if allocation.phase is StartPhase.BUNDLE_READY:
            allocation = await _advance(
                app.start_catalog,
                allocation,
                StartPhase.LIFECYCLE_COMMITTED,
            )

        if allocation.phase is StartPhase.RESULT_PUBLISHED:
            result, result_ref = await _reopen_result(allocation, task)
        else:
            result = await _build_result(
                app,
                request,
                allocation,
                task,
                appended.result_frontier,
            )
            result_ref = await _publish_result(app, allocation, task, result)
            allocation = await _advance(
                app.start_catalog,
                allocation,
                StartPhase.RESULT_PUBLISHED,
                result_ref,
            )

        evidence: StartCompletionEvidence = await app.runtime.verify_start(
            task,
            _expectation(
                allocation,
                StartMilestone.RESULT_PUBLISHED,
                result_ref,
            ),
        )
        await run_catalog_transition(
            app.start_catalog,
            CatalogCompletion(allocation, result_ref, evidence),
        )
        return result
    except _StartContradiction as exc:
        await _quarantine(app.start_catalog, allocation, exc)
        raise _storage_corrupt(exc.code) from exc
    finally:
        if task is not None:
            await app.runtime.release(task)
