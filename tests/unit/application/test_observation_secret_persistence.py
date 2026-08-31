"""Changed-file excerpts and approved-check output share the fail-closed scanner."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import apsw
import pytest

from yoetz.adapters.approved_checks import (
    ApprovedCheckApproval,
    ApprovedCheckCommand,
    ApprovedCheckRunner,
    approval_commitment,
)
from yoetz.adapters.integrations.observation_local import LocalObservationStore
from yoetz.adapters.sqlite.migrations import initialize_bundle
from yoetz.adapters.sqlite.observation import SqliteObservationStore
from yoetz.adapters.workspace_inspect import open_inspect_workspace
from yoetz.application.observation_coordinator import (
    ObservationCoordinator,
    build_inspection_excerpt_manifest,
)
from yoetz.application.observation_verification import ObservationVerificationJob
from yoetz.domain.observation import (
    ObservationContentChunk,
    ObservationContentKind,
    ObservationCursor,
    ObservationEnvelope,
    ObservationSource,
)
from yoetz.domain.values import JsonObject, Timestamp
from yoetz.observability.privacy import PersistenceScanResult
from yoetz.ports.check_sandbox import CheckSandboxLaunch, CheckSandboxStatus
from yoetz.ports.objects import ObjectMetadata, ObjectRef, ObjectSource
from yoetz.ports.workspace_inspect import InspectedArtifact
from yoetz.protocol.ids import PREFIX_BY_KIND, IdKind

_TASK = "tsk_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_DIGEST = "sha256:" + "ab" * 32
_COMMITMENT = "hmac-sha256:" + "cd" * 32
_WORKSPACE = "hmac-sha256:" + "11" * 32
_SECRET = b"AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
_AZURE = b"AZURE_CLIENT_SECRET=abc123secretvalue0001"
_TOKEN = b"GITHUB_TOKEN=notakeybutlongenoughvalue"


class _ReadySandbox:
    def prepare(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        deny_network: bool,
    ) -> CheckSandboxLaunch:
        del deny_network
        return CheckSandboxLaunch(
            argv=tuple(argv),
            env=dict(env),
            cwd=cwd,
            status=CheckSandboxStatus.READY,
            network_isolated=True,
        )


class _Clock:
    def now_utc(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


class _CapturingObjects:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        self.refs: dict[str, ObjectRef] = {}

    async def stage(self, source: ObjectSource, metadata: ObjectMetadata) -> ObjectMetadata:
        assert source.data is not None
        self.payloads.append(source.data)
        return metadata

    async def finalize(self, staged: ObjectMetadata) -> ObjectRef:
        payload = self.payloads[-1]
        ref = ObjectRef(
            PREFIX_BY_KIND[IdKind.OBJECT] + str(uuid.uuid4()),
            len(payload),
            _COMMITMENT,
            _DIGEST,
            "yoetz-object/1",
            "slot1",
            staged,
        )
        self.refs[ref.object_id] = ref
        return ref

    async def resolve_verified(self, object_id: str, envelope_digest: str) -> ObjectRef:
        ref = self.refs[object_id]
        assert ref.envelope_digest == envelope_digest
        return ref


def _artifact(relative_path: str, excerpt: bytes) -> InspectedArtifact:
    digest = "sha256:" + hashlib.sha256(excerpt).hexdigest()
    return InspectedArtifact(relative_path, digest, excerpt, False, len(excerpt))


def _job() -> ObservationVerificationJob:
    return ObservationVerificationJob(
        job_id="job_1",
        workspace_commitment=_WORKSPACE,
        policy_digest=_DIGEST,
        approval_commitment=_DIGEST,
        subject_state_digest=_DIGEST,
        state_token=1,
    )


def _coordinator(tmp_path: Path, objects: _CapturingObjects) -> ObservationCoordinator:
    return ObservationCoordinator(
        runtime=SimpleNamespace(task_id=_TASK, objects=objects),  # type: ignore[arg-type]
        local=LocalObservationStore(_state=tmp_path),
        clock=_Clock(),  # type: ignore[arg-type]
        ids=object(),  # type: ignore[arg-type]
        state_root=tmp_path,
    )


def _observation_envelope(*, refs: tuple[str, ...] = ()) -> ObservationEnvelope:
    return ObservationEnvelope(
        session_commitment=_COMMITMENT,
        event_kind="PostToolUse",
        source_identity="hook:issue-302",
        source=ObservationSource.CODEX_HOOK,
        cursor=ObservationCursor(1, 0, 1, _COMMITMENT, "codex-obs-hook/1.0.0"),
        receipt_time=Timestamp("2026-08-30T00:00:00.000Z"),
        structural_payload=JsonObject({"correlation_id": "call-302", "tool_name": "shell"}),
        content_object_refs=refs,
        gap_codes=(),
    )


@pytest.mark.anyio
async def test_observation_capture_binds_inner_bytes_and_deleted_object_weakens(
    tmp_path: Path,
) -> None:
    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "task_obs", "owner_generation": "1"})
    store = SqliteObservationStore(db)
    objects = _CapturingObjects()
    coordinator = _coordinator(tmp_path, objects)
    runtime = SimpleNamespace(task_id=_TASK, objects=objects)
    envelope = _observation_envelope()
    content = b"captured command output\n"
    chunk = ObservationContentChunk(
        ObservationContentKind.TOOL_OUTPUT,
        "call-302",
        _COMMITMENT,
        "text/plain",
        0,
        1,
        content,
    )

    manifests, redacted, unavailable = await coordinator._capture_content(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        runtime,  # type: ignore[arg-type]
        store,
        workspace=_WORKSPACE,
        envelope=envelope,
        chunks=(chunk,),
    )
    assert redacted is False
    assert unavailable is False
    assert len(manifests) == 1
    assert manifests[0].content_digest == "sha256:" + hashlib.sha256(content).hexdigest()
    assert manifests[0].content_bytes == len(content)

    objects.refs.pop(manifests[0].object_id)
    recovered, redacted, unavailable = await coordinator._capture_content(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        runtime,  # type: ignore[arg-type]
        store,
        workspace=_WORKSPACE,
        envelope=_observation_envelope(refs=(manifests[0].object_id,)),
        chunks=(),
    )
    assert recovered == ()
    assert redacted is False
    assert unavailable is True


@pytest.mark.anyio
async def test_observation_capture_secret_scans_before_digest_binding(tmp_path: Path) -> None:
    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "task_obs", "owner_generation": "1"})
    store = SqliteObservationStore(db)
    objects = _CapturingObjects()
    coordinator = _coordinator(tmp_path, objects)
    runtime = SimpleNamespace(task_id=_TASK, objects=objects)
    chunk = ObservationContentChunk(
        ObservationContentKind.TOOL_OUTPUT,
        "call-302",
        _COMMITMENT,
        "text/plain",
        0,
        1,
        b"prefix " + _SECRET,
    )

    manifests, redacted, unavailable = await coordinator._capture_content(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        runtime,  # type: ignore[arg-type]
        store,
        workspace=_WORKSPACE,
        envelope=_observation_envelope(),
        chunks=(chunk,),
    )
    assert redacted is True
    assert unavailable is False
    assert len(manifests) == 1
    captured_envelope = objects.payloads[0]
    assert _SECRET not in captured_envelope
    captured_inner = base64.b64decode(json.loads(captured_envelope)["content_b64"])
    assert _SECRET not in captured_inner
    assert manifests[0].redacted is True
    assert manifests[0].content_digest == ("sha256:" + hashlib.sha256(captured_inner).hexdigest())


def test_changed_file_prefix_canary_persists_only_redaction_metadata() -> None:
    secret_file = _artifact("src/env.py", b"export " + _SECRET + b"\nprint(1)\n")
    clean_file = _artifact("src/ok.py", b"print('hello-advice')\n")
    encoded, unavailable, redacted, truncated = build_inspection_excerpt_manifest(
        (secret_file, clean_file)
    )
    assert unavailable is False
    assert redacted is True
    assert truncated is False
    assert encoded is not None
    parsed = json.loads(encoded)
    artifacts = parsed["artifacts"]
    assert artifacts[0]["path"] == "src/env.py"
    assert artifacts[0]["redacted"] is True
    assert artifacts[0]["finding_kinds"] == ["credential_pattern"]
    assert "excerpt_b64" not in artifacts[0]
    assert artifacts[1]["redacted"] is False
    recovered = base64.b64decode(artifacts[1]["excerpt_b64"])
    assert b"hello-advice" in recovered
    assert _SECRET not in encoded
    assert b"wJalrXUtnFEMI" not in encoded


def test_changed_file_canary_withholds_excerpt_object() -> None:
    canary = b"unique-inspect-canary-\x00-secret"
    encoded, unavailable, redacted, truncated = build_inspection_excerpt_manifest(
        (_artifact("notes.txt", b"prefix " + canary),),
        canaries=(canary,),
    )
    assert encoded is None
    assert unavailable is True
    assert redacted is False
    assert truncated is False


def test_changed_file_scanner_failure_stores_no_excerpt(monkeypatch: pytest.MonkeyPatch) -> None:
    def _withhold(data: bytes, *, canaries: tuple[bytes, ...] = ()) -> PersistenceScanResult:
        del data, canaries
        return PersistenceScanResult(False, b"", True, ())

    monkeypatch.setattr(
        "yoetz.application.observation_coordinator.prepare_persisted_plaintext",
        _withhold,
    )
    encoded, unavailable, redacted, truncated = build_inspection_excerpt_manifest(
        (_artifact("notes.txt", b"ordinary excerpt bytes"),)
    )
    assert encoded is None
    assert unavailable is True
    assert redacted is False
    assert truncated is False


def test_azure_and_token_prefixes_are_classified_before_encoding() -> None:
    encoded, unavailable, redacted, truncated = build_inspection_excerpt_manifest(
        (_artifact("a.env", _AZURE + b"\n"), _artifact("b.env", _TOKEN + b"\n"))
    )
    assert unavailable is False
    assert redacted is True
    assert truncated is False
    assert encoded is not None
    assert _AZURE not in encoded
    assert _TOKEN not in encoded
    parsed = json.loads(encoded)
    for item in parsed["artifacts"]:
        assert item["redacted"] is True
        assert "excerpt_b64" not in item


@pytest.mark.anyio
async def test_approved_check_persistence_path_redacts_compound_secrets(
    tmp_path: Path,
) -> None:
    handle = open_inspect_workspace(tmp_path)
    captured: list[bytes] = []
    assignment = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    argv = ("/bin/echo", assignment)
    approval = ApprovedCheckApproval(
        approval_id="pytest-unit",
        argv=argv,
        allow_network=False,
        timeout_seconds=10.0,
        approval_commitment=approval_commitment("pytest-unit", argv, allow_network=False),
    )
    runner = ApprovedCheckRunner(
        {approval.approval_commitment: approval},
        sandbox=_ReadySandbox(),
        output_sink=captured.append,
    )
    runner.run(
        ApprovedCheckCommand(
            workspace=handle,
            approval=approval,
            subject_state_digest=_DIGEST,
        )
    )
    assert captured
    assert b"wJalrXUtnFEMI" not in captured[0]
    assert b"[REDACTED]" in captured[0]

    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "task_obs", "owner_generation": "1"})
    store = SqliteObservationStore(db)
    objects = _CapturingObjects()
    coordinator = _coordinator(tmp_path, objects)
    object_id = await coordinator._persist_approved_check_output(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        SimpleNamespace(task_id=_TASK, objects=objects),  # type: ignore[arg-type]
        store,
        _WORKSPACE,
        _job(),
        captured[0] + b"\n" + _AZURE,
    )
    assert object_id is not None
    assert objects.payloads
    payload = objects.payloads[0]
    assert b"wJalrXUtnFEMI" not in payload
    assert b"abc123secretvalue0001" not in payload
    parsed = json.loads(payload)
    decoded = base64.b64decode(parsed["content_b64"])
    assert b"[REDACTED]" in decoded
    assert parsed["redacted"] is True
    rows = db.execute(
        "SELECT redacted,content_kind,content_digest,content_bytes "
        "FROM observation_content_manifests"
    ).fetchall()
    assert rows == [
        (
            1,
            "approved_check_output",
            "sha256:" + hashlib.sha256(decoded).hexdigest(),
            len(decoded),
        )
    ]


@pytest.mark.anyio
async def test_approved_check_persistence_withholds_when_scan_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _withhold(data: bytes, *, canaries: tuple[bytes, ...] = ()) -> PersistenceScanResult:
        del data, canaries
        return PersistenceScanResult(False, b"", True, ("canary",))

    monkeypatch.setattr(
        "yoetz.application.observation_coordinator.prepare_persisted_plaintext",
        _withhold,
    )
    db = apsw.Connection(":memory:")
    initialize_bundle(db, {"task_id": "task_obs", "owner_generation": "1"})
    store = SqliteObservationStore(db)
    objects = _CapturingObjects()
    coordinator = _coordinator(tmp_path, objects)
    object_id = await coordinator._persist_approved_check_output(  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        SimpleNamespace(task_id=_TASK, objects=objects),  # type: ignore[arg-type]
        store,
        _WORKSPACE,
        _job(),
        b"AWS_SECRET_ACCESS_KEY=should-not-be-stored",
    )
    assert object_id is None
    assert objects.payloads == []
    assert db.execute("SELECT COUNT(*) FROM observation_content_manifests").fetchone() == (0,)
