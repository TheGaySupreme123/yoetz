"""Provider / AI-powered review recovery tokens (issue #742)."""

from __future__ import annotations

from yoetz.cli.render import render_human_check
from yoetz.domain.findings import SemanticFailureClass
from yoetz.mcp.summaries import summary_for_check
from yoetz.protocol.models import (
    CheckSuccessModel,
    CoverageModel,
    SemanticReason,
    SemanticStatus,
)
from yoetz.protocol.recovery import (
    RECOVERY_DIRECTIVES,
    continuation_for_semantic_outcome,
    directive_for,
)

_FRONTIER = {"sequence": "3", "head_digest": "sha256:" + "a" * 64}
_CANARY = "sk-user-secret-should-never-appear"
_PROVIDER_CANARY = "I am the raw model output with field X malformed"


def test_adapter_failure_class_selects_credential_token() -> None:
    assert (
        continuation_for_semantic_outcome(
            status=SemanticStatus.UNAVAILABLE,
            reason=SemanticReason.TRANSPORT_UNAVAILABLE,
            failure_class=SemanticFailureClass.AUTHENTICATION,
        )
        == "semantic_credential_rejected"
    )
    assert (
        continuation_for_semantic_outcome(
            status=SemanticStatus.UNAVAILABLE,
            reason=SemanticReason.TRANSPORT_UNAVAILABLE,
            failure_class="authorization",
        )
        == "semantic_credential_rejected"
    )
    assert (
        continuation_for_semantic_outcome(
            status=SemanticStatus.UNAVAILABLE,
            reason=SemanticReason.TRANSPORT_UNAVAILABLE,
            failure_class=SemanticFailureClass.TRANSPORT,
        )
        == "semantic_transport_retry"
    )


def test_closed_reason_tokens_cover_issue_examples() -> None:
    assert (
        continuation_for_semantic_outcome(
            status=SemanticStatus.INVALID,
            reason=SemanticReason.RESPONSE_SCHEMA_INVALID,
        )
        == "semantic_response_invalid"
    )
    assert (
        continuation_for_semantic_outcome(
            status=SemanticStatus.INVALID,
            reason=SemanticReason.RESPONSE_CONTENT_INVALID,
        )
        == "semantic_response_truncated"
    )
    assert (
        continuation_for_semantic_outcome(
            status=SemanticStatus.FAILED,
            reason=SemanticReason.CASE_CAPACITY_EXCEEDED,
        )
        == "semantic_capacity_exceeded"
    )


def test_terminal_outcome_reasons_are_not_misread_as_a_transport_diagnosis() -> None:
    """``retry_budget_exhausted`` and ``outcome_unknown`` name how a job ended, not why."""

    for reason in (SemanticReason.RETRY_BUDGET_EXHAUSTED, SemanticReason.OUTCOME_UNKNOWN):
        token = continuation_for_semantic_outcome(status=SemanticStatus.UNAVAILABLE, reason=reason)
        assert token == "semantic_no_judgment"
        directive = directive_for(token)
        assert directive is not None
        assert "transport" not in directive.directive.lower()


def test_predispatch_configuration_outcomes_carry_no_setup_prompt() -> None:
    """A check on an installation without a provider must not demand a credential every time."""

    for status, reason in (
        (SemanticStatus.NOT_CONFIGURED, SemanticReason.PROVIDER_NOT_CONFIGURED),
        (SemanticStatus.NOT_CONFIGURED, SemanticReason.LOCAL_MODEL_NOT_CONFIGURED),
        (SemanticStatus.UNAVAILABLE, SemanticReason.CREDENTIAL_UNAVAILABLE),
    ):
        assert continuation_for_semantic_outcome(status=status, reason=reason) is None


