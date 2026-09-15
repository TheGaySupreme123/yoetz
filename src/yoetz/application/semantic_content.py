"""Resolve bounded native evidence through task-owned ports before pure case building.

Resolution authorizes local reading only. The existing privacy coordinator independently
selects and authorizes outbound items. No missing object is replaced with its description.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from yoetz.application.observation_materialize import (
    MATERIALIZATION_MAPPING_VERSION,
    stable_observation_id,
)
from yoetz.domain.events import EvidenceDigestProvenance
from yoetz.domain.observation import (
    ObservationContentKind,
    ObservationContentManifest,
    ObservationSource,
)
from yoetz.domain.privacy import ReviewSelectionPolicy
from yoetz.domain.receipts import SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP
from yoetz.domain.values import Timestamp
from yoetz.kernel.deterministic_checks import DeterministicCase
from yoetz.ports.objects import ObjectKind
from yoetz.ports.runtime import TaskRuntime
from yoetz.ports.semantic import ReviewOmissionReason
from yoetz.protocol.canonical import canonical_encode, strict_json_parse
from yoetz.protocol.ids import IdKind

_NATIVE_KINDS = frozenset(
    {
        ObservationContentKind.TOOL_OUTPUT,
        ObservationContentKind.CHANGED_FILE,
        ObservationContentKind.WORKSPACE_DIFF,
    }
)
_MAX_NATIVE_EXCERPT_BYTES = 4096
_MAX_OBJECT_BYTES = _MAX_NATIVE_EXCERPT_BYTES * 2 + 2048
_MAX_CANDIDATES = 64


@dataclass(frozen=True, slots=True)
class ResolvedSemanticContent:
    content: bytes | None
    omission: ReviewOmissionReason | None = None
    gap: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticContentResolution:
    frontier: str
    items: Mapping[str, ResolvedSemanticContent]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", MappingProxyType(dict(self.items)))


def _frontier(case: DeterministicCase) -> str:
    return f"{case.frontier.sequence}:{case.frontier.head_digest}"


async def _read_manifest(
    runtime: TaskRuntime, manifest: ObservationContentManifest
) -> bytes | None:
    if manifest.envelope_digest is None:
        return None
    obj = await runtime.objects.resolve_verified(manifest.object_id, manifest.envelope_digest)
    if (
        obj.metadata.task_id != runtime.task_id
        or obj.metadata.kind is not ObjectKind.CAPTURED_CONTENT
        or obj.metadata.media_type != "application/vnd.yoetz.observation-content+json"
        or obj.plaintext_size > _MAX_OBJECT_BYTES
    ):
        return None
    raw = bytearray()
    async for chunk in runtime.objects.open_verified(obj):
        if len(raw) + len(chunk) > min(obj.plaintext_size, _MAX_OBJECT_BYTES):
            return None
        raw.extend(chunk)
    if len(raw) != obj.plaintext_size:
        return None
    parsed = strict_json_parse(bytes(raw))
    if (
        not isinstance(parsed, Mapping)
        or canonical_encode(parsed) != bytes(raw)
        or set(parsed)
        != {
            "format",
            "content_kind",
            "correlation_identity",
            "source_commitment",
            "media_type",
            "part_index",
            "part_count",
            "redacted",
            "content_b64",
        }
        or parsed.get("format") != "yoetz.observation-content/1"
        or parsed.get("media_type") != "text/plain"
        or type(parsed.get("content_b64")) is not str
    ):
        return None
    content = base64.b64decode(cast(str, parsed["content_b64"]), validate=True)
    rebound = ObservationContentManifest(
        object_id=obj.object_id,
        envelope_digest=obj.envelope_digest,
        content_kind=ObservationContentKind(cast(str, parsed["content_kind"])),
        part_index=cast(int, parsed["part_index"]),
        part_count=cast(int, parsed["part_count"]),
        redacted=cast(bool, parsed["redacted"]),
        content_digest="sha256:" + hashlib.sha256(content).hexdigest(),
        content_bytes=len(content),
        correlation_identity=cast(str, parsed["correlation_identity"]),
        source_commitment=cast(str, parsed["source_commitment"]),
    )
    if rebound != manifest:
        return None
    content.decode("utf-8", errors="strict")
    return content


async def resolve_semantic_content(
    *,
    frozen_case: DeterministicCase,
    runtime: TaskRuntime,
    workspace: str,
    authorized: bool,
    review_selection: ReviewSelectionPolicy,
    authorized_since: Timestamp | None = None,
    selected_refs: frozenset[str] | None = None,
) -> SemanticContentResolution:
    """Read at most 64 selected objects from at most 256 recent native envelopes.

    Older material outside that bounded source window remains unavailable. Each object is
    authenticated and bound to the frozen evidence identity, not merely to a matching digest.
    The grant timestamp prevents revocation/regrant from reviving an older captured source.
    """
    results: dict[str, ResolvedSemanticContent] = {}
    candidates = [
        (str(key), record)
        for key, record in sorted(frozen_case.projection.evidence.items())
        if key in frozen_case.allowed_ids
        and record.payload is not None
        and record.payload.captured_object_id is not None
        and record.payload.digest_binding is not None
        and record.payload.digest_binding.provenance
        is EvidenceDigestProvenance.OBSERVATION_CAPTURED
        and (selected_refs is None or str(key) in selected_refs)
    ]
    unavailable = {item.object_id for item in frozen_case.availability.unavailable_captured_objects}
    store = runtime.observation
    envelopes = ()
    if authorized and store is not None and "targeted_excerpts" in review_selection.sections:
        envelopes = store.list_envelopes(workspace, limit=256)
    used = 0
    count = 0
    authenticated: dict[str, bytes | None] = {}
    for index, (ref, record) in enumerate(candidates):
        payload = record.payload
        assert payload is not None and payload.captured_object_id is not None
        result = ResolvedSemanticContent(None, "not_recorded", "captured_object_unavailable")
        results[ref] = result
        if not authorized:
            results[ref] = ResolvedSemanticContent(None, "withheld_by_policy", "content_unselected")
            continue
        if (
            index >= _MAX_CANDIDATES
            or count >= review_selection.max_excerpts
            or "targeted_excerpts" not in review_selection.sections
        ):
            results[ref] = ResolvedSemanticContent(None, "not_selected", "content_unselected")
            continue
        if record.redacted or not record.object_available:
            results[ref] = ResolvedSemanticContent(None, "redacted_never_send", "content_redacted")
            continue
        binding = payload.digest_binding
        if (
            store is None
            or payload.captured_object_id in unavailable
            or binding is None
            or binding.provenance is not EvidenceDigestProvenance.OBSERVATION_CAPTURED
        ):
            continue
        try:
            manifest = store.load_content_manifest(payload.captured_object_id)
            if manifest is None:
                continue
            if manifest.content_kind not in _NATIVE_KINDS:
                results[ref] = ResolvedSemanticContent(None, "not_selected", "content_unselected")
                continue
            if manifest.redacted:
                results[ref] = ResolvedSemanticContent(
                    None, "redacted_never_send", "content_redacted"
                )
                continue
            if (
                manifest.content_bytes is None
                or manifest.content_digest != payload.content_digest
                or manifest.content_bytes != binding.byte_count
                or manifest.envelope_digest is None
            ):
                continue
            if manifest.content_bytes > min(
                review_selection.max_excerpt_bytes, _MAX_NATIVE_EXCERPT_BYTES
            ):
                results[ref] = ResolvedSemanticContent(
                    None, "not_selected", SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP
                )
                continue
            if used + manifest.content_bytes > review_selection.max_total_excerpt_bytes:
                results[ref] = ResolvedSemanticContent(None, "not_selected", "content_unselected")
                continue
            matched = False
            group: tuple[ObservationContentManifest, ...] = ()
            for envelope in envelopes:
                if (
                    envelope.source is not ObservationSource.CODEX_HOOK
                    or payload.captured_object_id not in envelope.content_object_refs
                    or (
                        authorized_since is not None
                        and envelope.receipt_time.wire < authorized_since.wire
                    )
                    or manifest.source_commitment != envelope.cursor.last_source_commitment
                    or manifest.correlation_identity is None
                    or not manifest.correlation_identity.startswith(envelope.source_identity + ":")
                ):
                    continue
                expected_id = stable_observation_id(
                    kind=IdKind.EVIDENCE,
                    task_id=runtime.task_id,
                    source_identity=f"{envelope.source_identity}:captured:{manifest.object_id}",
                    mapping_version=MATERIALIZATION_MAPPING_VERSION,
                    role="captured_evidence",
                )
                if expected_id != ref:
                    continue
                group = tuple(
                    item
                    for oid in envelope.content_object_refs
                    if (item := store.load_content_manifest(oid)) is not None
                    and item.correlation_identity == manifest.correlation_identity
                    and item.content_kind is manifest.content_kind
                )
                if (
                    len(group) != manifest.part_count
                    or {item.part_index for item in group} != set(range(manifest.part_count))
                    or any(
                        item.part_count != manifest.part_count
                        or item.source_commitment != manifest.source_commitment
                        or item.envelope_digest is None
                        or item.redacted
                        for item in group
                    )
                ):
                    continue
                matched = True
                break
            if not matched:
                continue
            if any(
                item.content_bytes is None
                or item.content_digest is None
                or item.content_bytes
                > min(review_selection.max_excerpt_bytes, _MAX_NATIVE_EXCERPT_BYTES)
                for item in group
            ):
                results[ref] = ResolvedSemanticContent(
                    None, "not_selected", SEMANTIC_CASE_CONTENT_OVER_ITEM_LIMIT_GAP
                )
                continue
            group_valid = True
            for part in group:
                if part.object_id not in authenticated:
                    if len(authenticated) >= _MAX_CANDIDATES:
                        group_valid = False
                        break
                    authenticated[part.object_id] = None
                    authenticated[part.object_id] = await _read_manifest(runtime, part)
                if authenticated[part.object_id] is None:
                    group_valid = False
            if not group_valid:
                continue
            content = authenticated[manifest.object_id]
            assert content is not None
            results[ref] = ResolvedSemanticContent(content)
            used += len(content)
            count += 1
        except Exception:
            # Object loss, authentication failure and corrupt metadata are per-item omissions.
            continue
    return SemanticContentResolution(_frontier(frozen_case), results)
