"""Issue #509: composed synthetic Codex ingress through encrypted READY storage.

These are in-process fixtures, not native Codex/socket/provider execution. Payloads enter
the host normalizer before the production ingest, object store, and ledger paths.
"""

from __future__ import annotations

import base64
import subprocess
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from builders.multi_agent import (
    MultiAgentService,
    multi_agent_service,
    relock_and_reopen_multi_agent_service,
)
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.semantic_case import build_semantic_case, semantic_case_to_prepared_payload
from yoetz.application.semantic_content import SemanticContentResolution, resolve_semantic_content
from yoetz.application.start import StartInternalResult
from yoetz.cli.hooks import bind_start_mapping_outcome
from yoetz.cli.observe_hooks import (
    _visible_content_chunks,  # pyright: ignore[reportPrivateUsage]
    map_hook_payload_to_envelope,
)
from yoetz.domain.events import EvidenceRecordedPayload
from yoetz.domain.observation import (
    ObservationContentChunk,
    ObservationContentKind,
    ObservationEnvelope,
    ObservationIngestRequest,
    ObservationIngestResult,
    ObservationRevokeCommand,
    ObservationSource,
    ObservationStatusQuery,
    observation_ingest_request_to_json,
    observation_ingest_result_from_json,
)
from yoetz.domain.privacy import ReviewContextProfile, ReviewSelectionPolicy
from yoetz.domain.values import timestamp_from_datetime
from yoetz.kernel.deterministic_checks import DeterministicCase, build_deterministic_case
from yoetz.kernel.reducers import replay
from yoetz.ports.control import RepositoryPrivacyContext
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.runtime import RouteAccess, RouteCommand, TaskRuntime
from yoetz.ports.semantic import SemanticCase
from yoetz.protocol.canonical import JsonValue, strict_json_parse
from yoetz.protocol.ids import IdKind, new_id
from yoetz.protocol.models import StartRequest

pytestmark = pytest.mark.anyio

_REPOSITORY = RepositoryPrivacyContext("hmac-sha256:" + "d" * 64, "git_common_root")
_MARKER = "synthetic-509-source: return left - right; test_addition FAILED"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _identity() -> dict[str, object]:
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": new_id(IdKind.REQUEST),
        "actor": {"actor_id": "harness:capture-conformance", "actor_type": "harness"},
        "client": {
            "kind": "test_client",
            "version": "0.1.0",
            "integration": "cooperative_mcp",
        },
    }


@dataclass
class _Cell:
    service: MultiAgentService
    task: StartInternalResult
    local: LocalObservationStore
    workspace: str
    host_session: str

    def normalize(
        self,
        *,
        tool: str = "functions.exec_command",
        output: str = _MARKER,
        call: str = "synthetic-509-call",
    ) -> tuple[ObservationEnvelope, tuple[ObservationContentChunk, ...]]:
        payload: dict[str, JsonValue] = {
            "hook_event_name": "PostToolUse",
            "session_id": self.host_session,
            "tool_name": tool,
            "tool_call_id": call,
            "exit_status": 1,
            "tool_response": output,
            "tool_input": {"cmd": "synthetic-input-must-not-be-output"},
            "transcript": "synthetic-transcript-must-never-be-selected",
        }
        envelope = map_hook_payload_to_envelope(
            "PostToolUse",
            payload,
            session_commitment=self.local.session_commitment(self.host_session),
            event_ordinal=1,
            key_material=self.local.key_material(),
        )
        chunks, truncated = _visible_content_chunks(
            "PostToolUse", payload, envelope=envelope, workspace_locator=None
        )
        assert not truncated
        assert all(_MARKER not in str(value) for value in envelope.structural_payload.values())
        return envelope, chunks

    async def ingest(
        self, envelope: ObservationEnvelope, chunks: tuple[ObservationContentChunk, ...]
    ) -> ObservationIngestResult:
        return observation_ingest_result_from_json(
            await self.service.app.observation_ingest(
                observation_ingest_request_to_json(
                    ObservationIngestRequest(self.host_session, envelope, chunks)
                )
            )
        )

    @asynccontextmanager
    async def runtime(self) -> AsyncGenerator[TaskRuntime]:
        runtime = await self.service.app.runtime.route(
            RouteCommand(
                self.task.session_id,
                self.task.writer_id,
                RouteAccess.WRITE,
                frozenset(
                    {
                        RuntimeCapability.STRUCTURAL_READ,
                        RuntimeCapability.PAYLOAD_READ,
                        RuntimeCapability.WRITE,
                    }
                ),
            )
        )
        try:
            yield runtime
        finally:
            await self.service.app.runtime.release(runtime)


