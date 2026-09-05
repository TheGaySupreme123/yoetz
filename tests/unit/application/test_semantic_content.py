"""Adversarial service-boundary tests for retained ordinary observation content."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from builders.policy_cases import evd, make_case
from yoetz.application.observation_materialize import (
    MATERIALIZATION_MAPPING_VERSION,
    stable_observation_id,
)
from yoetz.application.semantic_case import (
    build_semantic_case,
    semantic_case_to_prepared_payload,
)
from yoetz.application.semantic_content import resolve_captured_semantic_content
from yoetz.domain.events import (
    EvidenceContentAvailability,
    EvidenceDigestBinding,
    EvidenceDigestProvenance,
    EvidenceDigestSubject,
    EvidenceImmutability,
    EvidenceKind,
    EvidenceRecordedPayload,
    encode_payload,
)
from yoetz.domain.observation import (
    ObservationContentKind,
    ObservationContentManifest,
    ObservationCursor,
    ObservationEnvelope,
    ObservationSource,
)
from yoetz.domain.privacy import ReviewContextProfile, ReviewSelectionPolicy
from yoetz.domain.values import JsonObject, event_id, object_id, timestamp_from_string
from yoetz.kernel.projections import EvidenceProjectionRecord
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.importer import ImporterPort
from yoetz.ports.ledger import CheckPhase, FrozenCase, LedgerPort, OperationLease
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef, ObjectStorePort
from yoetz.ports.observation import TaskObservationPort
from yoetz.ports.runtime import OwnershipFence, TaskRuntime
from yoetz.protocol.canonical import canonical_digest, canonical_encode
from yoetz.protocol.ids import IdKind

_TASK = "tsk_10000000-0000-4000-8000-000000000001"
_SESSION = "ses_10000000-0000-4000-8000-000000000001"
_WRITER = "wri_10000000-0000-4000-8000-000000000001"
_REQUEST = "req_10000000-0000-4000-8000-000000000001"
_WORKSPACE = "hmac-sha256:" + "8" * 64
_SESSION_COMMITMENT = "hmac-sha256:" + "2" * 64
_SOURCE_COMMITMENT = "hmac-sha256:" + "3" * 64
_PROFILE = "claude-code-ordinary-observation-v1"


class _Objects:
    def __init__(self, ref: ObjectRef, wrapper: bytes) -> None:
        self.ref = replace(ref, plaintext_size=len(wrapper))
        self.wrapper = wrapper
        self.resolve_calls: list[tuple[str, str]] = []
        self.open_calls = 0

    async def resolve_verified(self, object_id: str, envelope_digest: str) -> ObjectRef:
        self.resolve_calls.append((object_id, envelope_digest))
        return self.ref

    async def open_verified(self, _ref: ObjectRef):
        self.open_calls += 1
        yield self.wrapper


class _Observation:
    def __init__(
        self,
        envelope: ObservationEnvelope,
        manifest: ObservationContentManifest,
        *,
        profiles: tuple[str, ...] = (_PROFILE,),
        lifecycle: str = "active",
    ) -> None:
        self.envelope = envelope
        self.manifest = manifest
        self.profiles = profiles
        self.lifecycle = lifecycle

    def content_capture_profiles(self, _workspace: str) -> tuple[str, ...]:
        return self.profiles

    def codex_session_commitment_for_session(
        self, *, workspace: str, yoetz_session_id: str
    ) -> str | None:
        if workspace != _WORKSPACE or yoetz_session_id != _SESSION:
            return None
        return _SESSION_COMMITMENT

    async def status_for_session(self, _workspace: str, _session_commitment: str) -> object:
        return SimpleNamespace(lifecycle=SimpleNamespace(value=self.lifecycle))

    def list_envelopes_for_session(
        self, _workspace: str, _session_commitment: str
    ) -> tuple[ObservationEnvelope, ...]:
        return (self.envelope,)

    def load_content_manifest(self, _object_id: str) -> ObservationContentManifest:
        return self.manifest


def _fixture(
    *,
    content: bytes = b"planted-defect-marker: missing validation",
    allowed: bool = True,
) -> tuple[FrozenCase, TaskRuntime, _Objects, _Observation, ObservationEnvelope]:
    object_value = object_id("obj_00000000-0000-4000-8000-000000000302")
    content_digest = "sha256:" + hashlib.sha256(content).hexdigest()
    envelope = ObservationEnvelope(
        session_commitment=_SESSION_COMMITMENT,
        event_kind="PostToolUse",
        source_identity="claude-phase-1",
        source=ObservationSource.CLAUDE_HOOK,
        cursor=ObservationCursor(
            source_generation=1,
            byte_position=0,
            event_position=1,
            last_source_commitment=_SOURCE_COMMITMENT,
            mapping_version=MATERIALIZATION_MAPPING_VERSION,
        ),
        receipt_time=timestamp_from_string("2026-07-01T00:00:00.000Z"),
        structural_payload=JsonObject({"tool_name": "Bash", "correlation_id": "tool-use-1"}),
        content_object_refs=(object_value,),
        gap_codes=(),
    )
    expected_event = stable_observation_id(
        kind=IdKind.EVENT,
        task_id=_TASK,
        source_identity=f"{envelope.source_identity}:captured:{object_value}",
        mapping_version=MATERIALIZATION_MAPPING_VERSION,
        role="captured_evidence_event",
    )
    payload = EvidenceRecordedPayload(
        evidence_id=evd(1),
        evidence_kind=EvidenceKind.OTHER,
        strength=EvidenceImmutability.IMMUTABLE_SNAPSHOT,
        observed_at=timestamp_from_string("2026-07-01T00:00:00.000Z"),
        captured_object_id=object_value,
        content_digest=content_digest,
        description="Observation-captured tool output bytes part=1/1",
        digest_binding=EvidenceDigestBinding(
            subject=EvidenceDigestSubject.BOUNDED_EXCERPT,
            content_availability=EvidenceContentAvailability.CAPTURED,
            byte_count=len(content),
            provenance=EvidenceDigestProvenance.OBSERVATION_CAPTURED,
        ),
    )
    evidence_record_value = EvidenceProjectionRecord(
        payload=payload,
        payload_digest=canonical_digest(encode_payload(payload)),
        redacted=False,
        source_event_id=event_id(expected_event),
        source_frontier=4,
    )
    case = make_case(
        evidence={evd(1): evidence_record_value},
        extra_refs=(evd(1),) if allowed else (),
    )
    if not allowed:
        # Keep the evidence projection durable while fencing its evidence ref
        # out of this frozen case's allowlist.
        case = replace(
            case,
            allowed_ids=frozenset({event_id(expected_event)}),
            coverage_by_ref={
                event_id(expected_event): case.coverage_by_ref[event_id(expected_event)]
            },
        )
    envelope_digest = "sha256:" + "5" * 64
    ref = ObjectRef(
        object_id=object_value,
        plaintext_size=1,
        commitment="hmac-sha256:" + "6" * 64,
        envelope_digest=envelope_digest,
        encryption_format="yoetz-object/1",
        key_slot="task",
        metadata=ObjectMetadata(
            ObjectKind.CAPTURED_CONTENT,
            "application/vnd.yoetz.observation-content+json",
            _TASK,
            datetime(2026, 7, 1, tzinfo=UTC),
        ),
    )
    manifest = ObservationContentManifest(
        object_id=object_value,
        envelope_digest=envelope_digest,
        content_kind=ObservationContentKind.TOOL_OUTPUT,
        part_index=0,
        part_count=1,
        redacted=False,
        content_digest=content_digest,
        content_bytes=len(content),
        correlation_identity="tool-use-1",
        source_commitment=_SOURCE_COMMITMENT,
    )
    wrapper = canonical_encode(
        JsonObject(
            {
                "format": "yoetz.observation-content/1",
                "content_kind": manifest.content_kind.value,
                "correlation_identity": cast(str, manifest.correlation_identity),
                "source_commitment": cast(str, manifest.source_commitment),
                "media_type": "text/plain",
                "part_index": manifest.part_index,
                "part_count": manifest.part_count,
                "redacted": manifest.redacted,
                "content_b64": base64.b64encode(content).decode("ascii"),
            }
        )
    )
    objects = _Objects(ref, wrapper)
    observation = _Observation(envelope, manifest)
    runtime = TaskRuntime(
        task_id=_TASK,
        session_id=_SESSION,
        writer_id=_WRITER,
        capabilities=frozenset({RuntimeCapability.WRITE}),
        ledger=cast(LedgerPort, object()),
        objects=cast(ObjectStorePort, objects),
        importer=cast(ImporterPort, object()),
        projection_version="0.1.0",
        engine_version="0.1.0",
        protocol_version="0.1",
        bundle_schema_version="1.0.0",
        fence=OwnershipFence("svc_10000000-0000-4000-8000-000000000001", 1, 1, "n" * 16),
        observation=cast(TaskObservationPort, observation),
    )
    lease = OperationLease(
        writer_id=_WRITER,
        operation_id=_REQUEST,
        session_id=_SESSION,
        phase=CheckPhase.SEMANTIC_WAIT,
        owner_generation="owner-1",
        lease_owner_id="svc-1",
        lease_generation=1,
        lease_expires_at=datetime(2026, 7, 1, 1, tzinfo=UTC),
        frontier=case.frontier,
        dependency_digest="sha256:" + "b" * 64,
    )
    return FrozenCase(case, lease), runtime, objects, observation, envelope


@pytest.mark.anyio
async def test_resolver_authenticates_content_and_binds_phase_before_builder() -> None:
    frozen, runtime, objects, _observation, _envelope = _fixture()
    resolved = await resolve_captured_semantic_content(
        runtime=runtime,
        frozen=frozen,
        workspace_commitment=_WORKSPACE,
    )

    assert len(objects.resolve_calls) == 1
    assert objects.open_calls == 1
    assert len(resolved.content) == 1
    captured = resolved.content[0]
    assert captured.content == b"planted-defect-marker: missing validation"
    assert resolved.scope is not None
    assert resolved.scope.phase_bindings == ((str(evd(1)), captured.phase_identity),)
    semantic = build_semantic_case(
        case_id="cas_10000000-0000-4000-8000-000000000001",
        frozen_case=frozen.case,
        dependency_digest=frozen.lease.dependency_digest,
        findings=(),
        review_context_profile=ReviewContextProfile.EXPANDED,
        review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED),
        policy_id="pvy_10000000-0000-4000-8000-000000000001",
        policy_version="1",
        captured_content=resolved.content,
        captured_content_scope=resolved.scope,
        captured_content_gaps=resolved.gaps,
    )
    prepared = semantic_case_to_prepared_payload(
        semantic,
        {item.item_id for item in semantic.items},
    )
    assert captured.content in prepared


@pytest.mark.anyio
async def test_resolver_does_not_open_irrelevant_or_out_of_scope_objects() -> None:
    frozen, runtime, objects, observation, envelope = _fixture(allowed=False)
    irrelevant = tuple(
        object_id(f"obj_00000000-0000-4000-8000-{index:012x}") for index in range(1, 16)
    )
    observation.envelope = replace(
        envelope,
        content_object_refs=tuple(sorted(irrelevant, key=str.encode)),
    )
    resolved = await resolve_captured_semantic_content(
        runtime=runtime,
        frozen=frozen,
        workspace_commitment=_WORKSPACE,
    )

    assert resolved.content == ()
    assert objects.resolve_calls == []
    assert objects.open_calls == 0


@pytest.mark.anyio
async def test_resolver_rejects_wrong_phase_without_reading_object() -> None:
    frozen, runtime, objects, observation, envelope = _fixture()
    observation.envelope = replace(envelope, source_identity="claude-phase-2")

    resolved = await resolve_captured_semantic_content(
        runtime=runtime,
        frozen=frozen,
        workspace_commitment=_WORKSPACE,
    )

    assert resolved.content == ()
    assert "content_unselected" in resolved.gaps
    assert objects.resolve_calls == []
    assert objects.open_calls == 0


@pytest.mark.anyio
async def test_resolver_rejects_unknown_profile_and_revoked_session_before_open() -> None:
    frozen, runtime, objects, observation, _envelope = _fixture()
    observation.profiles = ("invented-profile",)
    unknown = await resolve_captured_semantic_content(
        runtime=runtime,
        frozen=frozen,
        workspace_commitment=_WORKSPACE,
    )
    assert unknown.content == ()
    assert "content_capture_unavailable" in unknown.gaps
    assert objects.open_calls == 0

    observation.profiles = (_PROFILE,)
    observation.lifecycle = "stopped"
    revoked = await resolve_captured_semantic_content(
        runtime=runtime,
        frozen=frozen,
        workspace_commitment=_WORKSPACE,
    )
    assert revoked.content == ()
    assert "content_unselected" in revoked.gaps
    assert objects.open_calls == 0


@pytest.mark.anyio
async def test_resolver_rejects_forged_wrapper_and_prebounds_metadata_bytes() -> None:
    frozen, runtime, objects, observation, _envelope = _fixture()
    objects.wrapper = objects.wrapper.replace(b"cGxhbnRlZC", b"Zm9yZ2Vk")
    forged = await resolve_captured_semantic_content(
        runtime=runtime,
        frozen=frozen,
        workspace_commitment=_WORKSPACE,
    )
    assert forged.content == ()
    assert "content_capture_unavailable" in forged.gaps
    assert objects.open_calls == 1

    objects.open_calls = 0
    objects.wrapper = objects.wrapper
    observation.manifest = replace(observation.manifest, content_bytes=100)
    bounded = await resolve_captured_semantic_content(
        runtime=runtime,
        frozen=frozen,
        workspace_commitment=_WORKSPACE,
        max_total_bytes=1,
    )
    assert bounded.content == ()
    assert "content_unselected" in bounded.gaps
    assert objects.open_calls == 0


@pytest.mark.anyio
async def test_resolver_withholds_incomplete_multipart_group_before_open() -> None:
    frozen, runtime, objects, observation, _envelope = _fixture()
    observation.manifest = replace(observation.manifest, part_count=2)

    resolved = await resolve_captured_semantic_content(
        runtime=runtime,
        frozen=frozen,
        workspace_commitment=_WORKSPACE,
    )

    assert resolved.content == ()
    assert "content_capture_unavailable" in resolved.gaps
    assert objects.resolve_calls == []
    assert objects.open_calls == 0
