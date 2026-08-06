from __future__ import annotations

import asyncio
from collections.abc import Buffer, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

import yoetz.service.daemon as daemon_module
from yoetz.application.publish_work import PublishWorkInternalResult
from yoetz.application.service import (
    ClientProjectionContext,
    ControlProjectionBinding,
    ProjectedControlBody,
    ProjectionBindingFacts,
    ProjectionRenderMode,
)
from yoetz.config.models import LoggingConfig, YoetzConfig
from yoetz.domain.privacy import LocalDisclosureSink
from yoetz.domain.values import Frontier, JsonObject
from yoetz.observability.diagnostics import lookup_diagnostic_records
from yoetz.observability.logging import LogMode
from yoetz.ports.control import (
    ControlCallRequest,
    ControlClientKind,
    ControlError,
    ControlMethod,
    McpRouteProfile,
    ServiceState,
)
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.coverage import PublicationChannel, coverage_for_channel
from yoetz.protocol.errors import PublicErrorCode, PublicOperationError
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import (
    CheckRequest,
    PublishWorkAcceptedEventModel,
    PublishWorkAcceptedProjectionUnavailableModel,
    PublishWorkRequest,
    PublishWorkResult,
    PublishWorkSuccessModel,
    PublishWorkVersionSliceModel,
    StartRequest,
    StartResult,
    StatusRequest,
)
from yoetz.service.confidential_protocol import (
    ClientOpenEnvelope,
    EmptyVaultTarget,
    HumanCeremonyBinding,
    HumanCeremonyKind,
    KeyringRetryPhase,
    ServerCloseEnvelope,
    ServerErrorEnvelope,
    ServerOpenedEnvelope,
    VaultUnlockPreview,
    decode_human_frame,
    encode_human_frame,
)
from yoetz.service.daemon import ServiceComposition, ServiceDaemon
from yoetz.service.lifecycle import LifecycleError, ServiceLifecycle
from yoetz.service.vault import VaultMode

_INSTANCE_ID = "svc_00000000-0000-4000-8000-000000000001"
_UUID = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Clock:
    def now_utc(self) -> datetime:
        return datetime(2026, 7, 19, tzinfo=UTC)

    def monotonic_seconds(self) -> float:
        return 10.0


class _GenerationStore:
    def advance(self, instance_id: str) -> int:
        assert instance_id == _INSTANCE_ID
        return 7


class _Listener:
    def __init__(self) -> None:
        self.closed = False
        self._closed = asyncio.Event()

    async def accept(self) -> object:
        await self._closed.wait()
        raise RuntimeError("closed")

    async def aclose(self) -> None:
        self.closed = True
        self._closed.set()


@dataclass(frozen=True, slots=True)
class _Capability:
    active: bool = True


class _Monitor:
    capability = _Capability()

    def __init__(self) -> None:
        self.callback = None
        self.closed = False

    async def start(self, callback: object) -> None:
        self.callback = callback

    async def close(self) -> None:
        self.closed = True


class _Vault:
    mode = VaultMode.OS_KEYRING
    generation = 3
    ready = True

    def __init__(self) -> None:
        self.lock_count = 0
        self.close_count = 0

    async def lock(self) -> None:
        self.lock_count += 1
        self.ready = False

    async def close(self) -> None:
        self.close_count += 1
        self.ready = False


class _Application:
    def __init__(self) -> None:
        self.start_calls = 0
        self.publish_work_calls = 0
        self.projections: list[ClientProjectionContext] = []
        self.close_count = 0
        self.publish_work_error: PublicOperationError | None = None
        # Stands in for an accepted, durable batch so the post-commit response path can be
        # exercised without a real ledger.
        self.publish_work_result: PublishWorkResult | None = None
        self.publish_work_internal: PublishWorkInternalResult | None = None
        self.projection_error: BaseException | None = None
        self.projection_failures = 0
        self.append_count = 0
        self.publish_response_lookups = 0
        self.publish_response_stores = 0
        self.cached_publish_response: PublishWorkResult | None = None
        self.publish_response_store_error: PublicOperationError | None = None

    async def start(self, request: object) -> StartResult:
        assert isinstance(request, StartRequest)
        self.start_calls += 1
        await asyncio.sleep(0)
        return StartResult.model_validate(
            {
                "protocol_version": "0.1",
                "schema_version": "1.0.0",
                "request_id": request.request_id,
                "ok": False,
                "error": {
                    "code": "INVALID_REQUEST",
                    "message": "The request is invalid.",
                    "retryable": False,
                    "correlation_id": f"err_{_UUID}",
                },
            }
        )

    async def check(self, request: object) -> JsonObject:
        assert isinstance(request, CheckRequest)
        await asyncio.sleep(0)
        # Unprojected stand-in only. Projection is forced to fail in the dedicated correlation
        # tests before any public CheckResult is required.
        return JsonObject({"ok": True, "request_id": request.request_id})

    async def status(self, request: object) -> JsonObject:
        assert isinstance(request, StatusRequest)
        await asyncio.sleep(0)
        return JsonObject({"ok": True, "request_id": request.request_id, "view": request.view})

    async def publish_work(self, request: object) -> PublishWorkResult | PublishWorkInternalResult:
        assert isinstance(request, PublishWorkRequest)
        self.publish_work_calls += 1
        await asyncio.sleep(0)
        if self.publish_work_error is not None:
            raise self.publish_work_error
        if self.publish_work_result is not None:
            return self.publish_work_result
        if self.publish_work_internal is not None:
            outcome = "accepted" if self.append_count == 0 else "replayed"
            if self.append_count == 0:
                self.append_count += 1
            return replace(self.publish_work_internal, outcome=outcome)
        raise AssertionError("publish_work_error_required")

    async def load_publish_response(
        self, result: PublishWorkInternalResult, sink: LocalDisclosureSink
    ) -> PublishWorkResult | None:
        del result
        assert sink is LocalDisclosureSink.AGENT_CONTEXT
        self.publish_response_lookups += 1
        return self.cached_publish_response

    async def store_publish_response(
        self,
        result: PublishWorkInternalResult,
        sink: LocalDisclosureSink,
        projected: ProjectedControlBody,
    ) -> PublishWorkResult:
        del result
        assert sink is LocalDisclosureSink.AGENT_CONTEXT
        assert isinstance(projected, PublishWorkResult)
        self.publish_response_stores += 1
        if self.publish_response_store_error is not None:
            raise self.publish_response_store_error
        if self.cached_publish_response is None:
            self.cached_publish_response = projected
        return self.cached_publish_response

    async def review(self, request: object) -> JsonObject:
        assert isinstance(request, JsonObject)
        return JsonObject({"accepted": True})

    async def projection_binding_facts(
        self,
        method: ControlMethod,
        request: object,
        result: object,
    ) -> ProjectionBindingFacts:
        del method, result
        request_id = getattr(request, "request_id", None)
        return ProjectionBindingFacts(request_id if type(request_id) is str else None, None)

    async def project_result_for_client(
        self,
        context: ClientProjectionContext,
        binding: ControlProjectionBinding,
        result: object,
    ) -> ProjectedControlBody:
        method = binding.method
        assert method in {
            ControlMethod.START,
            ControlMethod.REVIEW,
            ControlMethod.PUBLISH_WORK,
            ControlMethod.CHECK,
            ControlMethod.STATUS,
        }
        self.projections.append(context)
        if self.projection_failures:
            self.projection_failures -= 1
            raise RuntimeError("one-shot-projection-failure")
        if self.projection_error is not None:
            raise self.projection_error
        if type(result) is PublishWorkInternalResult:
            return _projected_publish_work_result(result)
        assert isinstance(result, StartResult | PublishWorkResult | JsonObject)
        return result

    async def close(self) -> None:
        self.close_count += 1


