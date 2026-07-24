"""Frozen contracts shared by the ready application facade and service daemon."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Protocol, cast

from yoetz.application.egress import PrivacyCoordinator
from yoetz.application.observation_verification import ObservationVerificationSupervisor
from yoetz.domain.events import RuntimeProfile
from yoetz.domain.privacy import (
    AuthorizationScope,
    AuthorizationScopeKind,
    CandidateContext,
    CandidateContextItem,
    LocalDisclosureApproved,
    LocalDisclosureBlocked,
    LocalDisclosureSink,
    LocalDisclosureUnavailable,
    ProjectionAuditContext,
    ProjectionProvenanceContext,
)
from yoetz.domain.receipts import ReceiptVersionSlice
from yoetz.domain.values import (
    Frontier,
    JsonObject,
    frontier_from_json,
    validate_sha256_digest,
)
from yoetz.ports.clock import ClockPort
from yoetz.ports.control import ControlClientKind, ControlError, ControlMethod
from yoetz.ports.ids import IdPort
from yoetz.ports.ledger import CheckCommitResult, FrozenCase
from yoetz.ports.runtime import BundleRuntimePort, TaskRuntime
from yoetz.ports.start_catalog import StartCatalogPort, TaskRouteState
from yoetz.protocol.canonical import (
    JsonValue,
    canonical_digest,
    canonical_encode,
    strict_json_parse,
)
from yoetz.protocol.ids import IdKind, validate_id
from yoetz.protocol.models import (
    CheckResult,
    CheckResultModel,
    CheckSuccessModel,
    DataCategory,
    PublishWorkRequest,
    PublishWorkResult,
    PublishWorkResultModel,
    PublishWorkSuccessModel,
    ReceiptRequest,
    ReceiptResult,
    ReceiptResultModel,
    ReceiptSuccessModel,
    RespondRequest,
    RespondResult,
    RespondResultModel,
    RespondSuccessModel,
    StartRequest,
    StartResult,
    StartResultModel,
    StartSuccessModel,
    StatusRequest,
    StatusResult,
    StatusResultModel,
    StatusSuccessModel,
    classify_result_leaf,
)

__all__ = [
    "ClientProjectionContext",
    "ControlProjectionBinding",
    "ProjectionBindingFacts",
    "Application",
    "ProjectedControlBody",
    "ProjectionRenderMode",
    "ReadyApplicationFactory",
    "ServiceReadyContext",
    "UnprojectedControlBody",
    "VerificationPolicy",
    "resolve_client_disclosure_sink",
]

_MAX_FINDINGS_LIMIT = 10


class ProjectionRenderMode(str, Enum):  # noqa: UP042 - closed internal enum
    """How the authenticated ordinary client will render the projected body."""

    HUMAN_READABLE = "human_readable"
    MACHINE_READABLE = "machine_readable"


@dataclass(frozen=True, slots=True)
class ClientProjectionContext:
    """Trusted service-side facts used to choose one ordinary disclosure sink."""

    client_kind: ControlClientKind
    render_mode: ProjectionRenderMode
    output_is_controlling_tty: bool

    def __post_init__(self) -> None:
        if type(self.client_kind) is not ControlClientKind:
            raise TypeError("projection_client_kind_invalid")
        if type(self.render_mode) is not ProjectionRenderMode:
            raise TypeError("projection_render_mode_invalid")
        if type(self.output_is_controlling_tty) is not bool:
            raise TypeError("projection_tty_fact_invalid")

    @classmethod
    def fail_safe(cls, client_kind: ControlClientKind) -> ClientProjectionContext:
        """Construct the non-human default used when presentation facts are absent."""

        return cls(
            client_kind=client_kind,
            render_mode=ProjectionRenderMode.MACHINE_READABLE,
            output_is_controlling_tty=False,
        )


@dataclass(frozen=True, slots=True, repr=False)
class ControlProjectionBinding:
    """Trusted control and route facts bound to one ordinary-client projection."""

    rpc_id: str
    method: ControlMethod
    service_instance_id: str
    service_generation: int
    original_request_id: str | None
    route_identity_digest: str | None
    control_request_canonical: bytes

    def __post_init__(self) -> None:
        validate_id(IdKind.CONTROL_RPC, self.rpc_id)
        if type(self.method) is not ControlMethod:
            raise TypeError("projection_method_invalid")
        validate_id(IdKind.SERVICE_INSTANCE, self.service_instance_id)
        if type(self.service_generation) is not int or self.service_generation <= 0:
            raise ValueError("projection_service_generation_invalid")
        if self.original_request_id is not None:
            validate_id(IdKind.REQUEST, self.original_request_id)
        if self.route_identity_digest is not None:
            validate_sha256_digest(self.route_identity_digest)
        if type(self.control_request_canonical) is not bytes or not self.control_request_canonical:
            raise TypeError("projection_control_request_invalid")
        try:
            wire = strict_json_parse(self.control_request_canonical)
            if canonical_encode(wire) != self.control_request_canonical or not isinstance(
                wire, Mapping
            ):
                raise ValueError("projection_control_request_invalid")
            source = cast(Mapping[str, JsonValue], wire)
            if (
                source.get("rpc_id") != self.rpc_id
                or source.get("method") != self.method.value
                or source.get("service_instance_id") != self.service_instance_id
                or source.get("service_generation") != str(self.service_generation)
            ):
                raise ValueError("projection_control_request_mismatch")
        except (TypeError, ValueError) as exc:
            raise ValueError("projection_control_request_invalid") from exc

    def __repr__(self) -> str:
        return "ControlProjectionBinding(<redacted>)"


@dataclass(frozen=True, slots=True)
class ProjectionBindingFacts:
    """Route authority resolved inside the ready application for daemon binding."""

    original_request_id: str | None
    route_identity_digest: str | None

    def __post_init__(self) -> None:
        if self.original_request_id is not None:
            validate_id(IdKind.REQUEST, self.original_request_id)
        if self.route_identity_digest is not None:
            validate_sha256_digest(self.route_identity_digest)


def resolve_client_disclosure_sink(context: ClientProjectionContext) -> LocalDisclosureSink:
    """Resolve the sole ordinary-client sink without accepting a caller-named sink."""

    if type(context) is not ClientProjectionContext:
        raise TypeError("projection_context_invalid")
    if (
        context.client_kind is ControlClientKind.CLI
        and context.render_mode is ProjectionRenderMode.HUMAN_READABLE
        and context.output_is_controlling_tty
    ):
        return LocalDisclosureSink.LOCAL_HUMAN_VIEW
    return LocalDisclosureSink.AGENT_CONTEXT


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    """Immutable application snapshot of the two verification configuration choices."""

    semantic: Literal["disabled", "optional", "required"] = "optional"
    max_findings: int = 3

    def __post_init__(self) -> None:
        if self.semantic not in {"disabled", "optional", "required"}:
            raise ValueError("verification_semantic_invalid")
        if type(self.max_findings) is not int or not 1 <= self.max_findings <= _MAX_FINDINGS_LIMIT:
            raise ValueError("verification_max_findings_invalid")

    @property
    def default_check_mode(
        self,
    ) -> Literal["deterministic_only", "semantic_if_configured", "semantic_required"]:
        """Map the configured semantic default to the frozen check-request vocabulary."""

        if self.semantic == "disabled":
            return "deterministic_only"
        if self.semantic == "required":
            return "semantic_required"
        return "semantic_if_configured"


type ProjectedControlBody = (
    StartResult
    | PublishWorkResult
    | CheckResult
    | RespondResult
    | StatusResult
    | ReceiptResult
    | JsonObject
)


# These imports intentionally follow ``VerificationPolicy``: check.py consumes that immutable
# configuration type, while the facade owns the closed union of all use-case internal results.
from yoetz.application.check import check_internal_json  # noqa: E402
from yoetz.application.import_review import (  # noqa: E402
    ImportCodexJsonlRequest,
    ImportReportInternal,
    ReviewInternal,
    ReviewRequest,
    execute_import_codex_jsonl,
    execute_review,
)
from yoetz.application.publish_work import (  # noqa: E402
    PublishWorkInternalResult,
    execute_publish_work,
)
from yoetz.application.receipt import ReceiptInternalResult, execute_receipt  # noqa: E402
from yoetz.application.respond import RespondInternalResult, execute_respond  # noqa: E402
from yoetz.application.start import StartInternalResult, execute_start  # noqa: E402
from yoetz.application.status import StatusInternalResult, execute_status  # noqa: E402
from yoetz.domain.findings import (  # noqa: E402
    Finding,
)

type UnprojectedControlBody = (
    StartInternalResult
    | PublishWorkInternalResult
    | CheckCommitResult
    | RespondInternalResult
    | StatusInternalResult
    | ReceiptInternalResult
    | ImportReportInternal
    | ReviewInternal
    | JsonObject
)


class _SemanticEvaluator(Protocol):
    def __call__(self, frozen: FrozenCase, findings: tuple[Finding, ...]) -> Awaitable[object]: ...


type _ScopeResolver = Callable[
    [ControlProjectionBinding, Mapping[str, JsonValue]], AuthorizationScope
]
type _ReceiptVersions = Callable[[TaskRuntime], ReceiptVersionSlice]
type _SupportHandler = Callable[[object], Awaitable[JsonObject]]


def _empty_support_handlers() -> Mapping[ControlMethod, _SupportHandler]:
    return {}


_WORKFLOW_METHODS = frozenset(
    {
        ControlMethod.START,
        ControlMethod.PUBLISH_WORK,
        ControlMethod.CHECK,
        ControlMethod.RESPOND,
        ControlMethod.STATUS,
        ControlMethod.RECEIPT,
    }
)
_STRUCTURAL_SUPPORT_METHODS = frozenset(
    {
        ControlMethod.IMPORT_CODEX_JSONL,
        ControlMethod.PRIVACY_GET_SETUP,
        ControlMethod.PRIVACY_GET_EFFECTIVE,
        ControlMethod.PRIVACY_PROPOSE_POLICY,
        ControlMethod.PRIVACY_TIGHTEN_POLICY,
        ControlMethod.BACKUP_PREVIEW,
        ControlMethod.BACKUP_EXECUTE,
        ControlMethod.OBSERVATION_INGEST,
        ControlMethod.OBSERVATION_STATUS,
        ControlMethod.OBSERVATION_PAUSE,
        ControlMethod.OBSERVATION_RESUME,
        ControlMethod.OBSERVATION_REVOKE,
    }
)
_PATH_BEARING_SUPPORT_METHODS = frozenset(
    {
        ControlMethod.RESTORE_PREVIEW,
        ControlMethod.RESTORE_EXECUTE,
        ControlMethod.MIGRATE_PREVIEW,
        ControlMethod.MIGRATE_EXECUTE,
        ControlMethod.INTEGRATION_PREVIEW,
        ControlMethod.INTEGRATION_EXECUTE,
    }
)


def _pointer_matches(pointer: str, pattern: str) -> bool:
    actual = pointer.removeprefix("/").split("/")
    expected = pattern.removeprefix("/").split("/")
    return len(actual) == len(expected) and all(
        wanted == "*" or wanted == found for found, wanted in zip(actual, expected, strict=True)
    )


def _classify_support_result_leaf(
    method: ControlMethod,
    source: Mapping[str, JsonValue],
    pointer: str,
) -> Literal["public_structural"] | DataCategory:
    """Closed support classification; unknown methods and fields fail closed as content."""

    if method is ControlMethod.REVIEW:
        prefix = "/check_result"
        if pointer.startswith(f"{prefix}/"):
            nested = source.get("check_result")
            if not isinstance(nested, Mapping):
                raise TypeError("review_internal_check_invalid")
            return classify_result_leaf(
                ControlMethod.CHECK.value,
                cast(Mapping[str, JsonValue], nested),
                pointer.removeprefix(prefix),
            )
        return "public_structural"
    if method in _STRUCTURAL_SUPPORT_METHODS:
        return "public_structural"
    if method in _PATH_BEARING_SUPPORT_METHODS:
        if any(
            _pointer_matches(pointer, pattern)
            for pattern in (
                "/changed_files/*",
                "/file_changes/*/relative_path",
                "/file_states/*/relative_path",
            )
        ):
            return DataCategory.COMMAND_METADATA
        return "public_structural"
    return DataCategory.COMMAND_METADATA


def _internal_json(result: UnprojectedControlBody) -> dict[str, JsonValue]:
    if type(result) is StartInternalResult:
        return result.as_wire()
    if type(result) is PublishWorkInternalResult:
        return result.as_json()
    if type(result) is CheckCommitResult:
        return dict(check_internal_json(result).items())
    if type(result) is RespondInternalResult:
        return result.as_json()
    if type(result) is StatusInternalResult:
        return result.as_json()
    if type(result) is ReceiptInternalResult:
        return result.as_json()
    if type(result) is ImportReportInternal:
        return dict(result.as_json().items())
    if type(result) is ReviewInternal:
        return dict(result.as_json().items())
    if type(result) is JsonObject:
        return dict(result.items())
    raise TypeError("unprojected_control_body_invalid")


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _leaves(value: JsonValue, pointer: str = "") -> tuple[tuple[str, JsonValue], ...]:
    rows: list[tuple[str, JsonValue]] = []
    if isinstance(value, Mapping):
        for key, item in cast(Mapping[str, JsonValue], value).items():
            rows.extend(_leaves(item, f"{pointer}/{_escape_pointer(key)}"))
    elif type(value) in {tuple, list}:
        for index, item in enumerate(cast(tuple[JsonValue, ...] | list[JsonValue], value)):
            rows.extend(_leaves(item, f"{pointer}/{index}"))
    else:
        rows.append((pointer, value))
    return tuple(rows)


def _segments(pointer: str) -> tuple[str, ...]:
    if not pointer.startswith("/"):
        raise ValueError("projection_pointer_invalid")
    return tuple(
        segment.replace("~1", "/").replace("~0", "~")
        for segment in pointer.removeprefix("/").split("/")
    )


def _replace_pointer(root: JsonValue, pointer: str, replacement: JsonValue) -> JsonValue:
    parts = _segments(pointer)

    def replace_at(value: JsonValue, depth: int) -> JsonValue:
        if depth == len(parts):
            return replacement
        part = parts[depth]
        if isinstance(value, Mapping):
            source = dict(cast(Mapping[str, JsonValue], value))
            source[part] = replace_at(source[part], depth + 1)
            return source
        if type(value) in {tuple, list}:
            source_list = list(cast(tuple[JsonValue, ...] | list[JsonValue], value))
            index = int(part)
            source_list[index] = replace_at(source_list[index], depth + 1)
            return tuple(source_list)
        raise ValueError("projection_pointer_invalid")

    return replace_at(root, 0)


def _frontier_for_projection(source: Mapping[str, JsonValue]) -> Frontier:
    raw = source.get("subject_frontier", source.get("frontier"))
    if raw is None:
        raise ValueError("projection_frontier_missing")
    return frontier_from_json(raw)


def _public_model(method: ControlMethod, value: Mapping[str, JsonValue]) -> ProjectedControlBody:
    model = {
        ControlMethod.START: (StartSuccessModel, StartResultModel),
        ControlMethod.PUBLISH_WORK: (PublishWorkSuccessModel, PublishWorkResultModel),
        ControlMethod.CHECK: (CheckSuccessModel, CheckResultModel),
        ControlMethod.RESPOND: (RespondSuccessModel, RespondResultModel),
        ControlMethod.STATUS: (StatusSuccessModel, StatusResultModel),
        ControlMethod.RECEIPT: (ReceiptSuccessModel, ReceiptResultModel),
    }.get(method)
    if model is None:
        return JsonObject(value)
    success_type, result_type = model
    success = success_type.model_validate(value)
    # ``RespondResponseModel`` declares ``optional_non_null_fields`` (reason/waiver_scope/
    # waiver_expiry) that the frozen wire schema requires to be entirely omitted when absent,
    # never present as an explicit null. A blanket ``exclude_none=False`` round trip here would
    # reintroduce that null for an ordinary acknowledged response and crash the reflexive
    # re-validation below, so respond alone excludes it; the other five operations have no
    # ordinarily-absent ``optional_non_null_fields`` member and keep the established behavior.
    exclude_none = method is ControlMethod.RESPOND
    return result_type.model_validate(success.model_dump(mode="json", exclude_none=exclude_none))


@dataclass(frozen=True, slots=True)
class Application:
    """Ready-only facade with one sink-independent workflow and one disclosure boundary."""

    start_catalog: StartCatalogPort
    runtime: BundleRuntimePort
    clock: ClockPort
    ids: IdPort
    verification_policy: VerificationPolicy
    privacy: PrivacyCoordinator
    status_cursor_key: bytes
    waiver_policy_digest: str
    semantic_evaluator: _SemanticEvaluator
    disclosure_scope_for: _ScopeResolver
    receipt_version_resolver: _ReceiptVersions
    waiver_authorizer: Callable[[RespondRequest], bool]
    import_publication_authorizer: Callable[[object], bool]
    profile: RuntimeProfile
    policy_packs: tuple[str, ...]
    version_manifest: Mapping[str, JsonValue]
    support_handlers: Mapping[ControlMethod, _SupportHandler] = field(
        default_factory=_empty_support_handlers
    )
    verification_supervisor: ObservationVerificationSupervisor | None = None
    _close_lock: asyncio.Lock = field(init=False, repr=False, compare=False)
    _close_task: asyncio.Task[None] | None = field(
        init=False, default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_close_lock", asyncio.Lock())

    async def start(self, request: StartRequest) -> StartInternalResult:
        return await execute_start(self, request)  # pyright: ignore[reportArgumentType]

    async def publish_work(self, request: PublishWorkRequest) -> PublishWorkInternalResult:
        return await execute_publish_work(self, request)  # pyright: ignore[reportArgumentType]

    async def check(self, request: object) -> CheckCommitResult:
        from yoetz.application.check import execute_check

        return await execute_check(self, request)  # pyright: ignore[reportArgumentType]

    async def respond(self, request: RespondRequest) -> RespondInternalResult:
        return await execute_respond(self, request)  # pyright: ignore[reportArgumentType]

    async def status(self, request: StatusRequest) -> StatusInternalResult:
        return await execute_status(self, request)  # pyright: ignore[reportArgumentType]

    async def receipt(self, request: ReceiptRequest) -> ReceiptInternalResult:
        return await execute_receipt(self, request)  # pyright: ignore[reportArgumentType]

    async def import_codex_jsonl(self, request: ImportCodexJsonlRequest) -> ImportReportInternal:
        return await execute_import_codex_jsonl(
            self,  # pyright: ignore[reportArgumentType]
            request,
        )

    async def review(self, request: ReviewRequest) -> ReviewInternal:
        return await execute_review(self, request)  # pyright: ignore[reportArgumentType]

    async def privacy_get_setup(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.PRIVACY_GET_SETUP, request)

    async def privacy_get_effective(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.PRIVACY_GET_EFFECTIVE, request)

    async def privacy_propose_policy(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.PRIVACY_PROPOSE_POLICY, request)

    async def privacy_tighten_policy(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.PRIVACY_TIGHTEN_POLICY, request)

    async def privacy_receipts_list(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.PRIVACY_RECEIPTS_LIST, request)

    async def privacy_receipts_get(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.PRIVACY_RECEIPTS_GET, request)

    async def backup_preview(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.BACKUP_PREVIEW, request)

    async def backup_execute(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.BACKUP_EXECUTE, request)

    async def restore_preview(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.RESTORE_PREVIEW, request)

    async def restore_execute(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.RESTORE_EXECUTE, request)

    async def migrate_preview(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.MIGRATE_PREVIEW, request)

    async def migrate_execute(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.MIGRATE_EXECUTE, request)

    async def integration_preview(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.INTEGRATION_PREVIEW, request)

    async def integration_execute(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.INTEGRATION_EXECUTE, request)

    async def observation_ingest(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.OBSERVATION_INGEST, request)

    async def observation_status(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.OBSERVATION_STATUS, request)

    async def observation_pause(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.OBSERVATION_PAUSE, request)

    async def observation_resume(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.OBSERVATION_RESUME, request)

    async def observation_revoke(self, request: object) -> JsonObject:
        return await self._support(ControlMethod.OBSERVATION_REVOKE, request)

    async def _support(self, method: ControlMethod, request: object) -> JsonObject:
        handler = self.support_handlers.get(method)
        if handler is None:
            raise ControlError("method_forbidden")
        result = await handler(request)
        if type(result) is not JsonObject or "privacy_projection" in result:
            raise TypeError("support_internal_body_invalid")
        return result

    async def projection_binding_facts(
        self,
        method: ControlMethod,
        request: object,
        result: UnprojectedControlBody,
    ) -> ProjectionBindingFacts:
        """Resolve the exact post-workflow route facts without exposing runtime internals."""

        source = _internal_json(result)
        request_id_value = getattr(request, "request_id", None)
        if request_id_value is None and isinstance(request, Mapping):
            request_id_value = cast(Mapping[object, object], request).get("request_id")
        original_request_id = request_id_value if type(request_id_value) is str else None
        session_value = source.get("session_id")
        if session_value is None:
            return ProjectionBindingFacts(original_request_id, None)
        if type(session_value) is not str:
            raise TypeError("projection_session_invalid")
        route = await self.start_catalog.resolve_route(session_value)
        task_value = source.get("task_id")
        if (
            route is None
            or route.state is not TaskRouteState.ACTIVE
            or route.session_id != session_value
            or type(task_value) is not str
            or route.task_id != task_value
        ):
            raise ControlError("privacy_projection_unavailable", retryable=True)
        return ProjectionBindingFacts(original_request_id, route.route_identity_digest)

    def authorizes_waiver(self, request: RespondRequest) -> bool:
        return self.waiver_authorizer(request)

    def authorizes_import_publication(self, request: object) -> bool:
        return self.import_publication_authorizer(request)

    def receipt_versions_for(self, runtime: TaskRuntime) -> ReceiptVersionSlice:
        return self.receipt_version_resolver(runtime)

    async def evaluate_semantic_check(
        self, frozen: FrozenCase, deterministic_findings: tuple[Finding, ...]
    ) -> object:
        return await self.semantic_evaluator(frozen, deterministic_findings)

    async def project_result_for_client(
        self,
        context: ClientProjectionContext,
        binding: ControlProjectionBinding,
        result: UnprojectedControlBody,
    ) -> ProjectedControlBody:
        source = _internal_json(result)
        method = binding.method
        if "privacy_projection" in source or (
            method in _WORKFLOW_METHODS and source.get("ok") is not True
        ):
            raise TypeError("unprojected_control_body_invalid")
        scope = self.disclosure_scope_for(binding, source)
        needs_route = scope.kind in {
            AuthorizationScopeKind.TASK,
            AuthorizationScopeKind.REQUEST,
        }
        if (binding.route_identity_digest is not None) != needs_route:
            raise TypeError("projection_route_binding_invalid")
        sink = resolve_client_disclosure_sink(context)
        items: list[CandidateContextItem] = []
        for ordinal, (pointer, value) in enumerate(_leaves(source), start=1):
            classification = (
                classify_result_leaf(method.value, source, pointer)
                if method in _WORKFLOW_METHODS
                else _classify_support_result_leaf(method, source, pointer)
            )
            if classification == "public_structural":
                continue
            items.append(
                CandidateContextItem(
                    f"leaf-{ordinal}",
                    classification,
                    scope,
                    pointer,
                    canonical_encode(value),
                )
            )
        source_request_id = source.get("request_id")
        projection_request_id = (
            source_request_id if type(source_request_id) is str else self.ids.new(IdKind.REQUEST)
        )
        provenance: ProjectionProvenanceContext | None = None
        if all(source.get(key) is not None for key in ("session_id", "writer_id")) and (
            source.get("subject_frontier") is not None or source.get("frontier") is not None
        ):
            provenance = ProjectionProvenanceContext(
                cast(str, source["session_id"]),
                cast(str, source["writer_id"]),
                _frontier_for_projection(source),
            )
        candidate = CandidateContext(
            request_id=projection_request_id,
            channel=None,
            local_sink=sink,
            purpose="client_result_projection",
            scope=scope,
            subject_digest=canonical_digest(source),
            provider_binding=None,
            items=tuple(items),
            provenance_context=(provenance if sink is LocalDisclosureSink.AGENT_CONTEXT else None),
            projection_audit_context=ProjectionAuditContext(
                binding.rpc_id,
                method.value,
                binding.service_instance_id,
                binding.service_generation,
                binding.original_request_id,
                binding.route_identity_digest,
                binding.control_request_canonical,
                canonical_encode(source),
            ),
        )
        decision = await self.privacy.prepare_local_disclosure(candidate)
        if type(decision) is LocalDisclosureUnavailable:
            raise ControlError("privacy_projection_unavailable", retryable=True)
        if type(decision) is LocalDisclosureApproved:
            completed = cast(LocalDisclosureApproved | LocalDisclosureBlocked, decision)
        elif type(decision) is LocalDisclosureBlocked:
            completed = cast(LocalDisclosureApproved | LocalDisclosureBlocked, decision)
        else:
            raise TypeError("local_disclosure_result_invalid")
        # Digest-bound JSON receipt documents cannot be partly rewritten with omission
        # markers; fail closed when any present document content leaf is blocked.
        if (
            method is ControlMethod.RECEIPT
            and source.get("format") == "json"
            and any(
                omission.json_pointer == "/document"
                or omission.json_pointer.startswith("/document/")
                for omission in completed.omissions
            )
        ):
            raise ControlError("privacy_projection_unavailable", retryable=True)
        projected: JsonValue = source
        for omission in completed.omissions:
            projected = _replace_pointer(
                projected,
                omission.json_pointer,
                {
                    "omitted": True,
                    "category": omission.category.value,
                    "reason": omission.reason,
                },
            )
        if not isinstance(projected, Mapping):
            raise TypeError("projected_control_body_invalid")
        receipt = completed.receipt
        projection = {
            "sink": completed.sink.value,
            "local_disclosure_receipt_id": receipt.receipt_id,
            "policy_id": receipt.policy.policy_id,
            "policy_version": str(receipt.policy.version),
            "policy_digest": receipt.policy.policy_digest,
            "included_categories": tuple(item.value for item in receipt.approved_categories),
            "blocked_categories": tuple(item.value for item in receipt.blocked_categories),
            "omitted_pointers": tuple(item.json_pointer for item in completed.omissions),
            "projection_commitment": completed.case_or_projection_commitment,
        }
        complete = {**dict(projected.items()), "privacy_projection": projection}
        if method is ControlMethod.REVIEW:
            nested = complete.get("check_result")
            if not isinstance(nested, Mapping):
                raise TypeError("review_projection_shape_invalid")
            nested_complete = {**dict(nested.items()), "privacy_projection": projection}
            complete["check_result"] = nested_complete
            if canonical_encode(complete["privacy_projection"]) != canonical_encode(
                nested_complete["privacy_projection"]
            ):
                raise AssertionError("review_projection_identity_invalid")
        return _public_model(method, complete)

    async def close(self) -> None:
        async with self._close_lock:
            if self._close_task is None:
                object.__setattr__(self, "_close_task", asyncio.create_task(self._close_once()))
            task = self._close_task
        assert task is not None
        await task

    async def _close_once(self) -> None:
        failure: BaseException | None = None
        try:
            if self.verification_supervisor is not None:
                await self.verification_supervisor.stop()
        except BaseException as exc:
            failure = exc
        try:
            await self.privacy.close()
        except BaseException as exc:
            if failure is None:
                failure = exc
        try:
            await self.runtime.close()
        except BaseException as exc:
            if failure is None:
                failure = exc
        if failure is not None:
            raise failure


type _ReadyContextProvider = Callable[[int, int], Awaitable["ServiceReadyContext"]]


@dataclass(frozen=True, slots=True, repr=False)
class ServiceReadyContext:
    """Validated, generation-bound dependencies for one fresh ready application."""

    service_generation: int
    vault_generation: int
    generation_is_current: Callable[[int, int], bool]
    start_catalog: StartCatalogPort
    runtime: BundleRuntimePort
    clock: ClockPort
    ids: IdPort
    verification_policy: VerificationPolicy
    privacy: PrivacyCoordinator
    status_cursor_key: bytes
    waiver_policy_digest: str
    semantic_evaluator: _SemanticEvaluator
    disclosure_scope_for: _ScopeResolver
    receipt_version_resolver: _ReceiptVersions
    waiver_authorizer: Callable[[RespondRequest], bool]
    import_publication_authorizer: Callable[[object], bool]
    profile: RuntimeProfile
    policy_packs: tuple[str, ...]
    version_manifest: Mapping[str, JsonValue]
    support_handlers: Mapping[ControlMethod, _SupportHandler] = field(
        default_factory=_empty_support_handlers
    )
    verification_supervisor: ObservationVerificationSupervisor | None = None
    rediscover_pending_verification: Callable[[], Awaitable[None]] | None = None

    def __post_init__(self) -> None:
        if (
            type(self.service_generation) is not int
            or type(self.vault_generation) is not int
            or self.service_generation <= 0
            or self.vault_generation <= 0
        ):
            raise ValueError("ready_generation_invalid")

    def __repr__(self) -> str:
        return "ServiceReadyContext(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class ReadyApplicationFactory:
    """Open one fresh application behind the daemon's exact generation fence."""

    context_provider: _ReadyContextProvider

    async def __call__(self, service_generation: int, vault_generation: int) -> Application:
        context = await self.context_provider(service_generation, vault_generation)
        if (
            type(context) is not ServiceReadyContext
            or context.service_generation != service_generation
            or context.vault_generation != vault_generation
        ):
            await _close_ready_context(context)
            raise ControlError("service_generation_changed", retryable=True)
        return await self.open(context)

    async def open(self, context: ServiceReadyContext) -> Application:
        if type(context) is not ServiceReadyContext:
            raise TypeError("service_ready_context_invalid")
        if not context.generation_is_current(context.service_generation, context.vault_generation):
            await _close_ready_context(context)
            raise ControlError("service_generation_changed", retryable=True)
        try:
            application = Application(
                context.start_catalog,
                context.runtime,
                context.clock,
                context.ids,
                context.verification_policy,
                context.privacy,
                context.status_cursor_key,
                context.waiver_policy_digest,
                context.semantic_evaluator,
                context.disclosure_scope_for,
                context.receipt_version_resolver,
                context.waiver_authorizer,
                context.import_publication_authorizer,
                context.profile,
                context.policy_packs,
                context.version_manifest,
                context.support_handlers,
                context.verification_supervisor,
            )
            if context.verification_supervisor is not None:
                await context.verification_supervisor.start()
            if context.rediscover_pending_verification is not None:
                await context.rediscover_pending_verification()
            return application
        except BaseException:
            await _close_ready_context(context)
            raise


async def _close_ready_context(context: object) -> None:
    if not isinstance(context, ServiceReadyContext):
        return
    try:
        if context.verification_supervisor is not None:
            await context.verification_supervisor.stop()
    finally:
        try:
            await context.privacy.close()
        finally:
            await context.runtime.close()
