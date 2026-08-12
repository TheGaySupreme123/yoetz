"""Unit tests for unified hook observation ingress and consent plumbing."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest

from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.application.recommendations import RecommendationState, store_recommendation_state
from yoetz.cli import observe_hooks as observe_hooks_module
from yoetz.cli.observe_hooks import (
    SUPPORTED_HOOK_EVENTS,
    handle_observe,
    map_hook_payload_to_envelope,
)
from yoetz.domain.observation import (
    ObservationGapCode,
    ObservationIngestDisposition,
    ObservationIngestResult,
    ObservationSource,
    ObservationStatusQuery,
    observation_ingest_result_to_json,
)

_KEY = b"k" * 32


def test_map_supported_hook_payloads_structural_only(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-1")
    for event in sorted(SUPPORTED_HOOK_EVENTS):
        payload = {
            "session_id": "sess-1",
            "hook_event_name": event,
            "tool_name": "shell",
            "correlation_id": "corr-1",
            "exit_status": 0,
            "transcript": "MUST_NOT_APPEAR",
            "prompt": "MUST_NOT_APPEAR",
        }
        envelope = map_hook_payload_to_envelope(
            event,
            payload,
            session_commitment=session,
            event_ordinal=1,
            key_material=store.key_material(),
        )
        assert envelope.source is ObservationSource.CODEX_HOOK
        assert envelope.event_kind == event
        assert "transcript" not in envelope.structural_payload
        assert "prompt" not in envelope.structural_payload
        assert envelope.structural_payload.get("tool_name") == "shell"
        assert envelope.cursor.last_source_commitment.startswith("hmac-sha256:")


def test_unknown_future_hook_becomes_opaque_gap(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-2")
    envelope = map_hook_payload_to_envelope(
        "FutureHostEvent",
        {"session_id": "sess-2"},
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
        gap_codes=(ObservationGapCode.UNSUPPORTED_EVENT.value,),
    )
    assert envelope.event_kind == "FutureHostEvent"
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in envelope.gap_codes


def test_visible_unknown_content_is_redacted_and_hidden_fields_are_ignored(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-visible")
    envelope = map_hook_payload_to_envelope(
        "FutureHostEvent",
        {"session_id": "sess-visible"},
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
    )
    chunks, truncated = observe_hooks_module._visible_content_chunks(  # pyright: ignore[reportPrivateUsage]
        "FutureHostEvent",
        {
            "visibility": "task",
            "visible_content": "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456",
            "reasoning": "HIDDEN_REASONING_CANARY",
            "system": "SYSTEM_PROMPT_CANARY",
        },
        envelope=envelope,
        workspace_locator=None,
    )
    assert truncated is False
    assert len(chunks) == 1
    assert chunks[0].redacted is True
    assert b"sk-abcdefghijklmnopqrstuvwxyz123456" not in chunks[0].content
    assert b"HIDDEN_REASONING_CANARY" not in chunks[0].content
    assert b"SYSTEM_PROMPT_CANARY" not in chunks[0].content


def test_content_cap_sets_truncated_flag(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-trunc")
    envelope = map_hook_payload_to_envelope(
        "AgentMessage",
        {"session_id": "sess-trunc", "message": "x" * 700_000},
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
    )
    chunks, truncated = observe_hooks_module._visible_content_chunks(  # pyright: ignore[reportPrivateUsage]
        "AgentMessage",
        {"message": "x" * 700_000},
        envelope=envelope,
        workspace_locator=None,
    )
    assert truncated is True
    assert chunks


def test_identical_tool_calls_remain_distinct(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    session = store.session_commitment("sess-dup")
    payload = {
        "session_id": "sess-dup",
        "tool_name": "shell",
        "tool_call_id": "same-call",
        "exit_status": 0,
    }
    first = map_hook_payload_to_envelope(
        "PostToolUse",
        payload,
        session_commitment=session,
        event_ordinal=1,
        key_material=_KEY,
    )
    second = map_hook_payload_to_envelope(
        "PostToolUse",
        payload,
        session_commitment=session,
        event_ordinal=2,
        key_material=_KEY,
    )
    assert first.source_identity != second.source_identity


def test_observe_without_consent_exits_zero_no_spool(tmp_path: Path) -> None:
    stdout = io.BytesIO()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "s1", "tool_name": "shell", "exit_status": 0}
        ).encode(),
        stdout=stdout,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    assert json.loads(stdout.getvalue().decode()) == {}


def test_runtime_disabled_skips_capture_but_session_start_surfaces_cached_recommendation(
    tmp_path: Path,
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    store.set_runtime_enabled(False)
    store_recommendation_state(
        RecommendationState(last_evaluated_version="0.1.0", pending=("observation-enabled",)),
        root=tmp_path,
    )
    stdout = io.BytesIO()
    code = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps({"session_id": "disabled-session"}).encode(),
        stdout=stdout,
        workspace=str(tmp_path),
        _state=tmp_path,
    )
    assert code == 0
    payload = json.loads(stdout.getvalue())
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "Enable local observation" in context
    assert "workspace consent remains required" in context
    assert "yoetz recommend accept observation-enabled" in context
    assert store.pending_workspaces() == ()
    workspace_state = tmp_path / "observation/workspaces"
    assert not workspace_state.exists() or not list(workspace_state.glob("*.json"))


def test_unsafe_runtime_gate_fails_closed_with_distinct_diagnostic(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    store.set_runtime_enabled(True)
    gate = tmp_path / "observation/runtime-gate.json"
    gate.write_text("not-json", encoding="utf-8")
    stdout = io.BytesIO()
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps({"session_id": "unsafe-gate", "tool_name": "shell"}).encode(),
        stdout=stdout,
        workspace=str(tmp_path),
        _state=tmp_path,
    )
    assert json.loads(stdout.getvalue()) == {}
    diagnostics = (tmp_path / "observation/hook-diagnostics.jsonl").read_text()
    assert '"reason":"runtime_gate_unsafe"' in diagnostics
    assert store.pending_workspaces() == ()


def test_service_unavailable_never_spools_visible_plaintext(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    canary = "VISIBLE_TASK_CANARY_6f4b2f"
    code = handle_observe(
        event_name="UserPromptSubmit",
        stdin_bytes=json.dumps(
            {"session_id": "no-spool", "prompt": canary, "event_ordinal": 1}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    persisted = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert canary.encode() not in persisted
    status = store.status(ObservationStatusQuery(workspace))
    assert ObservationGapCode.CONTENT_CAPTURE_UNAVAILABLE.value in status.gaps


def test_observe_ingests_when_consented_and_pairs_pre_post(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)

    pre = handle_observe(
        event_name="PreToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "pair-1",
                "tool_name": "shell",
                "correlation_id": "c1",
                "tool_call_id": "c1",
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert pre == 0
    post_out = io.BytesIO()
    post = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "pair-1",
                "tool_name": "shell",
                "correlation_id": "c1",
                "tool_call_id": "c1",
                "exit_status": 0,
            }
        ).encode(),
        stdout=post_out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert post == 0
    status = store.status(
        __import__(
            "yoetz.domain.observation", fromlist=["ObservationStatusQuery"]
        ).ObservationStatusQuery(workspace)
    )
    assert status.source_coverage[ObservationSource.CODEX_HOOK] is True
    assert ObservationGapCode.UNPAIRED_EVENT.value not in status.gaps


def test_unpaired_post_records_gap(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "pair-2",
                "tool_name": "shell",
                "correlation_id": "missing-pre",
                "exit_status": 1,
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    status = store.status(
        __import__(
            "yoetz.domain.observation", fromlist=["ObservationStatusQuery"]
        ).ObservationStatusQuery(workspace)
    )
    assert ObservationGapCode.UNPAIRED_EVENT.value in status.gaps


def test_yoetz_tool_still_ingests_but_skips_advice_loop(tmp_path: Path) -> None:
    from yoetz.domain.observation import AdviceSnapshot
    from yoetz.domain.values import finding_id
    from yoetz.protocol.coverage import (
        ArtifactObservation,
        AuthorshipAssurance,
        CheckType,
        Coverage,
        EvidenceImmutability,
        LedgerFreshness,
        PublicationChannel,
    )

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.set_advice_snapshot(
        workspace,
        AdviceSnapshot(
            ranked_finding_ids=(finding_id("fnd_00000000-0000-4000-8000-000000000001"),),
            evidence_basis_digest="sha256:" + "a" * 64,
            confidence_coverage=Coverage(
                publication_channels=(PublicationChannel.HOOK_OBSERVED,),
                authorship_assurance=AuthorshipAssurance.HARNESS_OBSERVED,
                artifact_observation=ArtifactObservation.HOOK_OBSERVED,
                evidence_immutability=EvidenceImmutability.CONTENT_DIGEST,
                ledger_freshness=LedgerFreshness.CURRENT,
                check_types=(CheckType.DETERMINISTIC,),
                known_gaps=(),
            ),
            recommended_next_action="call_status",
            freshness_frontier="frontier-1",
            suppression_identity="suppress-1",
        ),
    )
    out = io.BytesIO()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {"session_id": "yoetz-tool", "tool_name": "mcp__yoetz__status"}
        ).encode(),
        stdout=out,
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0
    assert json.loads(out.getvalue().decode()) == {}


def test_skip_service_session_start_never_opens_service_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yoetz.adapters.integrations.codex_lifecycle import (
        mapping_from_start_ids,
        store_mapping,
    )
    from yoetz.protocol.ids import IdKind, new_id

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    service_touches: list[str] = []

    async def forbidden_connect(*_args: object, **_kwargs: object) -> object:
        service_touches.append("connect_service")
        raise AssertionError("skip_service must never open a service connection")

    async def forbidden_auto_start(*_args: object, **_kwargs: object) -> object:
        service_touches.append("_try_auto_start")
        raise AssertionError("skip_service must never auto-attach a ledger task")

    monkeypatch.setattr(observe_hooks_module, "connect_service", forbidden_connect)
    monkeypatch.setattr(observe_hooks_module, "_try_auto_start", forbidden_auto_start)

    # Unmapped session: the auto-attach branch must be gated by skip_service.
    unmapped = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps(
            {"session_id": "probe-unmapped", "hook_event_name": "SessionStart", "cwd": "."}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert unmapped == 0

    # Mapped session: the status-read branch must be gated as well.
    store_mapping(
        mapping_from_start_ids(
            codex_session_id="probe-mapped",
            yoetz_task_id=new_id(IdKind.TASK),
            yoetz_session_id=new_id(IdKind.SESSION),
            yoetz_writer_id=new_id(IdKind.WRITER),
            last_frontier=None,
        ),
        _state=tmp_path,
    )
    mapped = handle_observe(
        event_name="SessionStart",
        stdin_bytes=json.dumps(
            {"session_id": "probe-mapped", "hook_event_name": "SessionStart", "cwd": "."}
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert mapped == 0
    assert service_touches == []
    # Local capture still ran: sessions bound and envelopes queued in the outbox.
    assert store.find_workspace_for_codex_session("probe-unmapped") == workspace
    assert store.find_workspace_for_codex_session("probe-mapped") == workspace
    assert store.list_pending_outbox(workspace)


def test_malformed_stdin_exits_zero(tmp_path: Path) -> None:
    code = handle_observe(
        event_name="Stop",
        stdin_bytes=b"{not-json",
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        skip_service=True,
    )
    assert code == 0


def _drain_envelope(store: LocalObservationStore, session: str, identity: str, ordinal: int):
    payload = {
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "tool_name": "shell",
        "correlation_id": f"corr-{ordinal}",
        "exit_status": 1,
    }
    commitment = store.session_commitment(session)
    return map_hook_payload_to_envelope(
        "PostToolUse",
        payload,
        session_commitment=commitment,
        event_ordinal=ordinal,
        key_material=store.key_material(),
    )


@pytest.mark.anyio
async def test_drain_quarantines_permanent_and_keeps_retryable(
    tmp_path: Path,
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "sess-drain")
    perm = _drain_envelope(store, "sess-drain", "hook:perm", 1)
    retry = _drain_envelope(store, "sess-drain", "hook:retry", 2)
    store.enqueue_outbox(workspace, "sess-drain", perm)
    store.enqueue_outbox(workspace, "sess-drain", retry)
    assert store.pending_outbox_count(workspace) == 2

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del deadline_ms
            envelope = body["envelope"]  # type: ignore[index]
            if envelope["source_identity"] == perm.source_identity:
                result = ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.CONSENT_REVOKED.value,
                    None,
                )
            else:
                result = ObservationIngestResult(
                    ObservationIngestDisposition.REJECTED,
                    ObservationGapCode.SERVICE_UNAVAILABLE.value,
                    None,
                )
            return observation_ingest_result_to_json(result)

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    await observe_hooks_module._drain_outbox(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="sess-drain",
        connect=connect,  # type: ignore[arg-type]
    )

    # Permanent -> quarantined (never dropped); retryable -> still pending.
    assert store.quarantined_count(workspace) == 1
    assert store.list_quarantine(workspace)[0][1].source_identity == perm.source_identity
    pending = store.list_pending_outbox(workspace)
    assert len(pending) == 1
    assert pending[0][1].source_identity == retry.source_identity


@pytest.mark.anyio
async def test_drain_is_round_robin_across_all_workspace_sessions(
    tmp_path: Path,
) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    for session in ("current", "recovered"):
        store.bind_codex_session(workspace, session)
        for ordinal in (1, 2):
            store.enqueue_outbox(
                workspace,
                session,
                _drain_envelope(store, session, f"hook:{session}:{ordinal}", ordinal),
            )
    calls: list[str] = []

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del deadline_ms
            calls.append(body["codex_session_id"])  # type: ignore[index]
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="current",
        connect=connect,  # type: ignore[arg-type]
    )
    assert calls == ["current", "recovered", "current", "recovered"]
    assert store.pending_outbox_count(workspace) == 0


@pytest.mark.anyio
async def test_drain_preflight_failure_skips_rows_and_records_diagnostic(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "preflight")
    store.enqueue_outbox(workspace, "preflight", _drain_envelope(store, "preflight", "x", 1))

    async def unavailable(_kind: object):
        raise RuntimeError("offline")

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="preflight",
        connect=unavailable,
        _state=tmp_path,
    )
    assert store.list_pending_outbox_rows(workspace)[0].attempts == 0
    diagnostics = (tmp_path / "observation/hook-diagnostics.jsonl").read_text()
    assert '"reason":"drain_preflight_failed"' in diagnostics


@pytest.mark.anyio
async def test_drain_empty_outbox_never_connects_or_records_diagnostics(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "idle")
    connects: list[object] = []

    async def recording_connect(kind: object):
        connects.append(kind)
        raise RuntimeError("offline")

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="idle",
        connect=recording_connect,
        _state=tmp_path,
    )
    assert connects == []
    assert not (tmp_path / "observation/hook-diagnostics.jsonl").exists()


@pytest.mark.anyio
async def test_drain_budget_stops_without_advancing_unfinished_row(tmp_path: Path) -> None:
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "budget")
    store.enqueue_outbox(workspace, "budget", _drain_envelope(store, "budget", "x", 1))

    class SlowClient:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del body, deadline_ms
            await asyncio.sleep(1)

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return SlowClient()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="budget",
        connect=connect,  # type: ignore[arg-type]
        _state=tmp_path,
        budget_seconds=0.01,
    )
    assert store.list_pending_outbox_rows(workspace)[0].attempts == 0
    diagnostics = (tmp_path / "observation/hook-diagnostics.jsonl").read_text()
    assert '"reason":"drain_budget_exhausted"' in diagnostics


@pytest.mark.anyio
async def test_drain_probes_a_mapping_missing_session_once_per_pass(tmp_path: Path) -> None:
    """#211's recurrence tax: a dead-session backlog must not eat the drain budget.

    mapping_missing is session-scoped and cannot heal mid-pass, so one
    rejection retires the whole session for the rest of the pass while other
    sessions still deliver.
    """

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    for session, count in (("dead", 5), ("healthy", 2)):
        store.bind_codex_session(workspace, session)
        for ordinal in range(1, count + 1):
            store.enqueue_outbox(
                workspace, session, _drain_envelope(store, session, f"hook:{session}", ordinal)
            )

    attempts: list[str] = []

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del deadline_ms
            session = str(body["codex_session_id"])  # type: ignore[index]
            attempts.append(session)
            if session == "dead":
                return observation_ingest_result_to_json(
                    ObservationIngestResult(
                        ObservationIngestDisposition.REJECTED,
                        ObservationGapCode.MAPPING_MISSING.value,
                        None,
                    )
                )
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="dead",
        connect=connect,  # type: ignore[arg-type]
        _state=tmp_path,
    )
    assert attempts.count("dead") == 1, "a mapping_missing session must be probed once per pass"
    assert attempts.count("healthy") == 2, "healthy sessions must still deliver in the same pass"
    remaining = store.list_pending_outbox_rows(workspace)
    assert {row.codex_session_id for row in remaining} == {"dead"}
    assert len(remaining) == 5
    # Retired siblings carry the shared cause so `observe status` reports
    # mapping_missing=5, never a misleading not_attempted.
    assert all(row.last_reason == ObservationGapCode.MAPPING_MISSING.value for row in remaining)


@pytest.mark.anyio
async def test_drain_treats_service_unavailable_as_row_scoped_with_a_cap(tmp_path: Path) -> None:
    """One poisoned row must not wedge the workspace drain forever.

    service_unavailable is the catch-all for row-scoped failures (bundle
    contention, one malformed envelope), so healthy rows behind it still
    deliver; only a consecutive run of them yields the pass.
    """

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "mixed")
    for ordinal in range(1, 6):
        store.enqueue_outbox(
            workspace, "mixed", _drain_envelope(store, "mixed", "hook:mixed", ordinal)
        )
    poisoned = store.list_pending_outbox_rows(workspace)[0].envelope.source_identity

    attempts: list[str] = []

    class Client:
        async def observation_ingest(self, body: object, *, deadline_ms: int):
            del deadline_ms
            identity = str(body["envelope"]["source_identity"])  # type: ignore[index]
            attempts.append(identity)
            if identity == poisoned:
                return observation_ingest_result_to_json(
                    ObservationIngestResult(
                        ObservationIngestDisposition.REJECTED,
                        ObservationGapCode.SERVICE_UNAVAILABLE.value,
                        None,
                    )
                )
            return observation_ingest_result_to_json(
                ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
            )

        async def close(self) -> None:
            return None

    async def connect(_kind: object):
        return Client()

    await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
        store,
        workspace_commitment=workspace,
        codex_session_id="mixed",
        connect=connect,  # type: ignore[arg-type]
        _state=tmp_path,
    )
    assert len(attempts) == 5, "rows behind a service_unavailable row must still be attempted"
    remaining = store.list_pending_outbox_rows(workspace)
    assert [row.envelope.source_identity for row in remaining] == [poisoned]


@pytest.mark.anyio
async def test_drain_lease_prevents_concurrent_hooks_from_double_draining(
    tmp_path: Path,
) -> None:
    """#209 made hooks genuinely concurrent; only one may drain at a time."""

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "leased")
    store.enqueue_outbox(workspace, "leased", _drain_envelope(store, "leased", "x", 1))

    connects: list[object] = []

    async def recording_connect(kind: object):
        connects.append(kind)
        raise RuntimeError("offline")

    with store.drain_lease(workspace) as owned:
        assert owned is True
        # A second store instance (another hook process) must lose the lease
        # and skip the drain entirely — no connect, no diagnostics.
        other = LocalObservationStore(_state=tmp_path)
        await observe_hooks_module._drain_outbox(  # pyright: ignore[reportPrivateUsage]
            other,
            workspace_commitment=workspace,
            codex_session_id="leased",
            connect=recording_connect,
            _state=tmp_path,
        )
    assert connects == []
    assert not (tmp_path / "observation/hook-diagnostics.jsonl").exists()