def _start_body() -> StartRequest:
    return StartRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": f"req_{_UUID}",
            "actor": {"actor_id": "harness:test", "actor_type": "harness"},
            "client": {
                "kind": "test_client",
                "version": "0.1.0",
                "integration": "local_cli",
            },
            "mode": "create",
            "task_title": "shared service",
            "requested_view": "compact",
        }
    )


def _publish_work_body() -> PublishWorkRequest:
    return PublishWorkRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": "req_00000000-0000-4000-8000-000000000010",
            "session_id": "ses_00000000-0000-4000-8000-000000000011",
            "writer_id": "wri_00000000-0000-4000-8000-000000000012",
            "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
            "event_drafts": [
                {
                    "event_id": "evt_00000000-0000-4000-8000-000000000013",
                    "schema": {"name": "plan_published", "version": "1.0.0"},
                    "occurred_at": "2026-01-01T00:00:00.000Z",
                    # Deliberately unsorted: application rejects with unsorted_set_field.
                    "causal_parents": [
                        "evt_00000000-0000-4000-8000-000000000002",
                        "evt_00000000-0000-4000-8000-000000000001",
                    ],
                    "payload": {
                        "plan_version": 1,
                        "summary": "Plan",
                        "obligation_refs": [],
                    },
                    "artifact_refs": [],
                    "evidence_refs": [],
                }
            ],
            "actor": {"actor_id": "harness:test", "actor_type": "harness"},
            "client": {
                "kind": "test_client",
                "version": "0.1.0",
                "integration": "local_cli",
            },
        }
    )


def _request(daemon: ServiceDaemon, method: ControlMethod, body: object) -> ControlCallRequest:
    instance = daemon.composition.lifecycle.instance
    return ControlCallRequest(
        kind="call",
        protocol_version="1.0",
        rpc_id=new_id(IdKind.CONTROL_RPC),
        service_instance_id=instance.instance_id,
        service_generation=str(instance.generation),
        method=method,
        body=body,  # pyright: ignore[reportArgumentType]
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("route_profile", "expected"),
    (
        (None, "policy"),
        ("strict", "strict"),
    ),
)
async def test_ready_handler_preserves_check_route_default(
    route_profile: McpRouteProfile | None,
    expected: McpRouteProfile,
) -> None:
    seen: list[object] = []
    marker = object()

    async def handler(_request: object, *, route_profile: object = "policy") -> object:
        seen.append(route_profile)
        return marker

    request = ControlCallRequest(
        kind="call",
        protocol_version="1.0",
        rpc_id=new_id(IdKind.CONTROL_RPC),
        service_instance_id=_INSTANCE_ID,
        service_generation="7",
        method=ControlMethod.CHECK,
        body=_check_body(),
        route_profile=route_profile,
    )

    result = await ServiceDaemon._invoke_ready_handler(  # pyright: ignore[reportPrivateUsage]
        handler, request
    )

    assert result is marker
    assert seen == [expected]


def _daemon() -> tuple[ServiceDaemon, _Application, _Vault, _Listener]:
    application = _Application()
    vault = _Vault()
    listener = _Listener()
    lifecycle = ServiceLifecycle(
        _Clock(),
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "1" * 64,
        instance_id=_INSTANCE_ID,
    )
    composition = ServiceComposition(
        lifecycle=lifecycle,
        control_listener=listener,  # pyright: ignore[reportArgumentType]
        secret_ingress_listener=None,
        human_control_listener=None,
        human_control_service=None,
        session_monitor=_Monitor(),  # pyright: ignore[reportArgumentType]
        vault=vault,
        application=application,
    )
    return ServiceDaemon(_composition=composition), application, vault, listener


