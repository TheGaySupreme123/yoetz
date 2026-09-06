"""Service-side authentication and selection of retained observation content.

The semantic case builder stays pure. This module is the narrow application boundary that reads
the current observation consent, resolves only manifests referenced by current envelopes, verifies
the encrypted object and canonical inner wrapper, and hands bounded frozen values to the builder.
No structural event or outbox row is modified here, and no provider is contacted.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Final, cast

from yoetz.application.observation_materialize import (
    MATERIALIZATION_MAPPING_VERSION,
    observation_content_identity,
    stable_observation_id,
)
from yoetz.application.semantic_case import (
    MAX_CAPTURED_SEMANTIC_CONTENT_BYTES,
    CapturedContentScope,
    CapturedSemanticContent,
)
from yoetz.domain.events import (
    EvidenceContentAvailability,
    EvidenceDigestBinding,
    EvidenceDigestProvenance,
    EvidenceDigestSubject,
    EvidenceImmutability,
    EvidenceKind,
    EvidenceRecordedPayload,
)
from yoetz.domain.observation import (
    ObservationContentChunk,
    ObservationContentKind,
    ObservationContentManifest,
    ObservationEnvelope,
    ObservationGapCode,
    ObservationSource,
)
from yoetz.domain.observation_profiles import (
    CLAUDE_CODE_ORDINARY_HOOK_MAPPING_VERSION,
    CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID,
    CURSOR_ORDINARY_HOOK_MAPPING_VERSION,
    CURSOR_ORDINARY_OBSERVATION_PROFILE_ID,
)
from yoetz.domain.values import validate_sha256_digest
from yoetz.kernel.projections import EvidenceProjectionRecord
from yoetz.ports.ledger import FrozenCase
from yoetz.ports.objects import ObjectKind, ObjectRef
from yoetz.ports.runtime import TaskRuntime
from yoetz.protocol.canonical import JsonValue, canonical_encode, strict_json_parse
from yoetz.protocol.ids import IdKind

__all__ = [
    "CapturedContentResolution",
    "resolve_captured_semantic_content",
]

_CAPTURED_CONTENT_MEDIA_TYPE: Final = "application/vnd.yoetz.observation-content+json"
_CAPTURED_CONTENT_INNER_MEDIA_TYPE: Final = "text/plain"
_MAX_WRAPPER_BYTES: Final = 1_048_576
_CLAUDE_ORDINARY_PROFILE: Final = CLAUDE_CODE_ORDINARY_OBSERVATION_PROFILE_ID
_CURSOR_ORDINARY_PROFILE: Final = CURSOR_ORDINARY_OBSERVATION_PROFILE_ID
_AUTHORIZED_CAPTURE_PROFILES: Final = frozenset(
    {_CLAUDE_ORDINARY_PROFILE, _CURSOR_ORDINARY_PROFILE}
)
_MAX_CAPTURED_SEMANTIC_PARTS: Final = 64
_MAX_CAPTURED_SEMANTIC_INPUT_BYTES: Final = 2 * MAX_CAPTURED_SEMANTIC_CONTENT_BYTES
_CONTENT_GAPS: Final = frozenset(
    {
        ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value,
        ObservationGapCode.CONTENT_REDACTED.value,
        ObservationGapCode.CONTENT_UNSELECTED.value,
        ObservationGapCode.TRUNCATED_PAYLOAD.value,
    }
)
_CORRELATION_KEYS: Final = (
    "tool_use_id",
    "tool_call_id",
    "correlation_id",
    "parent_tool_call_id",
)


@dataclass(frozen=True, slots=True)
class _LocalCaptureFence:
    """Validated, plaintext-free snapshot of the authoritative local consent arm."""

    generation: str
    active: bool
    revoked: bool
    runtime_enabled: bool
    profiles: tuple[str, ...]


def _local_capture_fence(
    local_observation: object | None,
    workspace: str,
) -> tuple[_LocalCaptureFence | None, set[str], bool]:
    """Read the local consent fence, distinguishing missing from no local seam.

    ``TaskRuntime.observation`` is the mapped task bundle and can lag the owner-private local
    store while a pause, disable, or revoke is being propagated. Production passes the local
    store explicitly; the third return value preserves compatibility for pure/fake callers that
    have no local adapter at all.
    """

    if local_observation is None:
        return None, set(), False
    reader = getattr(local_observation, "content_capture_authority", None)
    if not callable(reader):
        return None, {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}, True
    try:
        raw = reader(workspace)
    except Exception:
        return None, {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}, True
    if raw is None:
        return None, {ObservationGapCode.CONSENT_MISSING.value}, True
    authority_workspace = getattr(raw, "workspace_commitment", None)
    generation = getattr(raw, "generation", None)
    active = getattr(raw, "active", None)
    revoked = getattr(raw, "revoked", False)
    runtime_enabled = getattr(raw, "runtime_enabled", None)
    raw_profiles = getattr(raw, "profiles", None)
    if (
        type(authority_workspace) is not str
        or authority_workspace != workspace
        or type(generation) is not str
        or type(active) is not bool
        or type(revoked) is not bool
        or type(runtime_enabled) is not bool
        or type(raw_profiles) is not tuple
    ):
        return None, {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}, True
    profiles = cast(tuple[object, ...], raw_profiles)
    if len(profiles) > 2 or any(type(profile) is not str for profile in profiles):
        return None, {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}, True
    profile_values = tuple(cast(str, profile) for profile in profiles)
    if (
        any(profile not in _AUTHORIZED_CAPTURE_PROFILES for profile in profile_values)
        or tuple(sorted(set(profile_values), key=str.encode)) != profile_values
    ):
        return None, {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}, True
    try:
        generation = validate_sha256_digest(generation)
    except TypeError, ValueError:
        return None, {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}, True
    return (
        _LocalCaptureFence(
            generation,
            active,
            revoked,
            runtime_enabled,
            profile_values,
        ),
        set(),
        True,
    )


def _local_capture_fence_current(
    local_observation: object | None,
    workspace: str,
    fence: _LocalCaptureFence,
) -> bool:
    """Check the local fence at a linearization point before opening/dispatching."""

    if local_observation is None:
        return True
    checker = getattr(local_observation, "content_capture_authority_is_current", None)
    if callable(checker):
        try:
            return checker(workspace, fence.generation, fence.profiles) is True
        except Exception:
            return False
    current, _gaps, _provided = _local_capture_fence(local_observation, workspace)
    return current is not None and current == fence and current.active


type _CandidateRow = tuple[str, EvidenceRecordedPayload, EvidenceProjectionRecord]
type _PendingRow = tuple[
    ObservationEnvelope,
    str,
    str,
    str,
    str,
    frozenset[str],
    EvidenceRecordedPayload,
]
type _MetadataRow = tuple[
    ObservationEnvelope,
    str,
    str,
    str,
    str,
    frozenset[str],
    EvidenceRecordedPayload,
    ObservationContentManifest,
]


@dataclass(frozen=True, slots=True)
class CapturedContentResolution:
    """Frozen result of one current-consent observation-content read."""

    scope: CapturedContentScope | None
    content: tuple[CapturedSemanticContent, ...]
    gaps: tuple[str, ...]
    # A local-store generation is carried back to the dispatch seam. The semantic
    # case may contain authenticated bytes only while this same generation is
    # still active; a pause/disable/revoke therefore invalidates the case before
    # another object open or provider call.
    local_fence_generation: str | None = None
    local_fence_profiles: tuple[str, ...] = ()
    local_fence_required: bool = False

    def __post_init__(self) -> None:
        if self.scope is not None and type(self.scope) is not CapturedContentScope:
            raise ValueError("semantic_content_resolution_invalid")
        if type(self.content) is not tuple or len(self.content) > _MAX_CAPTURED_SEMANTIC_PARTS:
            raise ValueError("semantic_content_resolution_invalid")
        if any(type(item) is not CapturedSemanticContent for item in self.content):
            raise ValueError("semantic_content_resolution_invalid")
        if type(self.gaps) is not tuple or len(self.gaps) > 16:
            raise ValueError("semantic_content_resolution_invalid")
        if any(type(gap) is not str or not gap for gap in self.gaps):
            raise ValueError("semantic_content_resolution_invalid")
        if self.gaps != tuple(sorted(set(self.gaps), key=str.encode)):
            raise ValueError("semantic_content_resolution_invalid")
        if self.local_fence_generation is not None and (
            type(self.local_fence_generation) is not str
        ):
            raise ValueError("semantic_content_resolution_invalid")
        if self.local_fence_generation is not None:
            try:
                validate_sha256_digest(self.local_fence_generation)
            except TypeError, ValueError:
                raise ValueError("semantic_content_resolution_invalid") from None
        if (
            type(self.local_fence_profiles) is not tuple
            or len(self.local_fence_profiles) > 2
            or any(
                profile not in _AUTHORIZED_CAPTURE_PROFILES for profile in self.local_fence_profiles
            )
            or tuple(sorted(set(self.local_fence_profiles), key=str.encode))
            != self.local_fence_profiles
        ):
            raise ValueError("semantic_content_resolution_invalid")
        if type(self.local_fence_required) is not bool:
            raise ValueError("semantic_content_resolution_invalid")


def _profile_for_envelope(envelope: ObservationEnvelope) -> str | None:
    """Return a profile only for the exact ordinary renderer identity.

    Source enum alone is insufficient: legacy/native hook envelopes use the same
    source while carrying different structural vocabularies and must never gain
    the ordinary captured-content arm by inference.
    """

    structural = envelope.structural_payload
    profile = structural.get("capability_profile_id")
    mapping_hint = structural.get("mapping_hint")
    if envelope.source is ObservationSource.CLAUDE_HOOK:
        if (
            profile == _CLAUDE_ORDINARY_PROFILE
            and mapping_hint == CLAUDE_CODE_ORDINARY_HOOK_MAPPING_VERSION
        ):
            return _CLAUDE_ORDINARY_PROFILE
    elif envelope.source is ObservationSource.CURSOR_HOOK:
        if (
            profile == _CURSOR_ORDINARY_PROFILE
            and mapping_hint == CURSOR_ORDINARY_HOOK_MAPPING_VERSION
        ):
            return _CURSOR_ORDINARY_PROFILE
    return None


def _consent_profiles(observation: object, workspace: str) -> tuple[tuple[str, ...], set[str]]:
    """Read and validate the current closed content-consent vocabulary.

    New stores expose the plural profile accessor.  The consent-object fallback
    keeps older in-process stores useful, but both paths reject unknown profile
    strings before any object can be opened.
    """

    profiles_reader = cast(
        Callable[[str], object] | None,
        getattr(observation, "content_capture_profiles", None),
    )
    if callable(profiles_reader):
        try:
            raw_profiles = profiles_reader(workspace)
        except Exception:
            return (), {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}
        if type(raw_profiles) is not tuple:
            return (), {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}
        profiles = cast(tuple[object, ...], raw_profiles)
        if any(type(profile) is not str for profile in profiles):
            return (), {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}
        profile_values = tuple(cast(str, profile) for profile in profiles)
        if (
            len(profile_values) > 2
            or any(profile not in _AUTHORIZED_CAPTURE_PROFILES for profile in profile_values)
            or tuple(sorted(set(profile_values), key=str.encode)) != profile_values
        ):
            return (), {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}
        if not profile_values:
            return (), {ObservationGapCode.CONTENT_UNSELECTED.value}
        return profile_values, set()

    consent_for = cast(Callable[[str], object] | None, getattr(observation, "consent_for", None))
    if not callable(consent_for):
        return (), {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}
    try:
        consent = consent_for(workspace)
    except Exception:
        return (), {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}
    if consent is None:
        return (), {ObservationGapCode.CONSENT_MISSING.value}
    if getattr(consent, "revoked_at", None) is not None:
        return (), {
            ObservationGapCode.CONSENT_REVOKED.value,
            ObservationGapCode.CONTENT_UNSELECTED.value,
        }
    if getattr(consent, "paused", False):
        return (), {ObservationGapCode.CONTENT_UNSELECTED.value}
    raw_profiles = getattr(consent, "content_capture_profiles", ())
    if type(raw_profiles) is not tuple:
        return (), {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}
    profiles = cast(tuple[object, ...], raw_profiles)
    if any(type(profile) is not str for profile in profiles):
        return (), {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}
    profile_values = tuple(cast(str, profile) for profile in profiles)
    if (
        len(profile_values) > 2
        or any(profile not in _AUTHORIZED_CAPTURE_PROFILES for profile in profile_values)
        or tuple(sorted(set(profile_values), key=str.encode)) != profile_values
    ):
        return (), {ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value}
    if not profile_values:
        return (), {ObservationGapCode.CONTENT_UNSELECTED.value}
    return profile_values, set()


async def _read_wrapper(
    runtime: TaskRuntime,
    ref: ObjectRef,
    *,
    fence_check: Callable[[], bool] | None = None,
) -> bytes:
    if fence_check is not None and not fence_check():
        raise ValueError("content_capture_unavailable")
    collected = bytearray()
    async for chunk in runtime.objects.open_verified(ref):
        if fence_check is not None and not fence_check():
            collected.clear()
            raise ValueError("content_capture_unavailable")
        if type(chunk) is not bytes or len(collected) + len(chunk) > _MAX_WRAPPER_BYTES:
            collected.clear()
            raise ValueError("content_capture_unavailable")
        collected.extend(chunk)
    if not collected:
        raise ValueError("content_capture_unavailable")
    result = bytes(collected)
    collected.clear()
    if len(result) != ref.plaintext_size:
        raise ValueError("content_capture_unavailable")
    if fence_check is not None and not fence_check():
        raise ValueError("content_capture_unavailable")
    return result


def _manifest_from_wrapper(
    material: bytes,
    *,
    object_id: str,
    envelope_digest: str,
) -> tuple[ObservationContentManifest, bytes]:
    try:
        parsed = strict_json_parse(material)
    except (TypeError, ValueError) as exc:
        raise ValueError("content_capture_unavailable") from exc
    expected_keys = {
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
    if (
        not isinstance(parsed, Mapping)
        or set(parsed) != expected_keys
        or canonical_encode(cast(JsonValue, parsed)) != material
        or parsed.get("format") != "yoetz.observation-content/1"
        or type(parsed.get("content_b64")) is not str
        or type(parsed.get("redacted")) is not bool
        or parsed.get("media_type") != _CAPTURED_CONTENT_INNER_MEDIA_TYPE
    ):
        raise ValueError("content_capture_unavailable")
    encoded = cast(str, parsed["content_b64"])
    try:
        content = base64.b64decode(encoded, validate=True)
        chunk = ObservationContentChunk(
            content_kind=ObservationContentKind(cast(str, parsed["content_kind"])),
            correlation_identity=cast(str, parsed["correlation_identity"]),
            source_commitment=cast(str, parsed["source_commitment"]),
            media_type=cast(str, parsed["media_type"]),
            part_index=cast(int, parsed["part_index"]),
            part_count=cast(int, parsed["part_count"]),
            content=content,
            redacted=cast(bool, parsed["redacted"]),
        )
        manifest = ObservationContentManifest(
            object_id=object_id,
            envelope_digest=envelope_digest,
            content_kind=chunk.content_kind,
            part_index=chunk.part_index,
            part_count=chunk.part_count,
            redacted=chunk.redacted,
            content_digest="sha256:" + hashlib.sha256(chunk.content).hexdigest(),
            content_bytes=len(chunk.content),
            correlation_identity=chunk.correlation_identity,
            source_commitment=chunk.source_commitment,
        )
    except (binascii.Error, TypeError, ValueError) as exc:
        raise ValueError("content_capture_unavailable") from exc
    return manifest, content


def _correlations(envelope: ObservationEnvelope) -> frozenset[str]:
    structural = cast(Mapping[str, object], envelope.structural_payload)
    return frozenset(
        value
        for key in _CORRELATION_KEYS
        for value in (structural.get(key),)
        if type(value) is str and value
    )


def _correlation_matches(
    envelope: ObservationEnvelope,
    manifest: ObservationContentManifest,
    correlations: frozenset[str],
) -> bool:
    """Accept only the correlation forms emitted by the native hook mapper."""

    correlation = manifest.correlation_identity
    if correlation is None:
        return False
    if correlation in correlations:
        return True
    prefix = f"{envelope.source_identity}:"
    if not correlation.startswith(prefix):
        return False
    suffix = correlation[len(prefix) :]
    allowed_suffixes = {
        ObservationContentKind.TOOL_OUTPUT: frozenset({"tool-output"}),
        ObservationContentKind.CHANGED_FILE: frozenset({"changed-file"}),
        ObservationContentKind.WORKSPACE_DIFF: frozenset({"diff", "patch"}),
    }
    return suffix in allowed_suffixes.get(manifest.content_kind, frozenset())


def _sort_gaps(gaps: set[str]) -> tuple[str, ...]:
    return tuple(sorted(gaps, key=str.encode))[:16]


def _projected_candidates(
    frozen: FrozenCase,
    *,
    allowed: frozenset[str],
) -> tuple[dict[str, tuple[_CandidateRow, ...]], set[str]]:
    """Index only case-allowed, typed captured evidence before any object read."""

    candidates: dict[str, list[_CandidateRow]] = {}
    gaps: set[str] = set()
    for raw_ref, record in frozen.case.projection.evidence.items():
        evidence_ref = str(raw_ref)
        if evidence_ref not in allowed:
            continue
        typed_record = record
        payload = typed_record.payload
        object_value = None if payload is None else payload.captured_object_id
        if object_value is None:
            continue
        if (
            type(payload) is not EvidenceRecordedPayload
            or payload.evidence_kind is not EvidenceKind.OTHER
            or payload.strength is not EvidenceImmutability.IMMUTABLE_SNAPSHOT
            or payload.content_digest is None
            or type(payload.digest_binding) is not EvidenceDigestBinding
            or payload.digest_binding.subject is not EvidenceDigestSubject.BOUNDED_EXCERPT
            or payload.digest_binding.content_availability
            is not EvidenceContentAvailability.CAPTURED
            or payload.digest_binding.provenance
            is not EvidenceDigestProvenance.OBSERVATION_CAPTURED
            or type(payload.digest_binding.byte_count) is not int
            or payload.digest_binding.byte_count <= 0
        ):
            gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
            continue
        if (
            typed_record.redacted
            or not typed_record.object_available
            or typed_record.redacted_object_id is not None
        ):
            gaps.add(
                ObservationGapCode.CONTENT_REDACTED.value
                if typed_record.redacted
                else ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value
            )
            continue
        assert payload is not None
        candidates.setdefault(str(object_value), []).append((evidence_ref, payload, typed_record))
    return (
        {
            object_value: tuple(sorted(rows, key=lambda row: row[0].encode("ascii")))
            for object_value, rows in candidates.items()
        },
        gaps,
    )


def _session_commitment_for_runtime(
    observation: object,
    workspace: str,
    session_id: str,
    task_id: str,
) -> str:
    route_reader = getattr(observation, "observation_route_for_session", None)
    if callable(route_reader):
        try:
            route = route_reader(workspace=workspace, yoetz_session_id=session_id)
        except Exception as exc:
            raise ValueError("content_capture_unavailable") from exc
        if type(route) is not tuple:
            raise ValueError("content_capture_unavailable")
        route_values = cast(tuple[object, ...], route)
        if (
            len(route_values) != 3
            or type(route_values[0]) is not str
            or type(route_values[1]) is not str
            or type(route_values[2]) is not bool
            or route_values[1] != task_id
        ):
            raise ValueError("content_capture_unavailable")
        # An inactive historical route is admitted only for this exact runtime
        # task. This explicit same-task reattach case retains task evidence while
        # refusing a row from another task; no broad workspace fallback exists.
        commitment = route_values[0]
        if not commitment:
            raise ValueError("content_capture_unavailable")
        return commitment
    # The historical commitment-only accessor cannot prove the runtime task or whether the
    # route is an explicit same-task reattach. Captured-content disclosure therefore fails closed
    # when the strengthened route seam is unavailable.
    raise ValueError("content_capture_unavailable")


async def _session_envelopes(
    observation: object,
    *,
    workspace: str,
    session_id: str,
    task_id: str,
    gaps: set[str],
) -> tuple[ObservationEnvelope, ...]:
    """Read envelopes for the exact routed task session and reattach route."""

    try:
        session_commitment = _session_commitment_for_runtime(
            observation,
            workspace,
            session_id,
            task_id,
        )
    except ValueError:
        gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
        return ()

    status_reader = cast(
        Callable[[str, str], Awaitable[object]] | None,
        getattr(observation, "status_for_session", None),
    )
    if callable(status_reader):
        try:
            await status_reader(workspace, session_commitment)
        except Exception:
            gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
            return ()

    list_for_session = cast(
        Callable[[str, str], object] | None,
        getattr(observation, "list_envelopes_for_session", None),
    )
    try:
        if callable(list_for_session):
            loaded = list_for_session(workspace, session_commitment)
        else:
            list_envelopes = cast(Callable[[str], object], getattr(observation, "list_envelopes"))
            loaded = list_envelopes(workspace)
    except Exception:
        gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
        return ()
    if type(loaded) is not tuple:
        gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
        return ()
    loaded_items = cast(tuple[object, ...], loaded)
    envelopes = tuple(
        item
        for item in loaded_items
        if type(item) is ObservationEnvelope and item.session_commitment == session_commitment
    )
    if len(envelopes) != len(loaded_items):
        gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
    return tuple(
        sorted(
            envelopes,
            key=lambda item: (
                item.cursor.source_generation,
                item.cursor.event_position,
                item.source_identity.encode("ascii"),
            ),
        )
    )


async def resolve_captured_semantic_content(
    *,
    runtime: TaskRuntime,
    frozen: FrozenCase,
    workspace_commitment: str,
    local_observation: object | None = None,
    max_parts: int = _MAX_CAPTURED_SEMANTIC_PARTS,
    max_total_bytes: int = _MAX_CAPTURED_SEMANTIC_INPUT_BYTES,
) -> CapturedContentResolution:
    """Resolve only current-consent Claude/Cursor content for one frozen task.

    Every returned part is authenticated twice: the object store verifies the encrypted envelope,
    then this seam verifies the canonical observation wrapper and its digest/byte binding. Content
    is selected only from envelopes in the exact observation workspace and is later rechecked by
    ``build_semantic_case`` against the frozen evidence projection.
    """

    if type(runtime) is not TaskRuntime or type(frozen) is not FrozenCase:
        raise TypeError("semantic_content_runtime_invalid")
    if (
        type(max_parts) is not int
        or not 0 <= max_parts <= _MAX_CAPTURED_SEMANTIC_PARTS
        or type(max_total_bytes) is not int
        or not 1 <= max_total_bytes <= _MAX_CAPTURED_SEMANTIC_INPUT_BYTES
    ):
        raise ValueError("semantic_content_bounds_invalid")
    observation = runtime.observation
    if observation is None:
        return CapturedContentResolution(
            None,
            (),
            ("content_capture_unavailable",),
            local_fence_required=False,
        )
    local_fence, local_gaps, local_fence_provided = _local_capture_fence(
        local_observation,
        workspace_commitment,
    )
    profiles, gaps = _consent_profiles(observation, workspace_commitment)
    gaps.update(local_gaps)
    if local_fence_provided and (
        local_fence is None
        or not local_fence.active
        or not local_fence.runtime_enabled
        or not local_fence.profiles
    ):
        if local_fence is not None and local_fence.revoked:
            gaps.add(ObservationGapCode.CONSENT_REVOKED.value)
        else:
            gaps.add(ObservationGapCode.CONTENT_UNSELECTED.value)
        return CapturedContentResolution(
            None,
            (),
            _sort_gaps(gaps),
            None if local_fence is None else local_fence.generation,
            () if local_fence is None else local_fence.profiles,
            False,
        )
    if local_fence is not None:
        # Local consent is authoritative for the content arm. Task-store profiles
        # may lag after a CLI change; selecting their intersection preserves the
        # durable task boundary while local disable/pause/revoke remains immediate.
        profiles = tuple(profile for profile in profiles if profile in local_fence.profiles)
        if not profiles:
            gaps.add(ObservationGapCode.CONTENT_UNSELECTED.value)
    scope = (
        None
        if not profiles
        else CapturedContentScope(
            task_id=runtime.task_id,
            session_id=runtime.session_id,
            workspace_commitment=workspace_commitment,
            authorized_profiles=profiles,
        )
    )
    if scope is None:
        return CapturedContentResolution(
            None,
            (),
            _sort_gaps(gaps),
            None if local_fence is None else local_fence.generation,
            () if local_fence is None else local_fence.profiles,
            False,
        )

    if max_parts == 0:
        gaps.add(ObservationGapCode.CONTENT_UNSELECTED.value)
        return CapturedContentResolution(
            scope,
            (),
            _sort_gaps(gaps),
            None if local_fence is None else local_fence.generation,
            () if local_fence is None else local_fence.profiles,
            False,
        )

    allowed = frozenset(str(ref) for ref in frozen.case.allowed_ids)
    candidates, candidate_gaps = _projected_candidates(frozen, allowed=allowed)
    gaps.update(candidate_gaps)
    # Metadata selection is bounded independently from semantic part admission:
    # selecting only the first object ID could split a valid multipart group and
    # turn an otherwise admissible capture into a false unavailable gap.
    selected_objects = tuple(sorted(candidates, key=str.encode))[:_MAX_CAPTURED_SEMANTIC_PARTS]
    if len(candidates) > len(selected_objects):
        gaps.add(ObservationGapCode.CONTENT_UNSELECTED.value)
    if not selected_objects:
        return CapturedContentResolution(
            scope,
            (),
            _sort_gaps(gaps),
            None if local_fence is None else local_fence.generation,
            () if local_fence is None else local_fence.profiles,
            False,
        )

    envelopes = await _session_envelopes(
        observation,
        workspace=workspace_commitment,
        session_id=runtime.session_id,
        task_id=runtime.task_id,
        gaps=gaps,
    )
    pending: list[_PendingRow] = []
    pending_keys: set[tuple[str, str]] = set()
    selected_set = frozenset(selected_objects)
    for envelope in envelopes:
        profile = _profile_for_envelope(envelope)
        if profile is None:
            if envelope.content_object_refs:
                gaps.add(ObservationGapCode.CONTENT_UNSELECTED.value)
            continue
        if profile not in scope.authorized_profiles:
            if envelope.content_object_refs:
                gaps.add(ObservationGapCode.CONTENT_UNSELECTED.value)
            continue
        phase_identity = observation_content_identity(envelope)
        correlations = _correlations(envelope)
        for object_value in envelope.content_object_refs:
            if object_value not in selected_set:
                continue
            associations = candidates.get(object_value, ())
            source = f"{envelope.source_identity}:captured:{object_value}"
            expected_event = stable_observation_id(
                kind=IdKind.EVENT,
                task_id=runtime.task_id,
                source_identity=source,
                mapping_version=MATERIALIZATION_MAPPING_VERSION,
                role="captured_evidence_event",
            )
            matching = tuple(
                row for row in associations if str(row[2].source_event_id) == expected_event
            )
            if len(matching) != 1:
                gaps.add(ObservationGapCode.CONTENT_UNSELECTED.value)
                continue
            evidence_ref, payload, _record = matching[0]
            key = (evidence_ref, object_value)
            if key in pending_keys:
                continue
            pending_keys.add(key)
            pending.append(
                (
                    envelope,
                    profile,
                    phase_identity,
                    evidence_ref,
                    object_value,
                    correlations,
                    payload,
                )
            )

    if not pending:
        return CapturedContentResolution(
            scope,
            (),
            _sort_gaps(gaps),
            None if local_fence is None else local_fence.generation,
            () if local_fence is None else local_fence.profiles,
            False,
        )

    # Read manifest metadata first. This bounds aggregate bytes and validates complete
    # multipart groups before the object store is asked for any plaintext wrapper.
    metadata_groups: dict[
        tuple[str, str, ObservationContentKind, str, str, int], list[_MetadataRow]
    ] = {}
    manifest_reader = cast(Callable[[str], object], getattr(observation, "load_content_manifest"))
    for (
        envelope,
        profile,
        phase_identity,
        evidence_ref,
        object_value,
        correlations,
        payload,
    ) in pending:
        try:
            loaded = manifest_reader(object_value)
            binding = payload.digest_binding
            if type(loaded) is not ObservationContentManifest:
                raise ValueError("content_capture_unavailable")
            if type(binding) is not EvidenceDigestBinding:
                raise ValueError("content_capture_unavailable")
            envelope_digest = loaded.envelope_digest
            content_bytes = loaded.content_bytes
            if (
                loaded.object_id != object_value
                or envelope_digest is None
                or loaded.content_digest != payload.content_digest
                or content_bytes is None
                or content_bytes != binding.byte_count
                or loaded.content_kind
                not in {
                    ObservationContentKind.TOOL_OUTPUT,
                    ObservationContentKind.CHANGED_FILE,
                    ObservationContentKind.WORKSPACE_DIFF,
                }
                or loaded.correlation_identity is None
                or not _correlation_matches(envelope, loaded, correlations)
                or loaded.source_commitment != envelope.cursor.last_source_commitment
                or content_bytes > MAX_CAPTURED_SEMANTIC_CONTENT_BYTES
            ):
                raise ValueError("content_unselected")
            group_key = (
                phase_identity,
                profile,
                loaded.content_kind,
                loaded.correlation_identity,
                loaded.source_commitment or "",
                loaded.part_count,
            )
            metadata_groups.setdefault(group_key, []).append(
                (
                    envelope,
                    profile,
                    phase_identity,
                    evidence_ref,
                    object_value,
                    correlations,
                    payload,
                    loaded,
                )
            )
        except Exception as exc:
            gaps.add(
                ObservationGapCode.CONTENT_UNSELECTED.value
                if str(exc) == "content_unselected"
                else ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value
            )

    complete_groups: list[list[_MetadataRow]] = []
    for rows in metadata_groups.values():
        rows.sort(key=lambda row: (row[-1].part_index, row[3].encode("ascii")))
        count = rows[0][-1].part_count
        if (
            len(rows) != count
            or [row[-1].part_index for row in rows] != list(range(count))
            or len({row[3] for row in rows}) != len(rows)
        ):
            gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
            continue
        complete_groups.append(rows)
    complete_groups.sort(
        key=lambda rows: (
            rows[0][2].encode("ascii"),
            rows[0][1].encode("ascii"),
            rows[0][-1].content_kind.value.encode("ascii"),
            rows[0][-1].correlation_identity.encode("ascii")
            if rows[0][-1].correlation_identity is not None
            else b"",
        )
    )

    bounded_groups: list[list[_MetadataRow]] = []
    admitted_bytes = 0
    admitted_parts = 0
    for rows in complete_groups:
        group_bytes = sum(row[-1].content_bytes or 0 for row in rows)
        if admitted_parts + len(rows) > max_parts or admitted_bytes + group_bytes > max_total_bytes:
            gaps.add(ObservationGapCode.CONTENT_UNSELECTED.value)
            continue
        admitted_bytes += group_bytes
        admitted_parts += len(rows)
        bounded_groups.append(rows)

    parts: list[CapturedSemanticContent] = []
    admitted_phase_bindings: dict[str, str] = {}

    def _open_fence_current() -> bool:
        return not local_fence_provided or (
            local_fence is not None
            and _local_capture_fence_current(
                local_observation,
                workspace_commitment,
                local_fence,
            )
        )

    for rows in bounded_groups:
        opened: list[CapturedSemanticContent] = []
        failed = False
        for (
            envelope,
            profile,
            phase_identity,
            evidence_ref,
            object_value,
            _correlation_values,
            _payload,
            loaded,
        ) in rows:
            if not _open_fence_current():
                # A local pause/disable/revoke that wins before this linearization
                # point invalidates the whole multipart group. Do not open even
                # an authenticated object from the stale task-store snapshot.
                gaps.add(ObservationGapCode.CONTENT_UNSELECTED.value)
                failed = True
                break
            try:
                assert loaded.envelope_digest is not None
                resolved = await runtime.objects.resolve_verified(
                    loaded.object_id, loaded.envelope_digest
                )
                if (
                    type(resolved) is not ObjectRef
                    or resolved.metadata.kind is not ObjectKind.CAPTURED_CONTENT
                    or resolved.metadata.media_type != _CAPTURED_CONTENT_MEDIA_TYPE
                    or resolved.metadata.task_id != runtime.task_id
                    or resolved.object_id != loaded.object_id
                    or resolved.envelope_digest != loaded.envelope_digest
                ):
                    raise ValueError("content_capture_unavailable")
                material = await _read_wrapper(
                    runtime,
                    resolved,
                    fence_check=_open_fence_current,
                )
                parsed, content_bytes = _manifest_from_wrapper(
                    material,
                    object_id=resolved.object_id,
                    envelope_digest=resolved.envelope_digest,
                )
                if parsed != loaded:
                    raise ValueError("content_capture_unavailable")
                opened.append(
                    CapturedSemanticContent(
                        object_ref=resolved,
                        manifest=parsed,
                        content=content_bytes,
                        task_id=runtime.task_id,
                        session_id=runtime.session_id,
                        workspace_commitment=workspace_commitment,
                        phase_identity=phase_identity,
                        capture_profile=profile,
                        capture_gaps=tuple(
                            sorted(
                                set(envelope.gap_codes) & _CONTENT_GAPS,
                                key=str.encode,
                            )
                        ),
                    )
                )
                admitted_phase_bindings[evidence_ref] = phase_identity
            except Exception:
                failed = True
                break
        if failed:
            gaps.add(ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value)
            for (
                _envelope,
                _profile,
                _phase,
                evidence_ref,
                _object,
                _correlation_values,
                _payload,
                _loaded,
            ) in rows:
                admitted_phase_bindings.pop(evidence_ref, None)
            continue
        parts.extend(opened)

    parts.sort(
        key=lambda item: (
            item.phase_identity.encode("ascii"),
            item.manifest.content_kind.value.encode("ascii"),
            item.manifest.correlation_identity.encode("ascii")
            if item.manifest.correlation_identity is not None
            else b"",
            item.manifest.part_index,
            item.object_ref.object_id.encode("ascii"),
        )
    )
    scope = CapturedContentScope(
        task_id=runtime.task_id,
        session_id=runtime.session_id,
        workspace_commitment=workspace_commitment,
        authorized_profiles=scope.authorized_profiles,
        phase_bindings=tuple(
            sorted(admitted_phase_bindings.items(), key=lambda item: item[0].encode("ascii"))
        ),
    )
    return CapturedContentResolution(
        scope,
        tuple(parts),
        _sort_gaps(gaps),
        None if local_fence is None else local_fence.generation,
        () if local_fence is None else local_fence.profiles,
        local_fence_provided and bool(parts),
    )