def _populate_realistic_store(
    store: LocalObservationStore,
    workspace: str,
    session: str,
    *,
    envelopes: int = 250,
    pending: int = 60,
    quarantined: int = 199,
) -> None:
    """Grow one workspace state to the shape the 2026-08-12 regression ran at.

    Hook cost is store-size-dependent, so latency guards against a small
    fixture pass trivially (#209). The live store that measured 3.06-4.89s
    held 256 envelopes, 73 pending rows, and 199 quarantine entries in a
    ~384KB file; this builds the same order of magnitude in one save.
    """

    state = store._load(workspace)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert state.envelopes is not None
    assert state.pending_outbox is not None
    assert state.quarantine is not None
    assert state.dedup is not None
    from datetime import UTC, datetime

    from yoetz.adapters.integrations.observation_local import (
        ObservationOutboxRow,
        _dedup_key,  # pyright: ignore[reportPrivateUsage]
    )
    from yoetz.domain.values import timestamp_from_datetime

    quarantined_at = timestamp_from_datetime(datetime.now(UTC).replace(microsecond=0))
    for ordinal in range(1, envelopes + pending + quarantined + 1):
        envelope = _drain_envelope(store, session, f"hook:bulk:{ordinal}", ordinal)
        if ordinal <= envelopes:
            state.envelopes.append(envelope)
            state.dedup.add(_dedup_key(workspace, envelope))
        elif ordinal <= envelopes + pending:
            state.pending_outbox.append(
                ObservationOutboxRow(codex_session_id=session, envelope=envelope)
            )
        else:
            state.quarantine.append((session, envelope, "service_unavailable", quarantined_at))
    store._save(workspace, state)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