def _locked_daemon(
    root: Path,
    factory: object,
) -> tuple[ServiceDaemon, _Vault]:
    vault = _Vault()
    vault.ready = False
    lifecycle = ServiceLifecycle(
        _Clock(),
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "2" * 64,
        instance_id=_INSTANCE_ID,
        singleton_lock_path=root / "service.lock",
    )
    composition = ServiceComposition(
        lifecycle=lifecycle,
        control_listener=_Listener(),  # pyright: ignore[reportArgumentType]
        secret_ingress_listener=None,
        human_control_listener=None,
        human_control_service=None,
        session_monitor=None,
        vault=vault,
        ready_application_factory=factory,  # pyright: ignore[reportArgumentType]
    )
    return ServiceDaemon(_composition=composition), vault


@pytest.mark.anyio
async def test_cli_mcp_and_ui_share_one_ready_application_and_projection() -> None:
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    body = _start_body()
    kinds = (ControlClientKind.CLI, ControlClientKind.MCP_BRIDGE, ControlClientKind.UI)
    results = await asyncio.gather(
        *(daemon.dispatch(kind, _request(daemon, ControlMethod.START, body)) for kind in kinds)
    )
    assert all(result.outcome == "ok" for result in results)
    assert application.start_calls == 3
    assert application.projections == [ClientProjectionContext.fail_safe(kind) for kind in kinds]


@pytest.mark.anyio
async def test_public_operation_error_surfaces_as_ok_false_not_internal_error() -> None:
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    application.publish_work_error = PublicOperationError(
        PublicErrorCode.EVENT_INVALID,
        "The event batch is invalid.",
        False,
        safe_details={"reason_code": "unsorted_set_field"},
    )
    body = _publish_work_body()

    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.PUBLISH_WORK, body),
    )

    assert result.outcome == "ok"
    assert isinstance(result.body, PublishWorkResult)
    assert result.body.root.ok is False
    assert result.body.root.error.code is PublicErrorCode.EVENT_INVALID
    assert result.body.root.error.safe_details == {"reason_code": "unsorted_set_field"}
    assert result.body.root.request_id == body.request_id
    assert result.body.root.error.correlation_id.startswith("err_")
    assert application.publish_work_calls == 1
    assert application.projections == []
    assert not isinstance(result.body, ControlError)
    await daemon.close()


@pytest.mark.anyio
async def test_runtime_value_error_through_dispatch_is_correlated_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runtime ValueError after validation must not collapse into frame_invalid (#126 C9)."""

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()

    async def boom(request: object) -> object:
        del request
        raise ValueError("recovery_generation_not_advanced")

    application.start = boom  # type: ignore[method-assign]

    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.START, _start_body()),
    )

    assert result.outcome == "error"
    assert isinstance(result.body, ControlError)
    assert result.body.reason == "internal_error"
    assert result.body.correlation_id is not None
    found = lookup_diagnostic_records(result.body.correlation_id, root=tmp_path)
    assert len(found) == 1
    assert found[0]["component"] == "service.daemon"
    assert found[0]["operation"] == "start_internal_error"
    await daemon.close()


@pytest.mark.anyio
async def test_genuinely_malformed_control_request_is_still_frame_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yoetz.service.control_protocol import ControlProtocolError

    daemon, _application, _vault, _listener = _daemon()
    await daemon.start()

    def _reject(_request: object) -> None:
        raise ControlProtocolError("frame_invalid")

    monkeypatch.setattr(daemon_module, "validate_request", _reject)
    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.START, _start_body()),
    )
    assert result.outcome == "error"
    assert isinstance(result.body, ControlError)
    assert result.body.reason == "frame_invalid"
    await daemon.close()


def _accepted_publish_work_result(request_id: str) -> PublishWorkResult:
    """An ok:false body is enough here: only the post-commit response path is under test."""

    return PublishWorkResult.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": request_id,
            "ok": False,
            "error": {
                "code": "INVALID_REQUEST",
                "message": "The request is invalid.",
                "retryable": False,
                "correlation_id": f"err_{_UUID}",
            },
        }
    )


def _publish_work_internal(request: PublishWorkRequest) -> PublishWorkInternalResult:
    frontier = Frontier(1, "sha256:" + "4" * 64)
    return PublishWorkInternalResult(
        protocol_version="0.1",
        schema_version="1.0.0",
        request_id=request.request_id,
        request_digest=canonical_digest({"request_id": request.request_id}),
        ok=True,
        outcome="accepted",
        task_id="tsk_00000000-0000-4000-8000-000000000020",
        session_id=request.session_id,
        writer_id=request.writer_id,
        subject_frontier=Frontier.genesis(),
        result_frontier=frontier,
        accepted_events=(
            PublishWorkAcceptedEventModel(
                event_id=cast(str, cast(Mapping[str, object], request.event_drafts[0])["event_id"]),
                schema_name="plan_published",
                schema_version="1.0.0",
                writer_sequence="1",
                ingestion_sequence="1",
                accepted_at="2026-01-01T00:00:00.000Z",
                predecessor_digest="genesis",
                entry_digest=frontier.head_digest,
                projection_status="projected",
            ),
        ),
        warning_codes=(),
        coverage=coverage_for_channel(PublicationChannel.LOCAL_CLI),
        gaps=(),
        versions=PublishWorkVersionSliceModel(
            protocol_version="0.1",
            engine_version="0.1.0",
            projection_version="0.1.0",
            policy_packs=("research-evidence/0.1.0", "work-integrity/0.1.0"),
        ),
    )


