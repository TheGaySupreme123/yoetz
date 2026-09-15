"""Registry, ratchet, and projection tests for typed recovery directives (issue #739)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from yoetz.cli.render import render_human_error
from yoetz.mcp.summaries import _MAX_SUMMARY_BYTES, summary_for_public_error
from yoetz.protocol.errors import PROTOCOL_REASON_CODES, normalize_safe_details
from yoetz.protocol.models import PublicErrorModel
from yoetz.protocol.recovery import (
    CONTINUATION_TOKENS,
    RECOVERY_DIRECTIVES,
    continuation_for_local_reason,
    continuation_for_reason,
    covered_reason_codes,
    directive_for,
)

_GUIDANCE_ROOT = Path(__file__).resolve().parents[3] / "guidance"
_CORRELATION_ID = "err_3f2a1b4c-5d6e-4f70-8a91-b2c3d4e5f607"


def _heading_slugs(path: Path) -> set[str]:
    """Return GitHub-style anchor slugs for every heading in a guidance document."""

    slugs: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#{1,6} +(.*?)\s*$", line)
        if match is None:
            continue
        text = re.sub(r"[^a-z0-9 \-]", "", match.group(1).lower())
        slugs.add(re.sub(r" +", "-", text.strip()))
    return slugs


def _error(**safe_details: object) -> dict[str, object]:
    return {
        "error": {
            "code": "SERVICE_UNAVAILABLE",
            "message": "A message the text channel must never copy.",
            "retryable": True,
            "correlation_id": _CORRELATION_ID,
            "safe_details": dict(safe_details),
        }
    }


class TestRatchet:
    def test_every_reason_code_has_a_disposition(self) -> None:
        """A reason code with neither a directive nor an exemption is the regression to catch."""

        assert PROTOCOL_REASON_CODES - covered_reason_codes() == set()

    def test_no_disposition_names_an_unregistered_reason_code(self) -> None:
        assert covered_reason_codes() - PROTOCOL_REASON_CODES == set()

    def test_continuation_tokens_are_admitted_by_the_protocol_normalizer(self) -> None:
        """A registered token nothing can put on the wire would be a dead registry entry."""

        for token in CONTINUATION_TOKENS:
            gated = normalize_safe_details({"continuation": token})
            assert gated.get("continuation") == token


class TestRegistryBounds:
    @pytest.mark.parametrize("token", sorted(CONTINUATION_TOKENS))
    def test_guidance_pointer_resolves_to_a_real_document_and_anchor(self, token: str) -> None:
        """A pointer that leads nowhere teaches an agent to ignore every pointer."""

        entry = RECOVERY_DIRECTIVES[token]
        if entry.guidance_uri is None:
            pytest.skip("directive carries no guidance pointer")
        name, _, anchor = entry.guidance_uri.removeprefix("yoetz://guidance/").partition("#")
        document = _GUIDANCE_ROOT / name
        assert document.is_file()
        if anchor:
            assert anchor in _heading_slugs(document)

    @pytest.mark.parametrize("token", sorted(CONTINUATION_TOKENS))
    def test_directive_is_an_instruction_not_a_prediction(self, token: str) -> None:
        """Coverage-bounded language: an error may direct, but may not promise an outcome."""

        entry = RECOVERY_DIRECTIVES[token]
        text = f"{entry.directive} {entry.nudge or ''}".lower()
        for promise in ("will fix", "will resolve", "this fixes", "guaranteed", "should work"):
            assert promise not in text


class TestReasonResolution:
    def test_read_and_write_timeouts_resolve_to_different_continuations(self) -> None:
        """The distinction #669 reported as lost is the whole point of the operation kind."""

        assert continuation_for_reason("request_timeout", write_operation=False) == (
            "read_timeout_new_identity"
        )
        assert continuation_for_reason("request_timeout", write_operation=True) == (
            "write_timeout_same_identity"
        )

    def test_unknown_operation_kind_carries_no_timeout_continuation(self) -> None:
        """Silence beats asserting a commit claim the caller could not establish."""

        assert continuation_for_reason("request_timeout") is None

    def test_local_and_protocol_reason_vocabularies_stay_disjoint(self) -> None:
        assert continuation_for_local_reason("service_already_running") == "service_holder_busy"
        assert continuation_for_reason("service_already_running") is None
        assert continuation_for_local_reason("unsorted_set_field") is None

    def test_unregistered_and_non_string_input_is_refused(self) -> None:
        assert directive_for(None) is None
        assert directive_for("not_a_registered_token") is None
        assert continuation_for_reason(object()) is None


