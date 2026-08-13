"""A declined confidential ceremony must not be reported as an unavailable service.

The supervised dogfood run failed here: the daemon refused a disclosure decision with
`kind_forbidden`, the client collapsed every unmapped server code to `protocol_error`, and the
CLI printed `service_unavailable`. The operator was told to restart a service that was running
correctly, and the one fact that explained what to do next never reached them.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import pytest

from yoetz.cli.app import _trusted_exception_failure
from yoetz.cli.exits import ceremony_refusal_message, exit_code_for, remediation_message
from yoetz.cli.unlock import HumanCeremonyCliError
from yoetz.protocol.errors import PublicErrorCode
from yoetz.service.confidential_client import ConfidentialClientError

_REFUSALS = (
    "ceremony_unsupported",
    "kind_forbidden",
    "pending_not_actionable",
    "pending_unavailable",
    "state_forbidden",
)


@pytest.mark.parametrize("reason", _REFUSALS)
def test_structural_refusals_survive_the_client_boundary(reason: str) -> None:
    """The client must carry these codes through rather than flattening them."""

    error = ConfidentialClientError(reason)

    assert error.reason == reason


@pytest.mark.parametrize("reason", _REFUSALS)
def test_refusals_report_their_cause_and_not_service_unavailable(
    reason: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _trusted_exception_failure(ConfidentialClientError(reason))

    captured = capsys.readouterr().err
    assert reason in captured
    assert "service_unavailable" not in captured
    # A refused ceremony is a request the service answered, not a transport failure.
    assert code == exit_code_for(PublicErrorCode.INVALID_REQUEST)


def test_state_forbidden_names_the_unlock_command() -> None:
    message = ceremony_refusal_message("state_forbidden")

    assert message is not None
    assert "yoetz service unlock" in message


def test_pending_unavailable_does_not_confirm_whether_the_id_existed() -> None:
    message = ceremony_refusal_message("pending_unavailable")

    assert message is not None
    # Absent and expired share one message on purpose; distinguishing them would confirm
    # whether an unknown proposal id was ever real.
    assert "no longer exists or has expired" in message


def test_genuine_transport_failure_still_reports_service_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = _trusted_exception_failure(ConfidentialClientError("service_unavailable"))

    assert "service_unavailable" in capsys.readouterr().err
    assert code == exit_code_for(PublicErrorCode.SERVICE_UNAVAILABLE)


def test_cancellation_is_not_a_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    code = _trusted_exception_failure(ConfidentialClientError("cancelled"))

    assert capsys.readouterr().err.strip() == "cancelled"
    assert code == exit_code_for("cancelled")


def test_unknown_reasons_have_no_refusal_message() -> None:
    assert ceremony_refusal_message("timeout") is None


def test_trusted_console_required_is_not_reported_as_bad_command_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing foreground console is an environment fact, not a malformed command."""

    code = _trusted_exception_failure(HumanCeremonyCliError("trusted_console_required"))

    captured = capsys.readouterr().err
    assert "trusted_console_required" in captured
    assert "invalid_request: the command input is invalid" not in captured
    assert "yoetz consent prepare" in captured
    assert "yoetz consent authorize" in captured
    assert code == exit_code_for(PublicErrorCode.INVALID_REQUEST)


@pytest.mark.parametrize(
    "reason",
    [
        "confirmation_mismatch",
        "input_invalid",
    ],
)
def test_genuine_input_errors_still_report_invalid_request(
    reason: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _trusted_exception_failure(HumanCeremonyCliError(reason))

    assert "invalid_request: the command input is invalid" in capsys.readouterr().err
    assert code == exit_code_for(PublicErrorCode.INVALID_REQUEST)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("chat_user_attestation_invalid", "--client-kind"),
        ("chat_user_reauthentication_unavailable", "local terminal"),
        ("chat_user_target_mismatch", "yoetz consent prepare"),
        ("pending_already_active", "yoetz consent authorize"),
        ("pending_expired", "yoetz consent prepare"),
        ("provider_credential_invalid", "8192"),
        ("provider_not_configured", "yoetz --set"),
        ("repository_privacy_scope_unavailable", "yoetz --privacy"),
        ("trusted_console_required", "yoetz consent prepare"),
    ],
)
def test_bare_tokens_carry_the_exact_next_command(reason: str, expected: str) -> None:
    """Every mapped token must name what to run next, not restate the token."""

    message = remediation_message(reason)

    assert message is not None
    assert expected in message


def test_unmapped_tokens_have_no_remediation() -> None:
    assert remediation_message("authorize_failed") is None


def test_resource_count_remediation_names_the_owning_sync_script() -> None:
    message = remediation_message("resource_counts_invalid")

    assert message is not None
    assert "scripts/verify_resource_manifest.py --sync" in message
    assert "update the compiled count" not in message


def test_secret_rejected_names_the_credential_and_the_retry() -> None:
    """A refused or unusable key must not look like a dead service; name the retry."""

    message = ceremony_refusal_message("secret_rejected")

    assert message is not None
    assert "not accepted" in message
    assert "yoetz provider credential set" in message