def _projected_publish_work_result(internal: PublishWorkInternalResult) -> PublishWorkResult:
    wire = internal.as_json()
    accepted = cast(tuple[dict[str, object], ...], wire["accepted_events"])
    for event in accepted:
        event.pop("summary", None)
    return PublishWorkResult.model_validate(
        {
            **wire,
            "privacy_projection": {
                "sink": "agent_context",
                "local_disclosure_receipt_id": "egr_00000000-0000-4000-8000-000000000021",
                "policy_id": "pvy_00000000-0000-4000-8000-000000000022",
                "policy_version": "1",
                "policy_digest": "sha256:" + "5" * 64,
                "included_categories": (),
                "blocked_categories": (),
                "omitted_pointers": (),
                "projection_commitment": "hmac-sha256:" + "6" * 64,
            },
        }
    )


@pytest.mark.anyio
async def test_post_commit_projection_failure_returns_total_acceptance_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An accepted write must never be surfaced to the caller as a failure.

    The handler returns (so a batch is durable) and only then does response shaping fail. The
    caller receives a reduced ``ok: true`` envelope with frontiers and accepted event facts, not
    ``response_projection_failed`` / ``INTERNAL_ERROR``. The envelope correlation id is the same
    id filed in the durable diagnostic sink.
    """

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    body = _publish_work_body()
    application.publish_work_internal = _publish_work_internal(body)
    application.projection_error = RuntimeError("post-commit-shape-failure")

    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.PUBLISH_WORK, body),
    )

    assert result.outcome == "ok"
    assert isinstance(result.body, PublishWorkResult)
    root = result.body.root
    assert type(root) is PublishWorkAcceptedProjectionUnavailableModel
    assert root.ok is True
    assert root.response_completeness == "accepted_projection_unavailable"
    assert root.reason_code == "response_projection_failed"
    assert root.request_id == body.request_id
    assert root.result_frontier.sequence == "1"
    assert root.result_frontier.head_digest == "sha256:" + "4" * 64
    assert len(root.accepted_events) == 1
    assert root.accepted_events[0].event_id == "evt_00000000-0000-4000-8000-000000000013"
    assert root.correlation_id.startswith("err_")
    found = lookup_diagnostic_records(root.correlation_id, root=tmp_path)
    assert len(found) == 1
    assert found[0]["operation"] == "publish_work_response_projection_failed"
    assert found[0]["component"] == "service.daemon"
    for forbidden in ("traceback", "exception", "payload", "path", "message"):
        assert forbidden not in found[0]
    # The operation itself ran exactly once and is not retried behind the caller's back.
    assert application.publish_work_calls == 1
    assert len(application.projections) == 1
    await daemon.close()


def _check_body() -> CheckRequest:
    return CheckRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": "req_00000000-0000-4000-8000-000000000030",
            "session_id": "ses_00000000-0000-4000-8000-000000000031",
            "writer_id": "wri_00000000-0000-4000-8000-000000000032",
            "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
            "mode": "deterministic_only",
            "actor": {"actor_id": "harness:test", "actor_type": "harness"},
            "client": {
                "kind": "test_client",
                "version": "0.1.0",
                "integration": "cooperative_mcp",
            },
        }
    )


def _status_body() -> StatusRequest:
    return StatusRequest.model_validate(
        {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": "req_00000000-0000-4000-8000-000000000040",
            "session_id": "ses_00000000-0000-4000-8000-000000000041",
            "writer_id": "wri_00000000-0000-4000-8000-000000000042",
            "view": "compact",
            "limit": "10",
            "actor": {"actor_id": "harness:test", "actor_type": "harness"},
            "client": {
                "kind": "test_client",
                "version": "0.1.0",
                "integration": "cooperative_mcp",
            },
        }
    )


@pytest.mark.anyio
async def test_check_response_projection_failure_correlation_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The correlation id on a check ControlError resolves to exactly one diagnostic record."""

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    application.projection_error = RuntimeError("check-shape-failure-must-not-leak")

    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.CHECK, _check_body()),
    )

    assert result.outcome == "error"
    assert isinstance(result.body, ControlError)
    assert result.body.reason == "response_projection_failed"
    assert result.body.retryable is True
    assert result.body.correlation_id is not None
    found = lookup_diagnostic_records(result.body.correlation_id, root=tmp_path)
    assert len(found) == 1
    assert found[0]["operation"] == "check_response_projection_failed"
    assert found[0]["component"] == "service.daemon"
    assert found[0]["request_id"] == "req_00000000-0000-4000-8000-000000000030"
    for forbidden in ("traceback", "exception", "payload", "path", "message"):
        assert forbidden not in found[0]
    assert "must-not-leak" not in str(found[0])
    await daemon.close()


@pytest.mark.anyio
async def test_status_read_projection_failure_correlation_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The correlation id on a status ControlError resolves to exactly one diagnostic record."""

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    application.projection_error = RuntimeError("status-shape-failure-must-not-leak")

    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.STATUS, _status_body()),
    )

    assert result.outcome == "error"
    assert isinstance(result.body, ControlError)
    assert result.body.reason == "read_projection_failed"
    assert result.body.retryable is True
    assert result.body.correlation_id is not None
    found = lookup_diagnostic_records(result.body.correlation_id, root=tmp_path)
    assert len(found) == 1
    assert found[0]["operation"] == "status_read_projection_failed"
    assert found[0]["component"] == "service.daemon"
    assert found[0]["request_id"] == "req_00000000-0000-4000-8000-000000000040"
    for forbidden in ("traceback", "exception", "payload", "path", "message"):
        assert forbidden not in found[0]
    assert "must-not-leak" not in str(found[0])
    await daemon.close()


@pytest.mark.anyio
async def test_status_handler_attribute_error_is_retryable_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unexpected failure during application.status is retryable, never terminal internal_error.

    Run-4 item_40: AttributeError inside the operation branch escaped as status_internal_error
    with retryable:false. A pure read that changed nothing must present as read_projection_failed.
    """

    import yoetz.observability.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()

    async def boom(request: object) -> object:
        del request
        raise AttributeError("'NoneType' object has no attribute 'sequence'")

    application.status = boom  # type: ignore[method-assign]

    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.STATUS, _status_body()),
    )

    assert result.outcome == "error"
    assert isinstance(result.body, ControlError)
    assert result.body.reason == "read_projection_failed"
    assert result.body.retryable is True
    assert result.body.correlation_id is not None
    found = lookup_diagnostic_records(result.body.correlation_id, root=tmp_path)
    assert len(found) == 1
    assert found[0]["operation"] == "status_read_projection_failed"
    assert found[0]["component"] == "service.daemon"
    assert found[0]["request_id"] == "req_00000000-0000-4000-8000-000000000040"
    for forbidden in ("traceback", "exception", "payload", "path", "message", "sequence"):
        assert forbidden not in found[0]
    await daemon.close()


