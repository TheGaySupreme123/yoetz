"""Authenticated object read failures stay bounded before semantic case construction."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from yoetz.application.semantic_content import _read_manifest  # pyright: ignore[reportPrivateUsage]
from yoetz.domain.observation import ObservationContentKind, ObservationContentManifest
from yoetz.ports.objects import ObjectKind, ObjectMetadata, ObjectRef
from yoetz.ports.runtime import TaskRuntime
from yoetz.protocol.canonical import JsonValue, canonical_encode

pytestmark = pytest.mark.anyio
_TASK = "tsk_00000000-0000-4000-8000-000000000001"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Objects:
    def __init__(self, payload: bytes, metadata: ObjectMetadata) -> None:
        self.payload = payload
        self.opened = 0
        self.ref = ObjectRef(
            "obj_00000000-0000-4000-8000-000000000001",
            len(payload),
            "hmac-sha256:" + "1" * 64,
            "sha256:" + "2" * 64,
            "yoetz-object/1",
            "key-slot-1",
            metadata,
        )

    async def resolve_verified(self, object_id: str, envelope_digest: str) -> ObjectRef:
        assert object_id == self.ref.object_id and envelope_digest == self.ref.envelope_digest
        return self.ref

    async def open_verified(self, _ref: ObjectRef) -> AsyncIterator[bytes]:
        self.opened += 1
        yield self.payload


def _fixture(**changes: JsonValue) -> tuple[TaskRuntime, ObservationContentManifest, _Objects]:
    content = b"synthetic source: return left - right"
    body: dict[str, JsonValue] = {
        "format": "yoetz.observation-content/1",
        "content_kind": "tool_output",
        "correlation_identity": "hook:source:tool-output",
        "source_commitment": "hmac-sha256:" + "3" * 64,
        "media_type": "text/plain",
        "part_index": 0,
        "part_count": 1,
        "redacted": False,
        "content_b64": base64.b64encode(content).decode("ascii"),
    }
    body.update(changes)
    store = _Objects(
        canonical_encode(body),
        ObjectMetadata(
            ObjectKind.CAPTURED_CONTENT,
            "application/vnd.yoetz.observation-content+json",
            _TASK,
            datetime(2026, 9, 15, tzinfo=UTC),
        ),
    )
    manifest = ObservationContentManifest(
        object_id=store.ref.object_id,
        envelope_digest=store.ref.envelope_digest,
        content_kind=ObservationContentKind.TOOL_OUTPUT,
        part_index=0,
        part_count=1,
        redacted=False,
        content_digest="sha256:" + hashlib.sha256(content).hexdigest(),
        content_bytes=len(content),
        correlation_identity="hook:source:tool-output",
        source_commitment="hmac-sha256:" + "3" * 64,
    )
    return cast(TaskRuntime, SimpleNamespace(task_id=_TASK, objects=store)), manifest, store


async def test_authenticated_exact_content_is_readable() -> None:
    runtime, manifest, store = _fixture()
    assert await _read_manifest(runtime, manifest) == b"synthetic source: return left - right"
    assert store.opened == 1


@pytest.mark.parametrize(
    "change",
    [
        {"content_b64": base64.b64encode(b"substituted bytes").decode("ascii")},
        {"correlation_identity": "hook:other:tool-output"},
        {"source_commitment": "hmac-sha256:" + "4" * 64},
        {"part_count": 2},
        {"redacted": True},
        {"content_kind": "tool_input"},
        {"media_type": "application/json"},
        {"extra": "unbound"},
    ],
)
async def test_manifest_substitution_is_rejected(change: dict[str, JsonValue]) -> None:
    runtime, manifest, _ = _fixture(**change)
    assert await _read_manifest(runtime, manifest) is None


async def test_wrong_task_is_rejected_before_open() -> None:
    runtime, manifest, store = _fixture()
    store.ref = replace(
        store.ref,
        metadata=replace(
            store.ref.metadata,
            task_id="tsk_00000000-0000-4000-8000-000000000002",
        ),
    )
    assert await _read_manifest(runtime, manifest) is None
    assert store.opened == 0


async def test_oversized_declared_object_is_rejected_before_open() -> None:
    runtime, manifest, store = _fixture()
    store.ref = replace(store.ref, plaintext_size=100_000)
    assert await _read_manifest(runtime, manifest) is None
    assert store.opened == 0


async def test_oversized_stream_is_rejected_without_truncation() -> None:
    runtime, manifest, store = _fixture()
    store.payload += b"x" * 100_000
    assert await _read_manifest(runtime, manifest) is None


async def test_noncanonical_wrapper_is_rejected() -> None:
    runtime, manifest, store = _fixture()
    store.payload += b" "
    store.ref = replace(store.ref, plaintext_size=len(store.payload))
    assert await _read_manifest(runtime, manifest) is None


async def test_multipart_authentication_shares_one_global_object_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from builders.policy_cases import evidence_record, make_case
    from yoetz.application import semantic_content
    from yoetz.application.observation_materialize import (
        MATERIALIZATION_MAPPING_VERSION,
        stable_observation_id,
    )
    from yoetz.domain.events import (
        EvidenceContentAvailability,
        EvidenceDigestBinding,
        EvidenceDigestProvenance,
        EvidenceDigestSubject,
        EvidenceKind,
        EvidenceRecordedPayload,
    )
    from yoetz.domain.observation import ObservationCursor, ObservationEnvelope, ObservationSource
    from yoetz.domain.privacy import ReviewContextProfile, ReviewSelectionPolicy
    from yoetz.domain.values import (
        EvidenceId,
        JsonObject,
        evidence_id,
        object_id,
        timestamp_from_string,
    )
    from yoetz.kernel.projections import EvidenceProjectionRecord
    from yoetz.protocol.coverage import EvidenceImmutability
    from yoetz.protocol.ids import IdKind

    stamp = timestamp_from_string("2026-09-15T00:00:00.000Z")
    digest = "sha256:" + hashlib.sha256(b"safe").hexdigest()
    source = "hmac-sha256:" + "a" * 64
    manifests: dict[str, ObservationContentManifest] = {}
    envelopes: list[ObservationEnvelope] = []
    evidence: dict[EvidenceId, EvidenceProjectionRecord] = {}
    for group in range(5):
        identity = f"hook:test-{group}"
        refs: list[str] = []
        for part in range(16):
            oid = f"obj_00000000-0000-4000-8000-{group * 16 + part + 1:012d}"
            refs.append(oid)
            manifests[oid] = ObservationContentManifest(
                oid,
                "sha256:" + "b" * 64,
                ObservationContentKind.TOOL_OUTPUT,
                part,
                16,
                False,
                digest,
                4,
                identity + ":tool-output",
                source,
            )
        envelope = ObservationEnvelope(
            source,
            "PostToolUse",
            identity,
            ObservationSource.CODEX_HOOK,
            ObservationCursor(1, 0, group + 1, source, "codex-obs-hook/1.0.0"),
            stamp,
            JsonObject({}),
            tuple(refs),
            (),
        )
        envelopes.append(envelope)
        eid = evidence_id(
            stable_observation_id(
                kind=IdKind.EVIDENCE,
                task_id=_TASK,
                source_identity=f"{identity}:captured:{refs[0]}",
                mapping_version=MATERIALIZATION_MAPPING_VERSION,
                role="captured_evidence",
            )
        )
        evidence[eid] = evidence_record(
            EvidenceRecordedPayload(
                eid,
                EvidenceKind.OTHER,
                EvidenceImmutability.IMMUTABLE_SNAPSHOT,
                stamp,
                captured_object_id=object_id(refs[0]),
                content_digest=digest,
                digest_binding=EvidenceDigestBinding(
                    EvidenceDigestSubject.BOUNDED_EXCERPT,
                    EvidenceContentAvailability.CAPTURED,
                    4,
                    EvidenceDigestProvenance.OBSERVATION_CAPTURED,
                ),
            ),
            group + 1,
        )
    reads: list[str] = []

    async def read(_runtime: TaskRuntime, manifest: ObservationContentManifest) -> bytes:
        reads.append(manifest.object_id)
        return b"safe"

    def window(_workspace: str, *, limit: int) -> tuple[ObservationEnvelope, ...]:
        assert limit == 256
        return tuple(envelopes)

    monkeypatch.setattr(semantic_content, "_read_manifest", read)
    runtime = cast(
        TaskRuntime,
        SimpleNamespace(
            task_id=_TASK,
            observation=SimpleNamespace(list_envelopes=window, load_content_manifest=manifests.get),
        ),
    )
    case = make_case(evidence=evidence, extra_refs=tuple(evidence))
    resolved = await semantic_content.resolve_semantic_content(
        frozen_case=case,
        runtime=runtime,
        workspace=source,
        authorized=True,
        review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED),
    )
    assert len(reads) == len(set(reads)) == 64
    assert sum(row.content is not None for row in resolved.items.values()) == 4
    assert sum(row.omission == "not_recorded" for row in resolved.items.values()) == 1
