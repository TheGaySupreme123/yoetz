"""Registry, ratchet, and projection tests for typed recovery directives (issue #739)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import pytest

from yoetz.cli.render import render_human_error
from yoetz.mcp.summaries import (
    _MAX_SUMMARY_BYTES,  # pyright: ignore[reportPrivateUsage]
    summary_for_public_error,
)
from yoetz.protocol.errors import (
    ADMITTED_CLAIM_REVISION_INVARIANTS,
    PROTOCOL_REASON_CODES,
    REASON_CODE_CONTINUATIONS,
    SAFE_DETAIL_KEYS,
    PublicErrorCode,
    PublicOperationError,
    normalize_safe_details,
)
from yoetz.protocol.models import PublicErrorModel
from yoetz.protocol.recovery import (
    CLAIM_REVISION_CORRECTIONS,
    CONTINUATION_TOKENS,
    RECOVERY_DIRECTIVES,
    continuation_for_local_reason,
    continuation_for_reason,
    correction_for_invariant,
    covered_reason_codes,
    directive_for,
    timeout_operation_kind,
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
    def test_local_reasons_cannot_shadow_operation_dependent_protocol_reasons(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import yoetz.protocol.recovery as recovery

        monkeypatch.setattr(
            recovery,
            "_LOCAL_REASON_CONTINUATIONS",
            {"request_timeout": "read_timeout_new_identity"},
        )
        with pytest.raises(
            RuntimeError, match="recovery_local_reason_collides_with_protocol_reason"
        ):
            recovery._check_registry()  # pyright: ignore[reportPrivateUsage]

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
    def test_read_write_and_start_timeouts_resolve_to_different_continuations(self) -> None:
        """The distinction #669 reported as lost is the whole point of the operation kind.

        ``start`` is its own kind: a lost first start returns neither session nor writer id, so
        the generic write recovery (read ``status view=operation``) is an instruction it cannot
        follow, and the shipped guidance excepts it with an exact same-request_id replay.
        """

        assert continuation_for_reason("request_timeout", operation_kind="read") == (
            "read_timeout_new_identity"
        )
        assert continuation_for_reason("request_timeout", operation_kind="write") == (
            "write_timeout_same_identity"
        )
        assert continuation_for_reason("request_timeout", operation_kind="start") == (
            "start_timeout_same_identity"
        )

    def test_operation_names_classify_into_the_three_timeout_kinds(self) -> None:
        assert timeout_operation_kind("start") == "start"
        for write in ("publish_work", "check", "respond", "receipt"):
            assert timeout_operation_kind(write) == "write"
        assert timeout_operation_kind("status") == "read"
        assert timeout_operation_kind(None) is None

    def test_start_timeout_directive_never_prescribes_the_operation_view(self) -> None:
        """The write directive requires ids a lost start never returned."""

        start = RECOVERY_DIRECTIVES["start_timeout_same_identity"]
        assert "view=operation" not in start.directive
        assert "same request_id" in start.directive
        assert "view=operation" in RECOVERY_DIRECTIVES["write_timeout_same_identity"].directive

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


class TestAttachmentAtConstruction:
    """The reason map is applied where every public error is built, not at each raising site.

    The first version of the registry mapped reasons to tokens but attached nothing: production
    use was one MCP timeout branch, so an ``unsorted_set_field`` rejection reached the model with
    the registered directive absent. Attachment now happens in ``PublicOperationError`` itself.
    """

    @pytest.mark.parametrize("reason", sorted(REASON_CODE_CONTINUATIONS))
    def test_every_mapped_reason_attaches_its_token_when_the_error_is_built(
        self, reason: str
    ) -> None:
        error = PublicOperationError(
            PublicErrorCode.EVENT_INVALID, "message", False, safe_details={"reason_code": reason}
        )
        assert error.safe_details.get("continuation") == REASON_CODE_CONTINUATIONS[reason]
        assert list(error.safe_details) == sorted(error.safe_details)

    def test_a_producer_chosen_continuation_is_never_overridden(self) -> None:
        error = PublicOperationError(
            PublicErrorCode.SERVICE_UNAVAILABLE,
            "message",
            True,
            safe_details={
                "reason_code": "frontier_changed",
                "continuation": "session_rebind_required",
            },
        )
        assert error.safe_details["continuation"] == "session_rebind_required"

    def test_an_unmapped_reason_attaches_nothing(self) -> None:
        error = PublicOperationError(
            PublicErrorCode.INTERNAL_ERROR,
            "message",
            False,
            safe_details={"reason_code": "internal_error"},
        )
        assert "continuation" not in error.safe_details

    def test_the_frontier_directive_agrees_with_the_shipped_replay_semantics(self) -> None:
        """publish_work stores a frontier conflict as a retryable failure under the same request_id."""

        directive = RECOVERY_DIRECTIVES["frontier_refresh_required"].directive
        assert "same request_id" in directive
        assert "new request_id" not in directive.lower()


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


class TestSafeDetailBounds:
    """The allowlist size and the schema's per-instance cap are different limits.

    ``public-error-1.0.0`` sets ``maxProperties: 32`` on a ``safe_details`` *instance* and admits
    property names by pattern, never by an enumerated list. The code-side allowlist may therefore
    exceed 32 -- adding ``invariant`` took it to 33 -- as long as no producer emits more than 32
    properties on one error. These tests lock that distinction, because mistaking one limit for
    the other is what nearly forced an unnecessary ``public-error-1.1.0``.
    """

    def test_allowlist_may_exceed_the_per_instance_cap(self) -> None:
        assert len(SAFE_DETAIL_KEYS) > 32
        assert tuple(SAFE_DETAIL_KEYS) == tuple(sorted(SAFE_DETAIL_KEYS))

    def test_real_producers_stay_inside_the_per_instance_cap(self) -> None:
        """Every error a producer actually builds must still validate against the schema."""

        from yoetz.domain.events import (
            ClaimRevisionMismatch,
            public_error_for_claim_revision_mismatch,
        )

        error = public_error_for_claim_revision_mismatch(
            ClaimRevisionMismatch("obligation_refs", "scope_overlap_required"), event_index=0
        ).bind_correlation_id(_CORRELATION_ID)
        public = error.as_public_dict()
        details = cast(dict[str, object], public["safe_details"])
        assert len(details) <= 32
        PublicErrorModel.model_validate(public)


class TestClaimRevisionCorrections:
    def test_every_admitted_invariant_has_a_correction(self) -> None:
        assert frozenset(CLAIM_REVISION_CORRECTIONS) == ADMITTED_CLAIM_REVISION_INVARIANTS

    def test_invariant_rides_as_a_typed_detail_not_only_in_the_message(self) -> None:
        """ADR-030: the fact the MCP projector used to recover by regex is now structural."""

        from yoetz.domain.events import (
            ClaimRevisionMismatch,
            public_error_for_claim_revision_mismatch,
        )

        error = public_error_for_claim_revision_mismatch(
            ClaimRevisionMismatch("claim_id", "claim_id_must_be_fresh"), event_index=3
        )
        assert error.safe_details.get("invariant") == "claim_id_must_be_fresh"

    def test_unregistered_invariant_is_stripped_by_the_normalizer(self) -> None:
        assert normalize_safe_details({"invariant": "never_registered"}) == {}
        assert correction_for_invariant("never_registered") is None
        assert correction_for_invariant(None) is None