@pytest.mark.anyio
async def test_publish_one_shot_projection_failure_is_accepted_without_replay() -> None:
    """Projection failure after append still returns acceptance; replay is optional, not required."""

    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    body = _publish_work_body()
    application.publish_work_internal = _publish_work_internal(body)
    application.projection_failures = 1

    first_request = _request(daemon, ControlMethod.PUBLISH_WORK, body)
    first = await daemon.dispatch(ControlClientKind.MCP_BRIDGE, first_request)
    assert first.outcome == "ok"
    assert isinstance(first.body, PublishWorkResult)
    first_root = first.body.root
    assert type(first_root) is PublishWorkAcceptedProjectionUnavailableModel
    assert first_root.result_frontier.sequence == "1"
    assert first_root.result_frontier.head_digest == "sha256:" + "4" * 64
    assert len(first_root.accepted_events) == 1
    assert application.append_count == 1
    # Reduced envelope is not stored as a full projected success body.
    assert application.publish_response_stores == 0

    # A later identical call may still project fully and cache the complete success body.
    second_request = _request(daemon, ControlMethod.PUBLISH_WORK, body)
    second = await daemon.dispatch(ControlClientKind.MCP_BRIDGE, second_request)
    assert second.outcome == "ok"
    assert isinstance(second.body, PublishWorkResult)
    assert type(second.body.root) is PublishWorkSuccessModel
    assert application.append_count == 1
    assert len(application.projections) == 2
    assert application.publish_response_stores == 1

    third_request = _request(daemon, ControlMethod.PUBLISH_WORK, body)
    third = await daemon.dispatch(ControlClientKind.MCP_BRIDGE, third_request)
    assert third.outcome == "ok"
    assert third.body == second.body
    assert third.rpc_id != second.rpc_id
    assert application.append_count == 1
    assert len(application.projections) == 2
    assert application.publish_response_lookups == 3
    assert application.publish_response_stores == 1
    await daemon.close()


@pytest.mark.anyio
async def test_publish_response_store_failure_does_not_degrade_valid_success() -> None:
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    body = _publish_work_body()
    application.publish_work_internal = _publish_work_internal(body)
    application.publish_response_store_error = PublicOperationError(
        PublicErrorCode.STORAGE_CORRUPT,
        "The stored publish response is invalid.",
        False,
    )

    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.PUBLISH_WORK, body),
    )

    assert result.outcome == "ok"
    assert isinstance(result.body, PublishWorkResult)
    assert result.body.root.ok is True
    assert application.append_count == 1
    assert application.publish_response_stores == 1
    assert application.cached_publish_response is None
    await daemon.close()


@pytest.mark.anyio
async def test_deliberate_post_commit_control_error_is_not_reclassified() -> None:
    """Bounded projection failures already say something true and must survive unchanged."""

    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    body = _publish_work_body()
    application.publish_work_result = _accepted_publish_work_result(body.request_id)
    application.projection_error = ControlError("privacy_projection_blocked")

    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.PUBLISH_WORK, body),
    )

    assert result.outcome == "error"
    assert isinstance(result.body, ControlError)
    assert result.body.reason == "privacy_projection_blocked"
    await daemon.close()


@pytest.mark.anyio
async def test_dispatch_passes_trusted_human_projection_context() -> None:
    daemon, application, _vault, _listener = _daemon()
    await daemon.start()
    body = _start_body()
    context = ClientProjectionContext(
        ControlClientKind.CLI,
        ProjectionRenderMode.HUMAN_READABLE,
        True,
    )

    result = await daemon.dispatch(
        ControlClientKind.CLI,
        _request(daemon, ControlMethod.START, body),
        projection_context=context,
    )

    assert result.outcome == "ok"
    assert application.projections == [context]

    cross_paired = await daemon.dispatch(
        ControlClientKind.UI,
        _request(daemon, ControlMethod.START, body),
        projection_context=context,
    )
    assert cross_paired.outcome == "error"
    assert isinstance(cross_paired.body, ControlError)
    assert cross_paired.body.reason == "frame_invalid"
    assert application.projections == [context]
    assert daemon.status().state is ServiceState.READY
    await daemon.close()


@pytest.mark.anyio
async def test_client_kind_and_state_admission_fail_closed() -> None:
    daemon, application, vault, _listener = _daemon()
    await daemon.start()
    forbidden = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.SERVICE_STATUS, JsonObject({})),
    )
    assert forbidden.outcome == "error"
    assert isinstance(forbidden.body, ControlError)
    assert forbidden.body.reason == "method_forbidden"

    locked = await daemon.dispatch(
        ControlClientKind.CLI,
        _request(daemon, ControlMethod.SERVICE_LOCK, JsonObject({})),
    )
    assert locked.outcome == "ok"
    assert daemon.status().state is ServiceState.LOCKED
    assert application.close_count == 1
    assert vault.lock_count >= 1

    rejected = await daemon.dispatch(
        ControlClientKind.UI,
        _request(daemon, ControlMethod.START, _start_body()),
    )
    assert rejected.outcome == "error"
    assert isinstance(rejected.body, ControlError)
    assert rejected.body.reason == "vault_locked"
    await daemon.close()


