from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from yoetz.application.service import (
    Application,
    ClientProjectionContext,
    ProjectionRenderMode,
    VerificationPolicy,
    resolve_client_disclosure_sink,
)
from yoetz.domain.events import RuntimeProfile
from yoetz.domain.privacy import LocalDisclosureSink
from yoetz.domain.values import JsonObject
from yoetz.ports.control import ControlClientKind, ControlError, ControlMethod
from yoetz.ports.start_catalog import StartCatalogPort, TaskRoute, TaskRouteState
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.models import CheckRequest

_REQUEST = "req_00000000-0000-4000-8000-000000000001"
_TASK = "tsk_00000000-0000-4000-8000-000000000002"
_SESSION = "ses_00000000-0000-4000-8000-000000000003"


class _Catalog:
    def __init__(self, route: TaskRoute | None) -> None:
        self.route = route
        self.calls = 0

    async def resolve_route(self, session_id: str) -> TaskRoute | None:
        assert session_id == _SESSION
        self.calls += 1
        return self.route


def _route(*, task_id: str = _TASK, state: TaskRouteState = TaskRouteState.ACTIVE) -> TaskRoute:
    return TaskRoute(
        task_id,
        _SESSION,
        f"tasks/{task_id}",
        1,
        state,
        canonical_digest(
            {"task_id": task_id, "bundle_relpath": f"tasks/{task_id}", "route_generation": 1}
        ),
    )


def _application(catalog: _Catalog) -> Application:
    return Application(
        start_catalog=cast(StartCatalogPort, catalog),
        runtime=object(),  # pyright: ignore[reportArgumentType]
        clock=object(),  # pyright: ignore[reportArgumentType]
        ids=object(),  # pyright: ignore[reportArgumentType]
        verification_policy=VerificationPolicy(),
        privacy=object(),  # pyright: ignore[reportArgumentType]
        status_cursor_key=b"cursor",
        waiver_policy_digest="sha256:" + "0" * 64,
        semantic_evaluator=object(),  # pyright: ignore[reportArgumentType]
        disclosure_scope_for=object(),  # pyright: ignore[reportArgumentType]
        receipt_version_resolver=object(),  # pyright: ignore[reportArgumentType]
        waiver_authorizer=lambda _: False,
        import_publication_authorizer=lambda _: False,
        profile=RuntimeProfile.TEST_FAKE,
        policy_packs=("research-evidence/0.1.0", "work-integrity/0.1.0"),
        version_manifest={},
    )


@pytest.mark.parametrize(
    ("context", "expected"),
    (
        (
            ClientProjectionContext(
                ControlClientKind.CLI,
                ProjectionRenderMode.HUMAN_READABLE,
                True,
            ),
            LocalDisclosureSink.LOCAL_HUMAN_VIEW,
        ),
        (
            ClientProjectionContext(
                ControlClientKind.CLI,
                ProjectionRenderMode.HUMAN_READABLE,
                False,
            ),
            LocalDisclosureSink.AGENT_CONTEXT,
        ),
        (
            ClientProjectionContext(
                ControlClientKind.CLI,
                ProjectionRenderMode.MACHINE_READABLE,
                True,
            ),
            LocalDisclosureSink.AGENT_CONTEXT,
        ),
        (
            ClientProjectionContext(
                ControlClientKind.MCP_BRIDGE,
                ProjectionRenderMode.HUMAN_READABLE,
                True,
            ),
            LocalDisclosureSink.AGENT_CONTEXT,
        ),
        (
            ClientProjectionContext(
                ControlClientKind.UI,
                ProjectionRenderMode.HUMAN_READABLE,
                True,
            ),
            LocalDisclosureSink.AGENT_CONTEXT,
        ),
    ),
)
def test_projection_context_resolves_sink_fail_closed(
    context: ClientProjectionContext, expected: LocalDisclosureSink
) -> None:
    assert resolve_client_disclosure_sink(context) is expected


def test_projection_context_fail_safe_default_is_machine_agent_context() -> None:
    context = ClientProjectionContext.fail_safe(ControlClientKind.CLI)

    assert context == ClientProjectionContext(
        ControlClientKind.CLI,
        ProjectionRenderMode.MACHINE_READABLE,
        False,
    )
    assert resolve_client_disclosure_sink(context) is LocalDisclosureSink.AGENT_CONTEXT


@pytest.mark.parametrize(
    "kwargs",
    (
        {"client_kind": "cli"},
        {"render_mode": "human_readable"},
        {"output_is_controlling_tty": 1},
    ),
)
def test_projection_context_rejects_untyped_facts(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "client_kind": ControlClientKind.CLI,
        "render_mode": ProjectionRenderMode.HUMAN_READABLE,
        "output_is_controlling_tty": True,
    }
    values.update(kwargs)
    with pytest.raises(TypeError):
        ClientProjectionContext(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("semantic", "mode"),
    (
        ("disabled", "deterministic_only"),
        ("optional", "semantic_if_configured"),
        ("required", "semantic_required"),
    ),
)
def test_verification_policy_maps_exact_check_mode(semantic: str, mode: str) -> None:
    policy = VerificationPolicy(semantic=semantic, max_findings=3)  # type: ignore[arg-type]

    assert policy.default_check_mode == mode