class TestNativeTextProjection:
    def test_vault_continuation_reaches_the_model(self) -> None:
        """Issue #740: the #512 continuation and its frozen commands were dropped entirely."""

        summary = summary_for_public_error(
            {
                "error": {
                    "code": "VAULT_LOCKED",
                    "message": "A message the text channel must never copy.",
                    "retryable": False,
                    "correlation_id": _CORRELATION_ID,
                    "safe_details": {
                        "continuation": "vault_initialization_required",
                        "prepare_command": "yoetz consent prepare vault_initialize",
                        "review_command": "yoetz consent review",
                    },
                }
            }
        )
        assert "vault_initialization_required" in summary
        assert "yoetz consent prepare vault_initialize" in summary
        assert "yoetz consent review" in summary

    def test_projection_never_copies_the_public_error_message(self) -> None:
        summary = summary_for_public_error(
            _error(reason_code="request_timeout", continuation="read_timeout_new_identity")
        )
        assert "must never copy" not in summary

    def test_unregistered_continuation_token_renders_nothing(self) -> None:
        """A token the normalizer refuses must not reach the text channel as a bare string."""

        summary = summary_for_public_error(_error(continuation="not_a_registered_token"))
        assert "not_a_registered_token" not in summary
        assert summary.startswith("Error SERVICE_UNAVAILABLE;")

    @pytest.mark.parametrize("token", sorted(CONTINUATION_TOKENS))
    def test_every_directive_fits_the_summary_ceiling(self, token: str) -> None:
        """A directive that overflows would drop the projection back to bare identity."""

        summary = summary_for_public_error(
            _error(
                continuation=token,
                prepare_command="yoetz consent prepare vault_initialize",
                review_command="yoetz consent review",
                authorize_command="yoetz consent authorize",
            )
        )
        assert len(summary.encode("ascii")) <= _MAX_SUMMARY_BYTES
        assert token in summary

    def test_error_identity_survives_a_budget_too_tight_for_advice(self) -> None:
        """Priority order: identity is never the part sacrificed to fit a directive."""

        summary = summary_for_public_error(
            _error(
                continuation="write_timeout_same_identity",
                reason_code="request_timeout",
                field="/" + "a" * 250,
            )
        )
        assert summary.startswith("Error SERVICE_UNAVAILABLE; retryable: yes;")
        assert _CORRELATION_ID in summary
        assert len(summary.encode("ascii")) <= _MAX_SUMMARY_BYTES


class TestCliProjection:
    def test_cli_renders_the_same_directive(self) -> None:
        model = PublicErrorModel.model_validate(
            {
                "code": "SERVICE_UNAVAILABLE",
                "message": "The local operation timed out and may still have committed.",
                "retryable": True,
                "correlation_id": _CORRELATION_ID,
                "safe_details": {
                    "reason_code": "request_timeout",
                    "continuation": "write_timeout_same_identity",
                },
            }
        )
        rendered = render_human_error(model)
        assert rendered.splitlines()[0].startswith("SERVICE_UNAVAILABLE:")
        assert "Continuation: write_timeout_same_identity" in rendered
        assert RECOVERY_DIRECTIVES["write_timeout_same_identity"].directive in rendered

    def test_cli_error_without_a_continuation_is_unchanged(self) -> None:
        model = PublicErrorModel.model_validate(
            {
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred.",
                "retryable": False,
                "correlation_id": _CORRELATION_ID,
            }
        )
        assert render_human_error(model) == "INTERNAL_ERROR: An internal error occurred."