@asynccontextmanager
async def _cell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[_Cell]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True, capture_output=True)
    async with multi_agent_service(tmp_path / "state") as service:
        monkeypatch.setenv("YOETZ_ISOLATED_ROOT", str(service.root))
        task = await service.app.start(
            StartRequest.model_validate(
                {
                    **_identity(),
                    "mode": "create",
                    "task_title": "Synthetic capture conformance",
                    "workspace_ref": str(workspace.resolve()),
                    "external_ref": "capture-509",
                    "requested_view": "compact",
                }
            ),
            repository_privacy_context=_REPOSITORY,
        )
        host_session = "synthetic-codex-capture-509"
        assert (
            bind_start_mapping_outcome(
                {
                    "session_id": host_session,
                    "tool_name": "mcp__yoetz__start",
                    "tool_response": {"structuredContent": task.as_wire()},
                },
                _state=service.root / "state",
            )
            == "bound"
        )
        local = LocalObservationStore(_state=service.root / "state")
        commitment = local.workspace_commitment(str(workspace.resolve()))
        local.grant_consent(commitment)
        local.bind_codex_session(commitment, host_session)
        yield _Cell(service, task, local, commitment, host_session)


async def _case(runtime: TaskRuntime) -> DeterministicCase:
    records = tuple([record async for record in runtime.ledger.load_events(runtime.session_id)])
    projection = replay(records)
    availability = await runtime.ledger.load_case_availability(
        runtime.session_id, await runtime.ledger.load_frontier(), projection
    )
    return build_deterministic_case(projection, records, availability)


async def _semantic(runtime: TaskRuntime, cell: _Cell, *, authorized: bool = True) -> SemanticCase:
    case = await _case(runtime)
    selection = ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED)
    resolution = await resolve_semantic_content(
        frozen_case=case,
        runtime=runtime,
        workspace=cell.workspace,
        authorized=authorized,
        review_selection=selection,
    )
    return _build_semantic(case, resolution)


def _build_semantic(case: DeterministicCase, resolution: SemanticContentResolution) -> SemanticCase:
    return build_semantic_case(
        case_id="cas_00000000-0000-4000-8000-000000000509",
        frozen_case=case,
        dependency_digest="sha256:" + "a" * 64,
        findings=(),
        review_context_profile=ReviewContextProfile.EXPANDED,
        review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED),
        policy_id="pvy_00000000-0000-4000-8000-000000000509",
        policy_version="1",
        resolved_content=resolution,
    )


def _prepared(semantic: SemanticCase) -> bytes:
    # Explicitly selects all already-built fixture items; no provider dispatch is claimed.
    return semantic_case_to_prepared_payload(semantic, {item.item_id for item in semantic.items})


async def test_captured_resolution_cannot_be_reused_after_a_new_ledger_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _cell(tmp_path, monkeypatch) as cell:
        envelope, chunks = cell.normalize()
        assert (await cell.ingest(envelope, chunks)).disposition.value == "accepted"
        async with cell.runtime() as runtime:
            case = await _case(runtime)
            resolution = await resolve_semantic_content(
                frozen_case=case,
                runtime=runtime,
                workspace=cell.workspace,
                authorized=True,
                review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED),
            )
            assert _MARKER.encode() in _prepared(_build_semantic(case, resolution))
        newer, newer_chunks = cell.normalize(call="next-observed-call", output="next output")
        assert (await cell.ingest(newer, newer_chunks)).disposition.value == "accepted"
        async with cell.runtime() as runtime:
            current = await _case(runtime)
            assert current.frontier != case.frontier
            with pytest.raises(ValueError, match="semantic_content_frontier_mismatch"):
                _build_semantic(current, resolution)