@pytest.mark.parametrize("maximum", (True, 0, 11))
def test_verification_policy_rejects_invalid_maximum(maximum: object) -> None:
    with pytest.raises(ValueError, match="verification_max_findings_invalid"):
        VerificationPolicy(max_findings=maximum)  # type: ignore[arg-type]


def test_verification_policy_rejects_unknown_semantic_mode() -> None:
    with pytest.raises(ValueError, match="verification_semantic_invalid"):
        VerificationPolicy(semantic="automatic")  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_projection_binding_facts_resolve_exact_active_catalog_route() -> None:
    route = _route()
    catalog = _Catalog(route)
    facts = await _application(catalog).projection_binding_facts(
        ControlMethod.IMPORT_CODEX_JSONL,
        {"request_id": _REQUEST},
        JsonObject(
            {
                "schema_version": "1.0.0",
                "request_id": _REQUEST,
                "task_id": _TASK,
                "session_id": _SESSION,
            }
        ),
    )

    assert facts.original_request_id == _REQUEST
    assert facts.route_identity_digest == route.route_identity_digest
    assert catalog.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize("route", (None, _route(state=TaskRouteState.QUARANTINED)))
async def test_projection_binding_facts_fail_closed_for_missing_or_stale_route(
    route: TaskRoute | None,
) -> None:
    with pytest.raises(ControlError, match="privacy_projection_unavailable"):
        await _application(_Catalog(route)).projection_binding_facts(
            ControlMethod.IMPORT_CODEX_JSONL,
            {"request_id": _REQUEST},
            JsonObject(
                {
                    "schema_version": "1.0.0",
                    "request_id": _REQUEST,
                    "task_id": _TASK,
                    "session_id": _SESSION,
                }
            ),
        )


@pytest.mark.anyio
async def test_projection_binding_facts_fail_closed_for_route_task_mismatch() -> None:
    other_task = "tsk_00000000-0000-4000-8000-000000000099"
    with pytest.raises(ControlError, match="privacy_projection_unavailable"):
        await _application(_Catalog(_route(task_id=other_task))).projection_binding_facts(
            ControlMethod.IMPORT_CODEX_JSONL,
            {"request_id": _REQUEST},
            JsonObject(
                {
                    "schema_version": "1.0.0",
                    "request_id": _REQUEST,
                    "task_id": _TASK,
                    "session_id": _SESSION,
                }
            ),
        )


@pytest.mark.anyio
async def test_installation_scoped_support_does_not_resolve_a_task_route() -> None:
    catalog = _Catalog(None)
    facts = await _application(catalog).projection_binding_facts(
        ControlMethod.PRIVACY_GET_SETUP,
        {},
        JsonObject({"schema_version": "1.0.0", "setup_state": "ready"}),
    )

    assert facts.original_request_id is None
    assert facts.route_identity_digest is None
    assert catalog.calls == 0


def _check_request(mode: str | None) -> CheckRequest:
    payload: dict[str, object] = {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": _REQUEST,
        "actor": {"actor_id": "harness:pytest", "actor_type": "harness"},
        "client": {
            "kind": "cooperative_agent",
            "version": "0.1.0",
            "integration": "cooperative_mcp",
        },
        "session_id": _SESSION,
        "writer_id": "wri_00000000-0000-4000-8000-000000000004",
        "expected_frontier": {"sequence": "0", "head_digest": "genesis"},
    }
    if mode is not None:
        payload["mode"] = mode
    return CheckRequest.model_validate(payload)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("semantic", "expected"),
    (
        ("disabled", "deterministic_only"),
        ("optional", "semantic_if_configured"),
        ("required", "semantic_required"),
    ),
)
async def test_omitted_check_mode_resolves_through_the_verification_policy(
    monkeypatch: pytest.MonkeyPatch, semantic: str, expected: str
) -> None:
    """The recorded check must carry a concrete mode, never an absent one to interpret later."""

    seen: list[object] = []

    async def _capture(_app: object, request: object) -> object:
        seen.append(request)
        return object()

    monkeypatch.setattr("yoetz.application.check.execute_check", _capture)
    application = _application(_Catalog(_route()))
    policy = VerificationPolicy(semantic=semantic, max_findings=3)  # type: ignore[arg-type]
    application = replace(application, verification_policy=policy)

    await application.check(_check_request(None))

    resolved = cast(CheckRequest, seen[0])
    assert resolved.mode == expected


@pytest.mark.anyio
async def test_present_check_mode_is_never_overridden_by_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller that asked for deterministic_only under a required policy still gets what it asked."""

    seen: list[object] = []

    async def _capture(_app: object, request: object) -> object:
        seen.append(request)
        return object()

    monkeypatch.setattr("yoetz.application.check.execute_check", _capture)
    application = replace(
        _application(_Catalog(_route())),
        verification_policy=VerificationPolicy(semantic="required", max_findings=3),
    )

    await application.check(_check_request("deterministic_only"))

    assert cast(CheckRequest, seen[0]).mode == "deterministic_only"