@pytest.mark.anyio
async def test_shutdown_is_idempotent_and_closes_owned_listener() -> None:
    daemon, application, vault, listener = _daemon()
    await daemon.start()
    await daemon.stop()
    await daemon.stop()
    assert listener.closed
    assert application.close_count == 1
    assert vault.close_count == 1


class _PassphraseVault:
    mode = VaultMode.PASSPHRASE
    generation = 3
    ready = False

    def __init__(self) -> None:
        self.lock_count = 0
        self.close_count = 0
        self.unlock_count = 0
        self._secret: bytes | None = None

    def expect_secret(self, value: bytes) -> None:
        self._secret = value

    async def unlock(self, handle: object) -> None:
        if self._secret is not None and handle != self._secret:
            raise AssertionError("unexpected unlock secret")
        self.unlock_count += 1
        self.ready = True
        self.generation += 1

    async def lock(self) -> None:
        self.lock_count += 1
        self.ready = False

    async def close(self) -> None:
        self.close_count += 1
        self.ready = False


class _SecretMemory:
    def capture(self, purpose: object, secret: bytearray) -> object:
        del purpose
        return bytes(secret)

    def close(self) -> None:
        return None


def _patch_auto_unlock_store(
    monkeypatch: pytest.MonkeyPatch,
    secret: bytes | None,
    reason: str = "none",
) -> None:
    class _Store:
        def load_with_reason(self) -> tuple[bytearray | None, str]:
            if secret is None:
                return None, reason
            return bytearray(secret), reason

    def _factory(_bundle: Path) -> _Store:
        return _Store()

    monkeypatch.setattr(daemon_module, "AutoUnlockPassphraseStore", _factory)


def _soft_lock_daemon(
    tmp_path: Path,
    *,
    factory: object,
    vault: _PassphraseVault,
) -> ServiceDaemon:
    lifecycle = ServiceLifecycle(
        _Clock(),
        generation_store=_GenerationStore(),
        process_start_identity_commitment="sha256:" + "4" * 64,
        instance_id=_INSTANCE_ID,
        singleton_lock_path=tmp_path / "service.lock",
    )
    composition = ServiceComposition(
        lifecycle=lifecycle,
        control_listener=_Listener(),  # pyright: ignore[reportArgumentType]
        secret_ingress_listener=None,
        human_control_listener=None,
        human_control_service=None,
        session_monitor=None,
        vault=vault,  # pyright: ignore[reportArgumentType]
        ready_application_factory=factory,  # pyright: ignore[reportArgumentType]
        secret_memory=_SecretMemory(),  # pyright: ignore[reportArgumentType]
        auto_unlock_bundle=tmp_path,
    )
    return ServiceDaemon(_composition=composition)


@pytest.mark.anyio
async def test_soft_lock_auto_ready_reopens_on_next_ordinary_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _Application()
    vault = _PassphraseVault()
    secret = b"correct horse battery staple!!"
    vault.expect_secret(secret)

    async def factory(service_generation: int, vault_generation: int) -> _Application:
        assert service_generation == 7
        assert vault_generation == 4
        return application

    _patch_auto_unlock_store(monkeypatch, secret)
    daemon = _soft_lock_daemon(tmp_path, factory=factory, vault=vault)
    await daemon.start()
    assert daemon.status().state is ServiceState.LOCKED
    daemon._state_reason = "idle_relock"  # pyright: ignore[reportPrivateUsage]

    result = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.START, _start_body()),
    )

    assert result.outcome == "ok"
    assert vault.unlock_count == 1
    assert daemon.status().state is ServiceState.READY
    assert daemon.status().state_reason == "none"
    assert application.start_calls == 1
    await daemon.close()


@pytest.mark.anyio
async def test_explicit_lock_does_not_auto_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _Application()
    vault = _PassphraseVault()

    async def factory(service_generation: int, vault_generation: int) -> _Application:
        del service_generation, vault_generation
        return application

    _patch_auto_unlock_store(monkeypatch, b"correct horse battery staple!!")
    daemon = _soft_lock_daemon(tmp_path, factory=factory, vault=vault)
    await daemon.start()
    daemon._state_reason = "explicit_lock"  # pyright: ignore[reportPrivateUsage]

    rejected = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.START, _start_body()),
    )

    assert rejected.outcome == "error"
    assert isinstance(rejected.body, ControlError)
    assert rejected.body.reason == "vault_locked"
    assert vault.unlock_count == 0
    assert daemon.status().state is ServiceState.LOCKED
    await daemon.close()