@pytest.mark.parametrize("tool", ["Bash", "functions.exec_command"])
async def test_codex_normalizer_retains_encrypted_output_and_replays_without_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    async with _cell(tmp_path, monkeypatch) as cell:
        envelope, chunks = cell.normalize(tool=tool)
        assert len(chunks) == 1
        first = await cell.ingest(envelope, chunks)
        assert first.disposition.value == "accepted"
        async with cell.runtime() as runtime:
            before = await runtime.ledger.load_frontier()
            case = await _case(runtime)
            rows = tuple(case.projection.evidence.values())
            assert len(rows) == 1
            evidence = rows[0].payload
            assert isinstance(evidence, EvidenceRecordedPayload)
            assert evidence.digest_binding is not None
            assert evidence.digest_binding.provenance.value == "observation_captured"
            assert evidence.captured_object_id is not None
            assert runtime.observation is not None
            manifest = runtime.observation.load_content_manifest(str(evidence.captured_object_id))
            assert manifest is not None and manifest.envelope_digest is not None
            ref = await runtime.objects.resolve_verified(
                manifest.object_id, manifest.envelope_digest
            )
            raw = b"".join([part async for part in runtime.objects.open_verified(ref)])
            document = cast(Mapping[str, JsonValue], strict_json_parse(raw))
            assert base64.b64decode(cast(str, document["content_b64"])) == _MARKER.encode()
            semantic = await _semantic(runtime, cell)
            prepared = _prepared(semantic)
            assert _MARKER.encode() in prepared, (
                semantic.packet.omissions,
                semantic.packet.coverage.known_gaps,
            )
            assert b"synthetic-input-must-not-be-output" not in prepared
            assert b"synthetic-transcript-must-never-be-selected" not in prepared
            captured_excerpts = [
                row
                for row in semantic.packet.targeted_excerpts
                if row.digest_provenance is not None
            ]
            assert len(captured_excerpts) == 1
            assert captured_excerpts[0].subject_state_relation.value == "unknown"
            provenance = captured_excerpts[0].digest_provenance
            assert provenance is not None
            assert provenance.provenance.value == "observation_captured"
        replayed = await cell.ingest(envelope, ())
        assert replayed.disposition.value == "duplicate"
        async with cell.runtime() as runtime:
            assert await runtime.ledger.load_frontier() == before
        # The actual object bytes on disk must remain encrypted, not merely base64-encoded.
        for path in cell.service.root.rglob("*"):
            if path.is_file():
                assert _MARKER.encode() not in path.read_bytes()


async def test_incomplete_multipart_ingress_retains_explicit_gap_without_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _cell(tmp_path, monkeypatch) as cell:
        envelope, chunks = cell.normalize()
        partial = (replace(chunks[0], part_count=2),)
        result = await cell.ingest(envelope, partial)
        assert result.disposition.value == "accepted"
        async with cell.runtime() as runtime:
            case = await _case(runtime)
            assert not case.projection.evidence
        assert (
            "content_capture_unavailable"
            in cell.local.status(ObservationStatusQuery(cell.workspace)).gaps
        )


@pytest.mark.parametrize("failure", ["wrong-session", "revoked"])
async def test_capture_ingress_refuses_wrong_session_or_revoked_consent_before_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    async with _cell(tmp_path, monkeypatch) as cell:
        envelope, chunks = cell.normalize()
        if failure == "wrong-session":
            envelope = replace(envelope, session_commitment="hmac-sha256:" + "f" * 64)
            expected_reason = "consent_missing"
        else:
            cell.local.revoke(ObservationRevokeCommand(cell.workspace))
            expected_reason = (
                "consent_missing"  # Revocation also removes the source-session binding.
            )
        async with cell.runtime() as runtime:
            before = await runtime.ledger.load_frontier()
        result = await cell.ingest(envelope, chunks)
        assert result.disposition.value == "rejected"
        assert result.reason == expected_reason
        async with cell.runtime() as runtime:
            assert await runtime.ledger.load_frontier() == before
            assert not (await _case(runtime)).projection.evidence


@pytest.mark.parametrize(
    "kind", [ObservationContentKind.TOOL_INPUT, ObservationContentKind.WORKSPACE_LOCATOR]
)
async def test_excluded_content_kind_never_enters_captured_evidence_or_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: ObservationContentKind
) -> None:
    async with _cell(tmp_path, monkeypatch) as cell:
        envelope, chunks = cell.normalize()
        assert (
            await cell.ingest(envelope, (replace(chunks[0], content_kind=kind),))
        ).disposition.value == "accepted"
        async with cell.runtime() as runtime:
            case = await _case(runtime)
            assert not case.projection.evidence
            assert any(
                "content_unselected" in coverage.known_gaps
                for coverage in case.coverage_by_ref.values()
            )
            assert _MARKER.encode() not in _prepared(await _semantic(runtime, cell))


