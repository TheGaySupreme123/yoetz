"""Exact Codex app-server cell stays secret-free, context-free, and fail closed."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import pytest

from yoetz.adapters.providers import codex_app_server as module
from yoetz.adapters.providers.codex_app_server import (
    CODEX_APP_SERVER_SCHEMA_SHA256,
    CODEX_EVALUATOR_CAPABILITY_CELL_SHA256,
    CODEX_EVALUATOR_CAPABILITY_PROFILE,
    CODEX_EVALUATOR_CONFIG_SHA256,
    CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT,
    CodexAppServerEvaluator,
    CodexAppServerExternalFactory,
    CodexAppServerProfile,
    CodexRuntimeStatus,
)
from yoetz.adapters.providers.data_use_catalog import data_use_record_for_endpoint
from yoetz.domain.findings import SemanticFailureClass
from yoetz.domain.privacy import (
    ApprovedOutboundCase,
    DataCategory,
    ProviderBinding,
    RequestCommitment,
)
from yoetz.ports.secret_memory import ProviderAttemptAuthBinding
from yoetz.ports.semantic import (
    Deadline,
    ExternalRuntimeAuthority,
    SemanticResultInvalid,
    SemanticResultRefused,
    SemanticResultSuccess,
    SemanticResultUnavailable,
)
from yoetz.protocol.canonical import canonical_digest, canonical_encode

pytestmark = pytest.mark.anyio

_DIGEST = "sha256:" + "d" * 64
_COMMITMENT = "hmac-sha256:" + "a" * 64
_NOW = datetime(2026, 8, 30, tzinfo=UTC)


class _Clock:
    def now_utc(self) -> datetime:
        return _NOW

    def monotonic_seconds(self) -> float:
        return 1.0


def _profile() -> CodexAppServerProfile:
    return CodexAppServerProfile(
        provider_id="openai-codex",
        endpoint_profile_id="codex-chatgpt-subscription",
        endpoint_profile_version="1.0.0",
        executable_path=Path("/opt/codex/0.150.1/codex"),
        executable_sha256="sha256:" + "a" * 64,
        runtime_version="0.150.1",
        source_identity="openai-codex-darwin-arm64-0.150.1",
        app_server_schema_sha256=CODEX_APP_SERVER_SCHEMA_SHA256,
        capability_cell_sha256=CODEX_EVALUATOR_CAPABILITY_CELL_SHA256,
        capability_profile=CODEX_EVALUATOR_CAPABILITY_PROFILE,
        capability_evidence_expires_at=CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT,
        codex_home=Path("/var/lib/yoetz/codex-0.150.1"),
        isolated_config_sha256=CODEX_EVALUATOR_CONFIG_SHA256,
        model="gpt-5.6-sol",
        reasoning_effort="high",
        timeout_seconds=30,
        data_use_profile=data_use_record_for_endpoint("codex-chatgpt-subscription").profile,
    )


def test_committed_compatibility_cell_matches_runtime_constants() -> None:
    root = Path(__file__).resolve().parents[4]
    cell = json.loads((root / "support/codex-evaluator/0.150.1/cell.json").read_text("utf-8"))
    config = (root / "support/codex-evaluator/0.150.1/config.toml").read_bytes()

    assert cell["runtime_version"] == module.CODEX_EVALUATOR_RUNTIME_VERSION
    assert cell["app_server_schema_sha256"] == CODEX_APP_SERVER_SCHEMA_SHA256
    assert cell["capability_profile"] == CODEX_EVALUATOR_CAPABILITY_PROFILE
    assert cell["capability_cell_sha256"] == CODEX_EVALUATOR_CAPABILITY_CELL_SHA256
    assert cell["evidence_expires_at"] == CODEX_EVALUATOR_EVIDENCE_EXPIRES_AT
    identity_keys = (
        "schema",
        "runtime_version",
        "distribution_kind",
        "distribution",
        "platform",
        "protocol",
        "executable_sha256",
        "app_server_schema_sha256",
        "isolated_config_sha256",
        "capability_profile",
        "credential_authority",
        "upstream_body_observability",
        "evidence_reviewed_at",
        "evidence_expires_at",
    )
    assert canonical_digest({key: cell[key] for key in identity_keys}) == (
        CODEX_EVALUATOR_CAPABILITY_CELL_SHA256
    )
    assert cell["isolated_config_sha256"] == CODEX_EVALUATOR_CONFIG_SHA256
    assert "sha256:" + hashlib.sha256(config).hexdigest() == CODEX_EVALUATOR_CONFIG_SHA256
    assert config.decode("utf-8") == module.CODEX_EVALUATOR_CONFIG
    assert cell["upstream_body_observability"] == "unavailable"
    assert cell["release_evidence"] == "pending"


def test_codex_output_schema_omits_only_provider_rejected_uniqueness_keyword() -> None:
    def keys(value: object) -> set[str]:
        if type(value) is dict:
            source = cast(dict[str, object], value)
            result = set(source)
            for item in source.values():
                result.update(keys(item))
            return result
        if type(value) is list:
            result: set[str] = set()
            for item in cast(list[object], value):
                result.update(keys(item))
            return result
        return set()

    assert "uniqueItems" in keys(module.JUDGMENT_JSON_SCHEMA)
    assert "uniqueItems" not in keys(
        module._CODEX_JUDGMENT_JSON_SCHEMA  # pyright: ignore[reportPrivateUsage]
    )


def _case() -> ApprovedOutboundCase:
    payload = canonical_encode({"schema": "yoetz.semantic-check-candidate/1", "items": []})
    return ApprovedOutboundCase(
        case_id="cas_64000000-0000-4000-8000-000000000001",
        request_id="req_64000000-0000-4000-8000-000000000001",
        payload=payload,
        media_type="application/json",
        schema_id="yoetz-semantic-case-1.0.0",
        included_item_ids=("case-packet",),
        approved_categories=(DataCategory.BOUNDED_STRUCTURAL_METADATA,),
        blocked_categories=(),
        byte_count=len(payload),
        token_count=8,
        provider_binding=ProviderBinding(
            "openai-codex",
            "gpt-5.6-sol",
            "codex-chatgpt-subscription",
            "1.0.0",
            "external",
        ),
        purpose="semantic-review",
        authorization_id="aut_64000000-0000-4000-8000-000000000001",
        policy_digest=_DIGEST,
        case_digest="sha256:" + "c" * 64,
    )


def _attempt(
    case: ApprovedOutboundCase,
) -> tuple[ProviderAttemptAuthBinding, ExternalRuntimeAuthority]:
    body_digest = module._sha256_bytes(  # pyright: ignore[reportPrivateUsage]
        case.payload
    )
    binding = ProviderAttemptAuthBinding(
        provider_id="openai-codex",
        model_id="gpt-5.6-sol",
        endpoint_profile_id="codex-chatgpt-subscription",
        endpoint_profile_version="1.0.0",
        purpose="semantic-review",
        authorization_scope_digest=_DIGEST,
        purpose_digest=canonical_digest({"purpose": "semantic-review"}),
        dispatch_id="dsp_64000000-0000-4000-8000-000000000001",
        request_body_digest=body_digest,
        service_generation=1,
        monotonic_deadline=30.0,
    )
    return binding, ExternalRuntimeAuthority(
        dispatch_id=binding.dispatch_id,
        request_body_digest=body_digest,
        request_commitment=_COMMITMENT,
        service_generation=1,
        monotonic_deadline=30.0,
    )


class _Runtime:
    def __init__(
        self,
        profile: CodexAppServerProfile,
        *,
        model_available: bool = True,
        account: object = None,
        predisclosure_event: dict[str, object] | None = None,
    ) -> None:
        self.profile = profile
        self.workdir = Path("/private/empty-attempt")
        self.pending_notifications: list[dict[str, object]] = []
        self.model_available = model_available
        self.account = (
            {"type": "chatgpt", "email": "discard@example", "planType": "plus"}
            if account is None
            else account
        )
        self.predisclosure_event = predisclosure_event
        self.sent: list[dict[str, object]] = []
        self.methods: list[str] = []
        self.events: list[dict[str, object]] = [
            {
                "method": "item/completed",
                "params": {
                    "item": {
                        "type": "agentMessage",
                        "text": (
                            '{"conclusion":"no_material_discrepancy","reviewer_challenges":[]}'
                        ),
                    }
                },
            },
            {
                "method": "turn/completed",
                "params": {
                    "threadId": "019a0000-0000-7000-8000-000000000001",
                    "turn": {
                        "id": "019a0000-0000-7000-8000-000000000002",
                        "status": "completed",
                        "error": None,
                    },
                },
            },
        ]

    async def send(self, value: dict[str, object]) -> None:
        self.sent.append(value)

    async def request(
        self, request_id: int, method: str, params: object, timeout: float
    ) -> dict[str, object]:
        del request_id, params, timeout
        self.methods.append(method)
        if method == "initialize":
            return {
                "codexHome": str(self.profile.codex_home),
                "userAgent": "yoetz_semantic_evaluator/0.150.1",
            }
        if method == "account/read":
            return {"account": self.account}
        if method == "model/list":
            return {
                "data": (
                    [
                        {
                            "id": self.profile.model,
                            "supportedReasoningEfforts": [{"reasoningEffort": "high"}],
                        }
                    ]
                    if self.model_available
                    else []
                ),
                "nextCursor": None,
            }
        if method == "thread/start":
            if self.predisclosure_event is not None:
                self.pending_notifications.append(self.predisclosure_event)
            return {
                "thread": {
                    "id": "019a0000-0000-7000-8000-000000000001",
                    "ephemeral": True,
                    "path": None,
                    "cwd": str(self.workdir),
                },
                "cwd": str(self.workdir),
                "model": self.profile.model,
                "modelProvider": "openai",
                "sandbox": {"type": "readOnly", "networkAccess": False},
                "instructionSources": [],
            }
        if method == "turn/start":
            return {
                "turn": {
                    "id": "019a0000-0000-7000-8000-000000000002",
                    "status": "inProgress",
                }
            }
        raise AssertionError(method)

    async def read(self, timeout: float) -> dict[str, object]:
        del timeout
        return self.events.pop(0)


class _LoginRuntime:
    def __init__(
        self,
        profile: CodexAppServerProfile,
        events: list[object],
        *,
        account_results: list[Mapping[str, object]] | None = None,
        login_result: Mapping[str, object] | None = None,
        cancel_error: BaseException | None = None,
        block_read: bool = False,
    ) -> None:
        self.profile = profile
        self.workdir = Path("/private/empty-login-attempt")
        self.events = list(events)
        self.account_results: list[Mapping[str, object]] = list(
            account_results or [{"account": {"type": "chatgpt", "planType": "plus"}}]
        )
        self.login_result = login_result
        self.cancel_error = cancel_error
        self.block_read = block_read
        self.pending_notifications: list[dict[str, object]] = []
        self.methods: list[str] = []
        self.timeouts: list[float] = []
        self.read_timeouts: list[float] = []
        self.sent: list[dict[str, object]] = []
        self.read_started = asyncio.Event()
        self.release_read = asyncio.Event()

    async def send(self, value: dict[str, object]) -> None:
        self.sent.append(value)

    async def request(
        self, request_id: int, method: str, params: object, timeout: float
    ) -> Mapping[str, object]:
        del request_id
        self.methods.append(method)
        self.timeouts.append(timeout)
        if method == "initialize":
            return {
                "codexHome": str(self.profile.codex_home),
                "userAgent": "yoetz_semantic_evaluator/0.150.1",
            }
        if method == "account/login/start":
            assert isinstance(params, dict)
            if self.login_result is not None:
                return self.login_result
            if params["type"] == "chatgpt":
                return {
                    "type": "chatgpt",
                    "loginId": "login-1",
                    "authUrl": "https://chatgpt.com/auth",
                }
            return {
                "type": "chatgptDeviceCode",
                "loginId": "login-1",
                "verificationUrl": "https://auth.openai.com/codex/device",
                "userCode": "ABCD-1234",
            }
        if method == "account/login/cancel":
            if self.cancel_error is not None:
                raise self.cancel_error
            return {}
        if method == "account/read":
            result = self.account_results.pop(0)
            if self.account_results:
                self.pending_notifications.append({"method": "account/updated", "params": {}})
            return result
        if method == "model/list":
            return {
                "data": [
                    {
                        "id": self.profile.model,
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": self.profile.reasoning_effort}
                        ],
                    }
                ],
                "nextCursor": None,
            }
        raise AssertionError(method)

    async def read(self, timeout: float) -> dict[str, object]:
        self.timeouts.append(timeout)
        self.read_timeouts.append(timeout)
        self.read_started.set()
        if self.block_read:
            await self.release_read.wait()
        if not self.events:
            raise TimeoutError
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        assert isinstance(event, dict)
        return cast(dict[str, object], event)


async def _evaluate(
    monkeypatch: pytest.MonkeyPatch,
    runtime: _Runtime,
    *,
    cleanup_outcome: str = "terminated",
) -> SemanticResultSuccess | SemanticResultUnavailable | SemanticResultInvalid:
    async def launch(profile: CodexAppServerProfile) -> _Runtime:
        assert profile is runtime.profile
        return runtime

    async def cleanup(value: object) -> str:
        assert value is runtime
        return cleanup_outcome

    monkeypatch.setattr(module, "_launch", launch)
    monkeypatch.setattr(module, "_cleanup", cleanup)
    case = _case()
    binding, authority = _attempt(case)
    evaluator = CodexAppServerEvaluator(runtime.profile, binding, authority, _Clock())
    return cast(
        SemanticResultSuccess | SemanticResultUnavailable | SemanticResultInvalid,
        await evaluator.evaluate(case, Deadline(_NOW + timedelta(seconds=30), 30.0)),
    )


async def _login(
    monkeypatch: pytest.MonkeyPatch,
    runtime: _LoginRuntime,
    *,
    mode: str = "browser",
) -> CodexRuntimeStatus:
    async def launch(profile: CodexAppServerProfile) -> _LoginRuntime:
        assert profile is runtime.profile
        return runtime

    async def cleanup(value: object) -> str:
        assert value is runtime
        return "terminated"

    monkeypatch.setattr(module, "_launch", launch)
    monkeypatch.setattr(module, "_cleanup", cleanup)
    return await module.codex_login(
        runtime.profile,
        mode=cast("Literal['browser', 'device_code']", mode),
        present_challenge=lambda _challenge: None,
    )


@pytest.mark.parametrize(
    ("mode", "expected_timeout"),
    [("browser", 600.0), ("device_code", 900.0)],
)
async def test_login_uses_the_native_method_window(
    monkeypatch: pytest.MonkeyPatch, mode: str, expected_timeout: float
) -> None:
    runtime = _LoginRuntime(
        _profile(),
        [
            {
                "method": "account/login/completed",
                "params": {"loginId": "login-1", "success": True},
            }
        ],
    )

    result = await _login(monkeypatch, runtime, mode=mode)

    assert result.auth_mode == "chatgpt"
    assert result.model_available is True
    assert any(timeout >= expected_timeout - 0.1 for timeout in runtime.timeouts)
    assert runtime.methods.count("account/login/cancel") == 0


async def test_login_accepts_terminal_event_in_bounded_grace_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _LoginRuntime(
        _profile(),
        [
            TimeoutError(),
            {
                "method": "account/login/completed",
                "params": {"loginId": "login-1", "success": True},
            },
        ],
    )

    result = await _login(monkeypatch, runtime)

    assert result.auth_mode == "chatgpt"
    assert runtime.methods.count("account/login/cancel") == 0
    assert runtime.read_timeouts[-1] <= module._LOGIN_TERMINAL_GRACE_SECONDS  # pyright: ignore[reportPrivateUsage]


async def test_login_timeout_cancels_native_flow_without_masking_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _LoginRuntime(
        _profile(),
        [TimeoutError(), TimeoutError()],
        cancel_error=RuntimeError("native detail must not escape"),
    )

    with pytest.raises(TimeoutError):
        await _login(monkeypatch, runtime)

    assert runtime.methods.count("account/login/cancel") == 1


async def test_login_invalid_terminal_cancels_native_flow_without_masking_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _LoginRuntime(
        _profile(),
        [{"method": "unexpected/notification", "params": {}}],
    )

    with pytest.raises(ValueError, match="codex_login_event_invalid"):
        await _login(monkeypatch, runtime)

    assert runtime.methods.count("account/login/cancel") == 1


@pytest.mark.parametrize("returned_id", [None, 5, "login-other"])
async def test_login_rejects_unbound_or_mismatched_completion(
    monkeypatch: pytest.MonkeyPatch, returned_id: object
) -> None:
    runtime = _LoginRuntime(
        _profile(),
        [
            {
                "method": "account/login/completed",
                "params": {"loginId": returned_id, "success": True},
            }
        ],
    )

    with pytest.raises(ValueError, match="codex_login_event_invalid"):
        await _login(monkeypatch, runtime)

    assert runtime.methods.count("account/login/cancel") == 1


async def test_login_rejects_challenge_mode_different_from_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _LoginRuntime(
        _profile(),
        [],
        login_result={
            "type": "chatgptDeviceCode",
            "loginId": "login-1",
            "verificationUrl": "https://auth.openai.com/codex/device",
            "userCode": "ABCD-1234",
        },
    )

    with pytest.raises(ValueError, match="codex_login_response_invalid"):
        await _login(monkeypatch, runtime, mode="browser")

    assert runtime.methods.count("account/login/cancel") == 1


async def test_login_waits_for_account_projection_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _LoginRuntime(
        _profile(),
        [
            {
                "method": "account/login/completed",
                "params": {"loginId": "login-1", "success": True},
            }
        ],
        account_results=[
            {"account": None},
            {"account": {"type": "chatgpt", "planType": "plus"}},
        ],
    )

    result = await _login(monkeypatch, runtime)

    assert result.auth_mode == "chatgpt"
    assert runtime.methods.count("account/read") == 2
    assert runtime.methods.count("account/login/cancel") == 0


async def test_login_preserves_cancellation_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _LoginRuntime(_profile(), [], block_read=True)
    cleaned = False

    async def launch(profile: CodexAppServerProfile) -> _LoginRuntime:
        assert profile is runtime.profile
        return runtime

    async def cleanup(value: object) -> str:
        nonlocal cleaned
        assert value is runtime
        cleaned = True
        return "terminated"

    monkeypatch.setattr(module, "_launch", launch)
    monkeypatch.setattr(module, "_cleanup", cleanup)
    task = asyncio.create_task(
        module.codex_login(
            runtime.profile,
            mode="browser",
            present_challenge=lambda _challenge: None,
        )
    )
    await runtime.read_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned is True
    assert runtime.methods.count("account/login/cancel") == 1


async def test_login_repeated_cancellation_cannot_interrupt_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _LoginRuntime(_profile(), [], block_read=True)
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleaned = False

    async def launch(profile: CodexAppServerProfile) -> _LoginRuntime:
        assert profile is runtime.profile
        return runtime

    async def cleanup(value: object) -> str:
        nonlocal cleaned
        assert value is runtime
        cleanup_started.set()
        await release_cleanup.wait()
        cleaned = True
        return "terminated"

    monkeypatch.setattr(module, "_launch", launch)
    monkeypatch.setattr(module, "_cleanup", cleanup)
    task = asyncio.create_task(
        module.codex_login(
            runtime.profile,
            mode="browser",
            present_challenge=lambda _challenge: None,
        )
    )
    await runtime.read_started.wait()
    task.cancel()
    await cleanup_started.wait()
    task.cancel()
    release_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned is True


async def test_launch_failure_removes_the_private_attempt_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = replace(_profile(), codex_home=tmp_path / "codex-home")

    def allow_local_binding(_profile: CodexAppServerProfile) -> None:
        return None

    def allow_private_bundle(_path: Path) -> None:
        return None

    monkeypatch.setattr(module.CodexAppServerProfile, "verify_local_binding", allow_local_binding)
    monkeypatch.setattr(module, "verify_private_local_bundle", allow_private_bundle)

    async def fail_launch(*_args: object, **_kwargs: object) -> None:
        raise OSError("closed launch failure")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_launch)

    with pytest.raises(OSError, match="closed launch failure"):
        await module._launch(profile)  # pyright: ignore[reportPrivateUsage]

    runtime_root = profile.codex_home / "runtime"
    assert runtime_root.is_dir()
    assert list(runtime_root.glob("attempt-*")) == []


async def test_launch_cancellation_cleans_a_process_returned_after_cancellation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = replace(_profile(), codex_home=tmp_path / "codex-home")
    spawn_started = asyncio.Event()
    release_spawn = asyncio.Event()

    class FakeProcess:
        pid = 991_526
        returncode: int | None = None
        stderr = None
        wait_calls = 0

        async def wait(self) -> int:
            self.wait_calls += 1
            self.returncode = 0
            return 0

    process = FakeProcess()

    def allow_local_binding(_profile: CodexAppServerProfile) -> None:
        return None

    def allow_private_bundle(_path: Path) -> None:
        return None

    async def delayed_spawn(*_args: object, **_kwargs: object) -> asyncio.subprocess.Process:
        spawn_started.set()
        await release_spawn.wait()
        return cast(asyncio.subprocess.Process, process)

    def absent_group(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(module.CodexAppServerProfile, "verify_local_binding", allow_local_binding)
    monkeypatch.setattr(module, "verify_private_local_bundle", allow_private_bundle)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    monkeypatch.setattr(module.os, "killpg", absent_group)

    launch_task = asyncio.create_task(module._launch(profile))  # pyright: ignore[reportPrivateUsage]
    await spawn_started.wait()
    launch_task.cancel()
    release_spawn.set()

    with pytest.raises(asyncio.CancelledError):
        await launch_task

    assert process.wait_calls == 1
    assert list((profile.codex_home / "runtime").glob("attempt-*")) == []


async def test_cleanup_reaps_child_after_process_group_signal_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = replace(_profile(), codex_home=tmp_path / "codex-home")
    workdir = profile.codex_home / "runtime" / "attempt-race"
    workdir.mkdir(parents=True)

    class FakeProcess:
        pid = 991_525
        returncode: int | None = None
        wait_calls = 0

        async def wait(self) -> int:
            self.wait_calls += 1
            self.returncode = 0
            return 0

    async def stderr_drain() -> bool:
        await asyncio.Event().wait()
        return False

    process = FakeProcess()
    runtime = module._CodexProcess(  # pyright: ignore[reportPrivateUsage]
        profile=profile,
        process=cast(asyncio.subprocess.Process, process),
        workdir=workdir,
        stderr_task=asyncio.create_task(stderr_drain()),
        pending_notifications=[],
    )
    probes = 0

    def racing_killpg(_pid: int, sig: int) -> None:
        nonlocal probes
        if sig == 0:
            probes += 1
            if probes == 1:
                return
        raise ProcessLookupError

    monkeypatch.setattr(module.os, "killpg", racing_killpg)

    result = await module._cleanup(runtime)  # pyright: ignore[reportPrivateUsage]

    assert result == "terminated"
    assert process.wait_calls == 1
    assert process.returncode == 0
    assert not workdir.exists()


async def test_cleanup_reports_failed_when_process_disappears_without_reap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profile = replace(_profile(), codex_home=tmp_path / "codex-home")
    workdir = profile.codex_home / "runtime" / "attempt-unreaped"
    workdir.mkdir(parents=True)

    class FakeProcess:
        pid = 991_527
        returncode: int | None = None

        async def wait(self) -> int:
            raise ProcessLookupError

    async def stderr_drain() -> bool:
        await asyncio.Event().wait()
        return False

    runtime = module._CodexProcess(  # pyright: ignore[reportPrivateUsage]
        profile=profile,
        process=cast(asyncio.subprocess.Process, FakeProcess()),
        workdir=workdir,
        stderr_task=asyncio.create_task(stderr_drain()),
        pending_notifications=[],
    )

    def absent_group(_pid: int, _sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(module.os, "killpg", absent_group)

    result = await module._cleanup(runtime)  # pyright: ignore[reportPrivateUsage]

    assert result == "failed"
    assert not workdir.exists()


async def test_success_records_weaker_runtime_boundary_without_identity_or_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(_profile())
    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultSuccess
    assert runtime.methods[:3] == ["initialize", "account/read", "model/list"]
    evidence = result.provenance.runtime_evidence
    assert evidence is not None
    assert evidence.credential_authority == "external_runtime_oauth"
    assert evidence.auth_mode == "chatgpt"
    assert evidence.plan_type == "plus"
    assert evidence.case_disclosed is True
    assert evidence.turn_acknowledged is True
    assert evidence.upstream_body_observability == "unavailable"
    assert evidence.final_output_sha256 is not None
    assert "discard@example" not in repr(result)
    assert "no_material_discrepancy" not in repr(evidence)


async def test_missing_model_fails_before_case_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _evaluate(monkeypatch, _Runtime(_profile(), model_available=False))

    assert type(result) is SemanticResultUnavailable
    evidence = result.provenance.runtime_evidence
    assert evidence is not None
    assert evidence.case_disclosed is False
    assert evidence.turn_acknowledged is False
    assert evidence.thread_id is None


@pytest.mark.parametrize("account", [False, {"type": "apiKey"}])
async def test_missing_or_wrong_login_fails_before_case_disclosure(
    monkeypatch: pytest.MonkeyPatch, account: object
) -> None:
    runtime = _Runtime(_profile(), account=None if account is False else account)
    if account is False:
        runtime.account = None

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.failure_class is SemanticFailureClass.AUTHENTICATION
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.case_disclosed is False
    assert all(item.get("method") != "turn/start" for item in runtime.sent)


async def test_stale_capability_evidence_fails_before_child_launch_or_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()

    class StaleClock(_Clock):
        def now_utc(self) -> datetime:
            return datetime(2026, 11, 30, tzinfo=UTC)

    async def forbidden_launch(_profile: object) -> object:
        pytest.fail("stale evidence must fail before child launch")

    monkeypatch.setattr(module, "_launch", forbidden_launch)
    case = _case()
    binding, authority = _attempt(case)
    result = await CodexAppServerEvaluator(profile, binding, authority, StaleClock()).evaluate(
        case, Deadline(_NOW + timedelta(seconds=30), 30.0)
    )

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.failure_class is SemanticFailureClass.UNSUPPORTED_PROFILE
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.case_disclosed is False
    assert result.provenance.runtime_evidence.process_cleanup == "not_started"


async def test_unknown_predisclosure_event_prevents_case_bytes_crossing_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(
        _profile(),
        predisclosure_event={"method": "config/warning", "params": {}},
    )

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.case_disclosed is False
    assert result.provenance.runtime_evidence.turn_acknowledged is False
    assert all(method != "turn/start" for method in (item.get("method") for item in runtime.sent))


async def test_exact_disabled_remote_control_notice_is_discarded_before_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(
        _profile(),
        predisclosure_event={
            "method": "remoteControl/status/changed",
            "params": {
                "environmentId": None,
                "installationId": "discard-installation-canary",
                "serverName": "discard-server-canary",
                "status": "disabled",
            },
        },
    )

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultSuccess
    assert "discard-installation-canary" not in repr(result)
    assert "discard-server-canary" not in repr(result)


@pytest.mark.parametrize("status", ["enabled", "connecting", None])
async def test_remote_control_notice_must_be_exactly_disabled_before_disclosure(
    monkeypatch: pytest.MonkeyPatch, status: object
) -> None:
    runtime = _Runtime(
        _profile(),
        predisclosure_event={
            "method": "remoteControl/status/changed",
            "params": {
                "environmentId": None,
                "installationId": "discarded",
                "serverName": "discarded",
                "status": status,
            },
        },
    )

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.case_disclosed is False


async def test_tool_event_is_rejected_after_ack_with_honest_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(_profile())
    runtime.events[0] = {
        "method": "item/completed",
        "params": {"item": {"type": "commandExecution", "text": "never-retained"}},
    }

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultInvalid
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.turn_acknowledged is True
    assert result.provenance.runtime_evidence.process_cleanup == "terminated"
    assert "never-retained" not in repr(result)


async def test_exact_rate_limit_update_is_discarded_after_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(_profile())
    runtime.events.insert(
        0,
        {
            "method": "account/rateLimits/updated",
            "params": {
                "rateLimits": {
                    "limitId": "codex",
                    "limitName": None,
                    "primary": {
                        "usedPercent": 27,
                        "windowDurationMins": 10080,
                        "resetsAt": 1788698233,
                    },
                    "secondary": None,
                    "credits": {
                        "hasCredits": False,
                        "unlimited": False,
                        "balance": "discard-balance-canary",
                    },
                    "individualLimit": None,
                    "spendControlReached": None,
                    "planType": "prolite",
                    "rateLimitReachedType": None,
                }
            },
            "emittedAtMs": 1788101632944,
        },
    )

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultSuccess
    assert "discard-balance-canary" not in repr(result)


async def test_native_warning_after_ack_is_transport_unknown_without_message_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(_profile())
    runtime.events[0] = {
        "method": "warning",
        "params": {
            "threadId": "019a0000-0000-7000-8000-000000000001",
            "message": "discard-native-warning-canary",
        },
        "emittedAtMs": 1788101617712,
    }

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.failure_class is SemanticFailureClass.TRANSPORT
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.turn_acknowledged is True
    assert "discard-native-warning-canary" not in repr(result)


async def test_unexpected_native_failure_stays_runtime_bounded_before_disclosure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_model_check(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("discard-unexpected-native-canary")

    monkeypatch.setattr(module, "_require_model", fail_model_check)
    result = await _evaluate(monkeypatch, _Runtime(_profile()))

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.failure_class is SemanticFailureClass.TRANSPORT
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.case_disclosed is False
    assert result.provenance.runtime_evidence.turn_acknowledged is False
    assert "discard-unexpected-native-canary" not in repr(result)


async def test_successful_output_with_unconfirmed_cleanup_is_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _evaluate(
        monkeypatch,
        _Runtime(_profile()),
        cleanup_outcome="failed",
    )

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.failure_class is SemanticFailureClass.TRANSPORT
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.turn_acknowledged is True
    assert result.provenance.runtime_evidence.process_cleanup == "failed"


async def test_structured_rate_limit_is_bounded_without_retaining_native_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(_profile())
    runtime.events[0] = {
        "method": "error",
        "params": {
            "error": {
                "message": "account and native details must not survive",
                "codexErrorInfo": {"responseStreamConnectionFailed": {"httpStatusCode": 429}},
            }
        },
    }

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.failure_class is SemanticFailureClass.RATE_LIMITED
    assert "account and native details" not in repr(result)


def test_structured_turn_failures_classify_quota_auth_and_policy_without_message_text() -> None:
    quota = module._turn_failure(  # pyright: ignore[reportPrivateUsage]
        {"message": "private", "codexErrorInfo": "usageLimitExceeded"}
    )
    auth = module._turn_failure(  # pyright: ignore[reportPrivateUsage]
        {"message": "private", "codexErrorInfo": "unauthorized"}
    )
    policy = module._turn_failure(  # pyright: ignore[reportPrivateUsage]
        {"message": "private", "codexErrorInfo": "cyberPolicy"}
    )

    assert quota.failure_class is SemanticFailureClass.QUOTA_EXHAUSTED
    assert auth.failure_class is SemanticFailureClass.AUTHENTICATION
    assert policy.outcome == "refused"
    assert "private" not in repr((quota, auth, policy))


def test_launcher_and_environment_do_not_inherit_aliases_credentials_or_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile()
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross")
    monkeypatch.setenv("HTTPS_PROXY", "https://must-not-cross")

    environment = module._process_environment(  # pyright: ignore[reportPrivateUsage]
        profile
    )

    assert profile.launcher_argv[:3] == (
        "/opt/codex/0.150.1/codex",
        "app-server",
        "--stdio",
    )
    assert environment["CODEX_HOME"] == str(profile.codex_home)
    assert "OPENAI_API_KEY" not in environment
    assert "HTTPS_PROXY" not in environment
    assert "HOME" not in environment


def test_factory_binds_every_runtime_authority_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def binding_is_valid(_profile: CodexAppServerProfile) -> None:
        return None

    monkeypatch.setattr(CodexAppServerProfile, "verify_local_binding", binding_is_valid)
    case = _case()
    binding, authority = _attempt(case)
    factory = CodexAppServerExternalFactory(_profile(), _Clock())
    factory.render(case)
    commitment = RequestCommitment("hmac-sha256/yoetz-privacy-egress-request-v1", _COMMITMENT)

    evaluator = factory.build_evaluator(binding, authority, commitment)

    assert type(evaluator) is CodexAppServerEvaluator
    for changed in (
        replace(authority, dispatch_id="dsp_64000000-0000-4000-8000-000000000002"),
        replace(authority, service_generation=2),
        replace(authority, monotonic_deadline=31.0),
        replace(authority, request_commitment="hmac-sha256:" + "b" * 64),
    ):
        with pytest.raises(ValueError, match="codex_runtime_factory_render_required"):
            factory.build_evaluator(binding, changed, commitment)


async def test_evaluator_rejects_deadline_not_bound_to_runtime_authority() -> None:
    case = _case()
    binding, authority = _attempt(case)
    evaluator = CodexAppServerEvaluator(_profile(), binding, authority, _Clock())

    with pytest.raises(ValueError, match="codex_runtime_attempt_binding_invalid"):
        await evaluator.evaluate(case, Deadline(_NOW + timedelta(seconds=30), 31.0))


# --- issue #527: bounded validation-stage diagnostics -------------------------------------------

_REF = "clm_20000000-0000-4000-8000-000000000001"
_NO_DISCREPANCY = '{"conclusion":"no_material_discrepancy","reviewer_challenges":[]}'


def _challenge_json(**overrides: object) -> str:
    challenge: dict[str, object] = {
        "finding_kind": "claim_without_admissible_evidence",
        "summary": "Evidence gap",
        "cited_refs": [_REF],
        "discrepancy": "The claim lacks a recorded basis.",
        "alternative_interpretation": "The claim may remain unresolved.",
        "message_to_main_agent": "Main agent: provide evidence for the claim.",
        "requested_next_step": "provide_evidence",
        "uncertainty": "The missing material may exist outside the case.",
    }
    challenge.update(overrides)
    return json.dumps({"conclusion": "challenges_returned", "reviewer_challenges": [challenge]})


def _agent_message(text: str, *, phase: str | None = None) -> dict[str, object]:
    item: dict[str, object] = {"type": "agentMessage", "text": text}
    if phase is not None:
        item["phase"] = phase
    return {"method": "item/completed", "params": {"item": item}}


def _runtime_with_output(*outputs: dict[str, object]) -> _Runtime:
    runtime = _Runtime(_profile())
    completion = runtime.events[-1]
    runtime.events = [*outputs, completion]
    return runtime


async def test_commentary_phase_is_discarded_and_final_answer_is_the_judgment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_with_output(
        _agent_message("narration-canary: reviewing the packet now", phase="commentary"),
        _agent_message(_NO_DISCREPANCY, phase="final_answer"),
    )

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultSuccess
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.failure_stage is None
    assert "narration-canary" not in repr(result)


async def test_commentary_alone_never_counts_as_a_judgment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_with_output(_agent_message(_NO_DISCREPANCY, phase="commentary"))

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultInvalid
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.failure_stage == "agent_message_count"


@pytest.mark.parametrize("phase", ["final_answer", None])
async def test_two_candidate_messages_report_the_count_stage(
    monkeypatch: pytest.MonkeyPatch, phase: str | None
) -> None:
    runtime = _runtime_with_output(
        _agent_message(_NO_DISCREPANCY, phase=phase),
        _agent_message(_NO_DISCREPANCY, phase=phase),
    )

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultInvalid
    assert result.provenance.failure_class is SemanticFailureClass.RESPONSE_SCHEMA
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.failure_stage == "agent_message_count"


async def test_unknown_message_phase_is_a_forbidden_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime_with_output(_agent_message(_NO_DISCREPANCY, phase="draft"))

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultInvalid
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.failure_stage == "event_forbidden"


@pytest.mark.parametrize(
    ("text", "stage"),
    [
        ("```json\n" + _NO_DISCREPANCY + "\n```", "output_not_json"),
        ("Here is my review-canary: " + _NO_DISCREPANCY, "output_not_json"),
        ('{"conclusion":"no_material_discrepancy","reviewer_challenges":[],}', "output_not_json"),
        ('{"result":' + _NO_DISCREPANCY + "}", "judgment_envelope_invalid"),
        ('{"judgment":"' + "prose-canary" + '"}', "judgment_envelope_invalid"),
        ('{"conclusion":"maybe-canary","reviewer_challenges":[]}', "judgment_enum_invalid"),
        (_challenge_json(finding_kind="hunch-canary"), "judgment_enum_invalid"),
        (_challenge_json(requested_next_step="ask-canary"), "judgment_enum_invalid"),
        (_challenge_json(cited_refs=[_REF, _REF]), "judgment_refs_duplicate"),
        (_challenge_json(cited_refs=["item-canary"]), "judgment_refs_invalid"),
        (
            '{"conclusion":"challenges_returned","reviewer_challenges":[]}',
            "judgment_conclusion_mismatch",
        ),
        (
            json.dumps(
                {
                    "conclusion": "no_material_discrepancy",
                    "reviewer_challenges": json.loads(_challenge_json())["reviewer_challenges"],
                }
            ),
            "judgment_conclusion_mismatch",
        ),
        (_challenge_json(summary=""), "judgment_text_bounds"),
        (_challenge_json(summary="canary " * 2000), "judgment_text_bounds"),
        (_challenge_json(extra="field-canary"), "judgment_shape_invalid"),
    ],
)
async def test_invalid_final_answers_name_one_closed_validation_stage(
    monkeypatch: pytest.MonkeyPatch, text: str, stage: str
) -> None:
    runtime = _runtime_with_output(_agent_message(text, phase="final_answer"))

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultInvalid
    assert result.provenance.status is module.SemanticStatus.INVALID
    assert result.provenance.failure_class is SemanticFailureClass.RESPONSE_SCHEMA
    assert result.raw_size == len(text.encode("utf-8"))
    evidence = result.provenance.runtime_evidence
    assert evidence is not None
    assert evidence.failure_stage == stage
    assert evidence.turn_acknowledged is True
    assert evidence.final_output_sha256 == module._sha256_bytes(  # pyright: ignore[reportPrivateUsage]
        text.encode("utf-8")
    )
    assert "canary" not in repr(result)


@pytest.mark.parametrize(
    "text",
    [_NO_DISCREPANCY, '{"judgment":' + _NO_DISCREPANCY + "}", _challenge_json()],
)
async def test_valid_bare_and_enveloped_final_answers_succeed(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    runtime = _runtime_with_output(_agent_message(text, phase="final_answer"))

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultSuccess
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.failure_stage is None


async def test_informational_notifications_and_plan_items_are_discarded_unread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(_profile())
    thread = "019a0000-0000-7000-8000-000000000001"
    runtime.events[0:0] = [
        {"method": "thread/name/updated", "params": {"threadId": thread, "threadName": "canary"}},
        {"method": "turn/moderationMetadata", "params": {"metadata": {"note": "canary"}}},
        {"method": "model/safetyBuffering/updated", "params": {"threadId": thread}},
        {"method": "deprecationNotice", "params": {"message": "canary"}},
        {"method": "configWarning", "params": {"message": "canary"}},
        {"method": "thread/queue/changed", "params": {"threadId": thread}},
        {"method": "thread/compacted", "params": {"threadId": thread}},
        {"method": "item/started", "params": {"item": {"type": "plan", "text": "canary"}}},
        {"method": "item/plan/delta", "params": {"delta": "canary"}},
        {"method": "turn/plan/updated", "params": {"plan": [{"step": "canary"}]}},
        {"method": "item/completed", "params": {"item": {"type": "plan", "text": "canary"}}},
        {"method": "item/started", "params": {"item": {"type": "contextCompaction"}}},
        {"method": "item/completed", "params": {"item": {"type": "contextCompaction"}}},
        {"method": "item/agentMessage/delta", "params": {"delta": "canary"}},
    ]

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultSuccess
    assert "canary" not in repr(result)


async def test_model_reroute_ends_the_turn_as_refused_with_its_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(_profile())
    runtime.events.insert(
        0,
        {
            "method": "model/rerouted",
            "params": {
                "fromModel": "gpt-5.6-sol",
                "toModel": "other-canary",
                "reason": "highRiskCyberActivity",
                "threadId": "019a0000-0000-7000-8000-000000000001",
                "turnId": "019a0000-0000-7000-8000-000000000002",
            },
        },
    )

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultRefused
    assert result.provenance.failure_class is SemanticFailureClass.AUTHORIZATION
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.failure_stage == "model_rerouted"
    assert "other-canary" not in repr(result)


async def test_delta_storm_is_bounded_by_the_event_limit_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(_profile())
    delta: dict[str, object] = {"method": "item/agentMessage/delta", "params": {"delta": "x"}}
    runtime.events[0:0] = [
        delta
        for _ in range(module._MAX_EVENT_COUNT)  # pyright: ignore[reportPrivateUsage]
    ]

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultInvalid
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.failure_stage == "event_limit"


async def test_large_streamed_judgment_fits_the_event_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(_profile())
    delta: dict[str, object] = {"method": "item/agentMessage/delta", "params": {"delta": "x"}}
    runtime.events[0:0] = [delta for _ in range(2000)]

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultSuccess


async def test_tool_event_and_unconfirmed_cleanup_name_their_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_runtime = _Runtime(_profile())
    tool_runtime.events[0] = {
        "method": "item/completed",
        "params": {"item": {"type": "commandExecution", "text": "never-retained"}},
    }
    tool = await _evaluate(monkeypatch, tool_runtime)
    assert type(tool) is SemanticResultInvalid
    assert tool.provenance.runtime_evidence is not None
    assert tool.provenance.runtime_evidence.failure_stage == "tool_event_forbidden"

    unconfirmed = await _evaluate(monkeypatch, _Runtime(_profile()), cleanup_outcome="failed")
    assert type(unconfirmed) is SemanticResultUnavailable
    assert unconfirmed.provenance.runtime_evidence is not None
    assert unconfirmed.provenance.runtime_evidence.failure_stage == "cleanup_unconfirmed"


async def test_post_ack_read_timeout_names_the_deadline_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime(_profile())

    async def stalled_read(timeout: float) -> dict[str, object]:
        del timeout
        raise TimeoutError

    runtime.read = stalled_read  # type: ignore[method-assign]

    result = await _evaluate(monkeypatch, runtime)

    assert type(result) is SemanticResultUnavailable
    assert result.provenance.failure_class is SemanticFailureClass.TRANSPORT
    assert result.provenance.runtime_evidence is not None
    assert result.provenance.runtime_evidence.failure_stage == "deadline_expired"


def test_every_adapter_stage_token_is_a_registered_closed_stage() -> None:
    from yoetz.domain.findings import RUNTIME_FAILURE_STAGES

    mapped = set(module._FAILURE_STAGE_BY_TOKEN.values())  # pyright: ignore[reportPrivateUsage]
    assert mapped <= RUNTIME_FAILURE_STAGES
    judgment_stages = {
        "judgment_envelope_invalid",
        "judgment_enum_invalid",
        "judgment_refs_duplicate",
        "judgment_refs_invalid",
        "judgment_conclusion_mismatch",
        "judgment_text_bounds",
        "judgment_shape_invalid",
    }
    assert judgment_stages <= RUNTIME_FAILURE_STAGES
    unmapped = {
        module._failure_stage(  # pyright: ignore[reportPrivateUsage]
            error, turn_acknowledged=acknowledged, launched=True
        )
        for error in (RuntimeError("native detail"), ValueError("unmapped_token"))
        for acknowledged in (True, False)
    }
    assert unmapped == {"unclassified"}