@pytest.mark.anyio
async def test_monitor_loss_does_not_auto_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing the session monitor requires a ceremony, unlike the recoverable soft locks.

    Idle, session lock, and suspend all describe conditions the service can watch recover from.
    Monitor loss removes the capability that produces those events for the life of the process,
    so auto-re-ready would make the lock momentary and then run on with session-lock relock
    silently no longer applying -- the containment ADR-008 describes, gone without notice.
    """

    application = _Application()
    vault = _PassphraseVault()

    async def factory(service_generation: int, vault_generation: int) -> _Application:
        del service_generation, vault_generation
        return application

    _patch_auto_unlock_store(monkeypatch, b"correct horse battery staple!!")
    daemon = _soft_lock_daemon(tmp_path, factory=factory, vault=vault)
    await daemon.start()
    daemon._state_reason = "monitor_lost"  # pyright: ignore[reportPrivateUsage]

    rejected = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.START, _start_body()),
    )

    assert rejected.outcome == "error"
    assert isinstance(rejected.body, ControlError)
    assert rejected.body.reason == "vault_locked"
    assert vault.unlock_count == 0
    assert daemon.status().state is ServiceState.LOCKED
    await daemon.close()


@pytest.mark.anyio
async def test_soft_lock_absent_auto_unlock_stays_hard_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = _Application()
    vault = _PassphraseVault()

    async def factory(service_generation: int, vault_generation: int) -> _Application:
        del service_generation, vault_generation
        return application

    _patch_auto_unlock_store(monkeypatch, None, "auto_unlock_absent")
    daemon = _soft_lock_daemon(tmp_path, factory=factory, vault=vault)
    await daemon.start()
    daemon._state_reason = "idle_relock"  # pyright: ignore[reportPrivateUsage]

    rejected = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.START, _start_body()),
    )
    assert rejected.outcome == "error"
    assert isinstance(rejected.body, ControlError)
    assert rejected.body.reason == "vault_locked"
    assert vault.unlock_count == 0
    assert daemon.status().state_reason == "passphrase_required"

    again = await daemon.dispatch(
        ControlClientKind.MCP_BRIDGE,
        _request(daemon, ControlMethod.START, _start_body()),
    )
    assert again.outcome == "error"
    assert vault.unlock_count == 0
    await daemon.close()


@pytest.mark.anyio
async def test_idle_close_publishes_idle_relock_reason() -> None:
    daemon, application, vault, _listener = _daemon()
    await daemon.start()
    assert daemon.status().state_reason == "none"
    # Lifecycle idle drain closes ready without daemon.lock; the close path must publish
    # idle_relock so soft auto-ready can recognize the reason.
    await daemon._close_ready_locked()  # pyright: ignore[reportPrivateUsage]
    assert application.close_count == 1
    assert vault.lock_count >= 1
    assert daemon.status().state_reason == "idle_relock"
    await daemon.close()


@pytest.mark.anyio
async def test_unlock_activation_constructs_exact_generation_once(tmp_path: Path) -> None:
    application = _Application()
    calls: list[tuple[int, int]] = []

    async def factory(service_generation: int, vault_generation: int) -> _Application:
        calls.append((service_generation, vault_generation))
        return application

    daemon, vault = _locked_daemon(tmp_path, factory)
    await daemon.start()
    assert daemon.status().state is ServiceState.LOCKED
    await daemon.composition.lifecycle.transition(ServiceState.UNLOCKING)
    vault.ready = True

    await daemon.activate_ready_application(7, 3)

    assert calls == [(7, 3)]
    assert daemon.status().state is ServiceState.READY
    assert daemon.status().state_reason == "none"
    await daemon.lock()
    assert application.close_count == 1
    assert not vault.ready
    await daemon.close()


@pytest.mark.anyio
async def test_preunlocked_vault_activates_ready_application_on_daemon_start(
    tmp_path: Path,
) -> None:
    application = _Application()
    calls: list[tuple[int, int]] = []

    async def factory(service_generation: int, vault_generation: int) -> _Application:
        calls.append((service_generation, vault_generation))
        return application

    daemon, vault = _locked_daemon(tmp_path, factory)
    vault.ready = True

    await daemon.start()

    assert calls == [(7, 3)]
    assert daemon.status().state is ServiceState.READY
    await daemon.close()


@pytest.mark.anyio
async def test_locked_passphrase_vault_never_reports_keyring_locked(tmp_path: Path) -> None:
    async def factory(_service_generation: int, _vault_generation: int) -> _Application:
        return _Application()

    daemon, vault = _locked_daemon(tmp_path, factory)
    vault.mode = VaultMode.PASSPHRASE

    await daemon.start()

    assert daemon.status().state is ServiceState.LOCKED
    assert daemon.status().state_reason == "passphrase_required"
    await daemon.close()


@pytest.mark.anyio
async def test_unlock_activation_revalidates_after_factory_and_closes_partial(
    tmp_path: Path,
) -> None:
    application = _Application()
    daemon: ServiceDaemon
    vault: _Vault

    async def factory(_service_generation: int, _vault_generation: int) -> _Application:
        vault.generation = 4
        return application

    daemon, vault = _locked_daemon(tmp_path, factory)
    await daemon.start()
    await daemon.composition.lifecycle.transition(ServiceState.UNLOCKING)
    vault.ready = True

    with pytest.raises(LifecycleError, match="invalid_transition"):
        await daemon.activate_ready_application(7, 3)

    assert application.close_count == 1
    assert not vault.ready
    assert daemon.status().state is ServiceState.LOCKED
    assert daemon.status().state_reason == "unlock_failed"
    await daemon.close()


@pytest.mark.anyio
async def test_lock_serializes_with_in_flight_ready_activation(tmp_path: Path) -> None:
    application = _Application()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def factory(_service_generation: int, _vault_generation: int) -> _Application:
        entered.set()
        await release.wait()
        return application

    daemon, vault = _locked_daemon(tmp_path, factory)
    await daemon.start()
    await daemon.composition.lifecycle.transition(ServiceState.UNLOCKING)
    vault.ready = True
    activation = asyncio.create_task(daemon.activate_ready_application(7, 3))
    await entered.wait()
    locking = asyncio.create_task(daemon.lock("explicit_lock"))
    await asyncio.sleep(0)
    assert not locking.done()

    release.set()
    await activation
    await locking

    assert daemon.status().state is ServiceState.LOCKED
    assert application.close_count == 1
    assert not vault.ready
    await daemon.close()


@pytest.mark.anyio
async def test_production_composition_starts_locked_before_ready_only_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    metadata = tmp_path / "state"
    paths = daemon_module._ProductionPaths(  # pyright: ignore[reportPrivateUsage]
        root,
        metadata / "service-generation.json",
        metadata / "unlock-throttle.json",
        metadata / "service.lock",
    )
    bound: list[str] = []

    async def bind(kind: str) -> _Listener:
        bound.append(kind)
        return _Listener()

    binders = daemon_module._ListenerBinders(  # pyright: ignore[reportPrivateUsage]
        lambda: bind("control"),  # pyright: ignore[reportArgumentType]
        lambda: bind("secret"),  # pyright: ignore[reportArgumentType]
        lambda: bind("human"),  # pyright: ignore[reportArgumentType]
    )
    composition = await daemon_module._production_composition(  # pyright: ignore[reportPrivateUsage]
        _config=YoetzConfig(),
        _paths=paths,
        _binders=binders,
    )
    daemon = ServiceDaemon(_composition=composition)

    await daemon.start()

    assert bound == ["control", "secret", "human"]
    assert daemon.status().state is ServiceState.LOCKED
    assert daemon.status().vault_mode == "uninitialized"
    assert daemon.status().capabilities == ("confidential_ingress",)
    assert not (root / "vault").exists()
    await daemon.close()


def test_installation_marker_round_trip_is_canonical_and_self_authenticated(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    marker_path = root / "installation-state.json"
    store = daemon_module._InstallationStateStore(  # pyright: ignore[reportPrivateUsage]
        marker_path,
        tmp_path / "unlock-throttle.json",
        tmp_path / "service-generation.json",
    )
    digest = "sha256:" + "3" * 64

    store.publish(VaultMode.OS_KEYRING, None, digest)
    loaded = store.load()

    assert loaded is not None
    assert loaded.vault_mode is VaultMode.OS_KEYRING
    assert loaded.mode_binding_digest == digest
    assert marker_path.read_bytes().endswith(b"\n")
    assert marker_path.stat().st_mode & 0o077 == 0

    corrupted = bytearray(marker_path.read_bytes())
    digest_start = corrupted.rfind(b"sha256:") + len(b"sha256:")
    corrupted[digest_start] = ord("0") if corrupted[digest_start] != ord("0") else ord("1")
    marker_path.write_bytes(corrupted)
    with pytest.raises(RuntimeError, match="installation_marker_invalid"):
        store.load()


@pytest.mark.anyio
async def test_service_entry_point_installs_the_structural_log_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected-dispatch correlation ids are useless unless this sink is installed."""

    installed: list[LogMode] = []

    class _Daemon:
        def __init__(self, *, _composition: object) -> None:
            del _composition

        async def serve(self) -> None:
            return None

    async def composition(*, _config: YoetzConfig) -> object:
        assert type(_config) is YoetzConfig
        return object()

    def load(
        overrides: Mapping[str, str], env: Mapping[str, str], path: Path | None
    ) -> YoetzConfig:
        del overrides, env, path
        return YoetzConfig()

    def configure(config: LoggingConfig, mode: LogMode) -> None:
        del config
        installed.append(mode)

    monkeypatch.setattr(daemon_module, "load_config", load)
    monkeypatch.setattr(daemon_module, "configure_logging", configure)
    monkeypatch.setattr(daemon_module, "_production_composition", composition)
    monkeypatch.setattr(daemon_module, "ServiceDaemon", _Daemon)

    with pytest.raises(SystemExit):
        await daemon_module.run_service()

    assert installed == [LogMode.SERVICE]