class _InstantAckClient:
    async def observation_ingest(self, body: object, *, deadline_ms: int):
        del body, deadline_ms
        # DUPLICATE routes to ACKNOWLEDGE without needing a service cursor.
        return observation_ingest_result_to_json(
            ObservationIngestResult(ObservationIngestDisposition.DUPLICATE, None, None)
        )

    async def close(self) -> None:
        return None


def test_hook_invocation_parses_the_state_file_once_not_seventeen_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#209: one hook process loads the store once and reuses the parse.

    Before the stat-validated parse cache, every store method re-read and
    re-parsed the whole workspace state file — 17 times per hook invocation
    against the live 384KB store.
    """

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "parse-count")
    _populate_realistic_store(store, workspace, "parse-count", pending=8, quarantined=40)

    parses = 0
    original = LocalObservationStore._state_from_json  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

    def counting(self: LocalObservationStore, raw: object):
        nonlocal parses
        parses += 1
        return original(self, raw)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(LocalObservationStore, "_state_from_json", counting)

    async def connect(_kind: object):
        return _InstantAckClient()

    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "parse-count",
                "tool_name": "shell",
                "correlation_id": "pc-1",
                "exit_status": 0,
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )
    assert code == 0
    assert parses == 1, (
        f"one hook invocation parsed the workspace state {parses} times; "
        "the per-instance parse cache is not being hit (33 parses measured "
        "with the cache neutered at this fixture shape)"
    )


def test_hook_wall_clock_meets_the_declared_timeout_on_a_realistic_store(
    tmp_path: Path,
) -> None:
    """#209's guard: hook wall time vs the timeout hooks.json declares.

    The 2026-08-12 regression measured 3.06-4.89s per hook at exactly this
    store shape against a declared 3s, and nothing went red because no test
    asserted wall clock at a realistic store size. The bound here is the
    historical 3s declaration — comfortably above the fixed cost (~0.3-0.6s)
    and comfortably below the pre-fix cost — not the new 10s declaration,
    which would have passed even while the bug was live.
    """

    import time as time_module

    from yoetz.adapters.integrations.codex_plugin import parse_hooks_json, render_plugin_tree

    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    store.bind_codex_session(workspace, "latency")
    _populate_realistic_store(store, workspace, "latency")
    state_file = next((tmp_path / "observation" / "workspaces").glob("*.json"))
    assert state_file.stat().st_size >= 250_000, (
        "latency guard must run against a realistically-sized store; "
        f"got {state_file.stat().st_size} bytes"
    )

    async def connect(_kind: object):
        return _InstantAckClient()

    started = time_module.monotonic()
    code = handle_observe(
        event_name="PostToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": "latency",
                "tool_name": "shell",
                "correlation_id": "lat-1",
                "exit_status": 0,
            }
        ).encode(),
        stdout=io.BytesIO(),
        workspace=str(tmp_path),
        _state=tmp_path,
        connect=connect,  # type: ignore[arg-type]
    )
    elapsed = time_module.monotonic() - started
    assert code == 0
    assert elapsed < 3.0, (
        f"hook invocation took {elapsed:.2f}s against a realistic store; "
        "the 2026-08-12 regression shape is back"
    )
    hooks = parse_hooks_json(render_plugin_tree()["hooks/hooks.json"])
    events = hooks["hooks"]
    declared = None
    for group in events["PostToolUse"]:  # type: ignore[index, call-overload]
        for handler in group["hooks"]:  # type: ignore[index, call-overload]
            if "observe" in str(handler["command"]):  # type: ignore[index]
                declared = handler["timeout"]  # type: ignore[index]
    assert isinstance(declared, int)
    assert elapsed < declared, "hook exceeded its own declared hooks.json timeout"