async def test_selected_packet_preserves_valid_item_beside_oversized_and_missing_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _cell(tmp_path, monkeypatch) as cell:
        missing_id: str | None = None
        for call, output in (
            ("missing", "missing-509-content"),
            ("oversized", "oversized-509:" + "x" * 6000),
            ("valid", _MARKER),
        ):
            envelope, chunks = cell.normalize(output=output, call=call)
            assert (await cell.ingest(envelope, chunks)).disposition.value == "accepted"
            if call == "missing":
                async with cell.runtime() as runtime:
                    evidence = next(
                        iter((await _case(runtime)).projection.evidence.values())
                    ).payload
                    assert evidence is not None and evidence.captured_object_id is not None
                    missing_id = str(evidence.captured_object_id)
        assert missing_id is not None
        # Delete only the exact captured object inside this fixture's synthetic private root.
        paths = tuple(cell.service.root.rglob(missing_id))
        assert len(paths) == 1
        paths[0].unlink()
        async with cell.runtime() as runtime:
            semantic = await _semantic(runtime, cell)
            prepared = _prepared(semantic)
            assert _MARKER.encode() in prepared, (
                semantic.packet.omissions,
                semantic.packet.coverage.known_gaps,
            )
            assert b"missing-509-content" not in prepared
            assert b"oversized-509:" not in prepared
            assert (
                len(
                    [
                        row
                        for row in semantic.packet.targeted_excerpts
                        if row.digest_provenance is not None
                    ]
                )
                == 1
            )
            assert {row.reason for row in semantic.packet.omissions} >= {
                "not_recorded",
                "not_selected",
            }
            assert "captured_object_unavailable" in semantic.packet.coverage.known_gaps
            assert "semantic_case_content_over_item_limit" in semantic.packet.coverage.known_gaps
            denied = await _semantic(runtime, cell, authorized=False)
            assert _MARKER.encode() not in _prepared(denied)
            assert not [
                row for row in denied.packet.targeted_excerpts if row.digest_provenance is not None
            ]
            assert "withheld_by_policy" in {row.reason for row in denied.packet.omissions}


@pytest.mark.parametrize("mismatch", ["session-stream", "source", "correlation", "regrant"])
async def test_captured_packet_rejects_non_native_or_mismatched_source_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    async with _cell(tmp_path, monkeypatch) as cell:
        envelope, chunks = cell.normalize()
        if mismatch == "session-stream":
            envelope = replace(envelope, source=ObservationSource.CODEX_SESSION_STREAM)
        elif mismatch == "source":
            chunks = (replace(chunks[0], source_commitment="hmac-sha256:" + "f" * 64),)
        elif mismatch == "correlation":
            chunks = (replace(chunks[0], correlation_identity="different-call:tool-output"),)
        assert (await cell.ingest(envelope, chunks)).disposition.value == "accepted"
        async with cell.runtime() as runtime:
            case = await _case(runtime)
            assert case.projection.evidence
            after_capture = timestamp_from_datetime(
                datetime.fromisoformat(envelope.receipt_time.wire) + timedelta(seconds=1)
            )
            resolution = await resolve_semantic_content(
                frozen_case=case,
                runtime=runtime,
                workspace=cell.workspace,
                authorized=True,
                authorized_since=after_capture if mismatch == "regrant" else None,
                review_selection=ReviewSelectionPolicy.for_profile(ReviewContextProfile.EXPANDED),
            )
            assert resolution.items
            assert all(item.content is None for item in resolution.items.values())
            assert _MARKER.encode() not in _prepared(_build_semantic(case, resolution))


