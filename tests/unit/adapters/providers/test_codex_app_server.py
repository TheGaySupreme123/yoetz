"""Exact Codex app-server cell stays secret-free, context-free, and fail closed."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

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


async def test_success_records_weaker_runtime_boundary_without_identity_or_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _evaluate(monkeypatch, _Runtime(_profile()))

    assert type(result) is SemanticResultSuccess
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