@pytest.mark.anyio
async def test_human_connection_cancels_an_open_ceremony_when_terminal_disconnects() -> None:
    """An interrupted foreground setup must not block every later ceremony."""

    ceremony_id = "a" * 64

    class _Stream:
        def __init__(self) -> None:
            self._incoming = bytearray(
                encode_human_frame(
                    ClientOpenEnvelope(
                        "b" * 64,
                        HumanCeremonyKind.VAULT_UNLOCK,
                        EmptyVaultTarget(expected_mode="passphrase"),
                    )
                )
            )
            self.sent: list[bytes] = []
            self.closed = False

        @property
        def peer_identity(self) -> object:
            return object()

        async def receive(self, max_bytes: int) -> bytes:
            if not self._incoming:
                return b""
            size = min(max_bytes, len(self._incoming))
            chunk = bytes(self._incoming[:size])
            del self._incoming[:size]
            return chunk

        async def send_all(self, data: Buffer) -> None:
            self.sent.append(bytes(data))

        async def aclose(self) -> None:
            self.closed = True

    class _HumanService:
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        async def open_ceremony(self, request: ClientOpenEnvelope) -> ServerOpenedEnvelope:
            return ServerOpenedEnvelope(
                ceremony_id,
                1,
                HumanCeremonyBinding(
                    binding_version=1,
                    ceremony_id=ceremony_id,
                    connection_nonce=request.connection_nonce,
                    ceremony_kind=HumanCeremonyKind.VAULT_UNLOCK,
                    service_instance_id=_INSTANCE_ID,
                    service_generation=7,
                    vault_generation=3,
                    policy_generation=None,
                    target_digest="sha256:" + "2" * 64,
                    expires_at_monotonic_ms=1_000_000,
                ),
                VaultUnlockPreview(),
                KeyringRetryPhase(),
            )

        async def cancel(self, received_ceremony_id: str) -> ServerCloseEnvelope:
            self.cancelled.append(received_ceremony_id)
            return ServerCloseEnvelope(received_ceremony_id, 3, "cancelled")

    stream = _Stream()
    service = _HumanService()
    handler = daemon_module._HumanConnectionServer(service)  # pyright: ignore[reportPrivateUsage, reportArgumentType]

    await handler(stream)

    assert service.cancelled == [ceremony_id]
    assert stream.closed
    assert [type(decode_human_frame(item)) for item in stream.sent] == [
        ServerOpenedEnvelope,
        ServerErrorEnvelope,
    ]