async def test_missing_multipart_sibling_omits_every_part_from_selected_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yoetz.application.observation_materialize as materialize
    from yoetz.domain.observation import ObservationContentManifest

    original_drafts = materialize._captured_evidence_drafts  # pyright: ignore[reportPrivateUsage]

    def descending_evidence_order(
        envelope: ObservationEnvelope,
        *,
        task_id: str,
        manifests: tuple[ObservationContentManifest, ...],
        parents: tuple[str, ...] = (),
    ) -> tuple[tuple[materialize.MaterializedObservationDraft, ...], tuple[str, ...]]:
        # Force the real generated evidence IDs into descending input order. Keep the actual
        # identity algorithm so the downstream authenticated resolver still checks real IDs.
        ordered = tuple(
            sorted(
                manifests,
                key=lambda item: materialize.stable_observation_id(
                    kind=IdKind.EVIDENCE,
                    task_id=task_id,
                    source_identity=f"{envelope.source_identity}:captured:{item.object_id}",
                    mapping_version=materialize.MATERIALIZATION_MAPPING_VERSION,
                    role="captured_evidence",
                ),
                reverse=True,
            )
        )
        return original_drafts(envelope, task_id=task_id, manifests=ordered, parents=parents)

    monkeypatch.setattr(materialize, "_captured_evidence_drafts", descending_evidence_order)
    async with _cell(tmp_path, monkeypatch) as cell:
        envelope, chunks = cell.normalize()
        chunk = chunks[0]
        parts = (
            replace(chunk, part_index=0, part_count=2, content=chunk.content[:30]),
            replace(chunk, part_index=1, part_count=2, content=chunk.content[30:]),
        )
        assert (await cell.ingest(envelope, parts)).disposition.value == "accepted"
        async with cell.runtime() as runtime:
            case = await _case(runtime)
            assert len(case.projection.evidence) == 2
            semantic = await _semantic(runtime, cell)
            assert (
                len(
                    [
                        row
                        for row in semantic.packet.targeted_excerpts
                        if row.digest_provenance is not None
                    ]
                )
                == 2
            )
            evidence = next(iter(case.projection.evidence.values())).payload
            assert evidence is not None and evidence.captured_object_id is not None
            paths = tuple(cell.service.root.rglob(str(evidence.captured_object_id)))
            assert len(paths) == 1
            paths[0].unlink()
            after_loss = await _semantic(runtime, cell)
            assert not [
                row
                for row in after_loss.packet.targeted_excerpts
                if row.digest_provenance is not None
            ]
            assert "captured_object_unavailable" in after_loss.packet.coverage.known_gaps


async def test_captured_selection_survives_ready_restart_and_preserves_deleted_object_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _cell(tmp_path, monkeypatch) as cell:
        envelope, chunks = cell.normalize(output="restart-neighbor-509", call="restart-neighbor")
        assert (await cell.ingest(envelope, chunks)).disposition.value == "accepted"
        async with cell.runtime() as runtime:
            evidence = next(iter((await _case(runtime)).projection.evidence.values())).payload
            assert evidence is not None and evidence.captured_object_id is not None
            neighbor_object_id = str(evidence.captured_object_id)
        valid, valid_chunks = cell.normalize(call="restart-valid")
        assert (await cell.ingest(valid, valid_chunks)).disposition.value == "accepted"

        await relock_and_reopen_multi_agent_service(cell.service)
        async with cell.runtime() as runtime:
            semantic = await _semantic(runtime, cell)
            packet = _prepared(semantic)
            assert _MARKER.encode() in packet
            assert b"restart-neighbor-509" in packet
            frontier = await runtime.ledger.load_frontier()
        # Restart and a contentless retry do not append a second captured result.
        assert (await cell.ingest(valid, ())).disposition.value == "duplicate"
        async with cell.runtime() as runtime:
            assert await runtime.ledger.load_frontier() == frontier

        paths = tuple(cell.service.root.rglob(neighbor_object_id))
        assert len(paths) == 1
        paths[0].unlink()
        await relock_and_reopen_multi_agent_service(cell.service)
        async with cell.runtime() as runtime:
            after_loss = await _semantic(runtime, cell)
            packet = _prepared(after_loss)
            assert _MARKER.encode() in packet
            assert b"restart-neighbor-509" not in packet
            assert "captured_object_unavailable" in after_loss.packet.coverage.known_gaps
            assert (
                len(
                    [
                        row
                        for row in after_loss.packet.targeted_excerpts
                        if row.digest_provenance is not None
                    ]
                )
                == 1
            )