def test_directives_never_downgrade_required_review() -> None:
    """Every directive offering a local-only check also names the required-review rule."""

    for token, entry in RECOVERY_DIRECTIVES.items():
        if not token.startswith("semantic_") or "local-only" not in entry.directive:
            continue
        assert "For optional review" in entry.directive or "for optional review" in entry.directive
        assert "required review" in entry.directive
        assert "unmet" in entry.directive


def test_truncated_answer_directive_never_offers_a_second_job() -> None:
    directive = directive_for("semantic_response_truncated")
    assert directive is not None
    assert "NEW request_id" not in directive.directive
    assert "Do not spend a second job" in directive.directive


def test_succeeded_and_disabled_reasons_have_no_failure_directive() -> None:
    assert (
        continuation_for_semantic_outcome(
            status=SemanticStatus.SUCCEEDED,
            reason=SemanticReason.SEMANTIC_COMPLETED,
        )
        is None
    )
    assert (
        continuation_for_semantic_outcome(
            status=SemanticStatus.NOT_REQUESTED,
            reason=SemanticReason.DETERMINISTIC_MODE,
        )
        is None
    )
    assert (
        continuation_for_semantic_outcome(
            status=SemanticStatus.BLOCKED_BY_POLICY,
            reason=SemanticReason.CHANNEL_DISABLED,
        )
        is None
    )


def test_directives_never_copy_caller_or_provider_text() -> None:
    for token in (
        "semantic_response_invalid",
        "semantic_response_truncated",
        "semantic_credential_rejected",
        "semantic_capacity_exceeded",
        "semantic_timeout",
        "semantic_refused",
        "semantic_rate_limited",
        "semantic_transport_retry",
        "semantic_no_judgment",
        "semantic_coordinator_review",
    ):
        entry = RECOVERY_DIRECTIVES[token]
        blob = f"{entry.directive} {entry.nudge or ''}"
        assert _CANARY not in blob
        assert _PROVIDER_CANARY not in blob
        assert "will fix" not in blob.lower()
        assert "guaranteed" not in blob.lower()


def test_check_renderers_project_registry_text_without_raw_output() -> None:
    envelope: dict[str, object] = {
        "ok": True,
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": "req_00000000-0000-4000-8000-000000000001",
        "session_id": "ses_00000000-0000-4000-8000-000000000001",
        "task_id": "tsk_00000000-0000-4000-8000-000000000001",
        "writer_id": "wri_00000000-0000-4000-8000-000000000001",
        "result_frontier": _FRONTIER,
        "subject_frontier": _FRONTIER,
        "verdict": "incomplete_check",
        "findings": [],
        "suppressed_count": "0",
        "semantic_status": "invalid",
        "semantic_reason": "response_content_invalid",
        "semantic_provenance": {
            "failure_class": "response_content",
            "status": "invalid",
            "reason": "response_content_invalid",
        },
        "coverage": {
            "publication_channels": ["engine_derived"],
            "authorship_assurance": "service_authenticated",
            "artifact_observation": "published_only",
            "evidence_immutability": "content_digest",
            "ledger_freshness": "current",
            "check_types": ["deterministic"],
            "known_gaps": ["semantic_relevance_review_not_run"],
        },
        "versions": {
            "protocol": "0.1",
            "engine": "test",
            "projection": "test",
            "policies": [],
        },
    }
    text = summary_for_check(envelope)
    assert "Continuation: semantic_response_truncated." in text
    assert _PROVIDER_CANARY not in text
    assert len(text.encode("ascii")) <= 512

    result = CheckSuccessModel.model_construct(
        verdict="incomplete_check",
        semantic_status="unavailable",
        semantic_reason="transport_unavailable",
        semantic_provenance=None,
        findings=(),
        suppressed_count="0",
        coverage=CoverageModel.model_construct(known_gaps=()),
        children=None,
        advisory_notes=(),
    )
    rendered = render_human_check(result)
    assert "Continuation: semantic_transport_retry" in rendered
    assert directive_for("semantic_transport_retry") is not None
    assert _CANARY not in rendered
