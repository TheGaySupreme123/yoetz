"""Frozen recovery directives for typed public-error continuations.

Every value in this module is repository-authored text keyed by a typed token. Nothing here is
derived from a caller, a payload, a path, a provider, or model output, so a directive can be
rendered on any agent-facing surface without widening the egress boundary that
``CONTRIBUTING.md`` locks: "Nothing user-controlled ... appears in ... errors, or MCP text
summaries."

The point of the indirection is that the *token* travels and the *text* does not. A renderer
reconstructs the directive locally from the token, so the wire shape stays exactly as narrow as it
was before this module existed, and no ``safe_details`` key or ``public-error`` schema version was
added to carry prose (issue #739).

Directives restate what ``guidance/workflow.md`` already rules, in the one place an agent is
guaranteed to look: the error it just hit. When a rule changes, both move together — the
import-time gate below holds the vocabulary closed, and the guidance anchors are checked against
the registered resource set by ``yoetz.mcp.resources``.

A directive is an instruction, never a prediction. "Repeat the read with a new request_id" is
admissible because the error proves the read did not commit. "This will fix it" is not: the error
cannot substantiate an outcome, and coverage-bounded language forbids claiming one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal

from yoetz.protocol.errors import (
    ADMITTED_CLAIM_REVISION_INVARIANTS,
    ADMITTED_CONTINUATION_TOKENS,
    PROTOCOL_REASON_CODES,
    REASON_CODE_CONTINUATIONS,
)

__all__ = [
    "CLAIM_REVISION_CORRECTIONS",
    "CONTINUATION_TOKENS",
    "REASON_CODE_DIRECTIVE_EXEMPTIONS",
    "RECOVERY_DIRECTIVES",
    "RecoveryDirective",
    "TimeoutOperationKind",
    "WRITE_OPERATIONS",
    "continuation_for_local_reason",
    "continuation_for_reason",
    "correction_for_invariant",
    "covered_reason_codes",
    "directive_for",
    "timeout_operation_kind",
]

# The three ways a local request can time out, which is the one distinction a reason code alone
# cannot carry (issue #669): a read proves nothing committed, a write leaves the outcome unknown but
# recoverable through the operation view, and a ``start`` leaves it unknown *without* the session
# and writer ids that view requires.
type TimeoutOperationKind = Literal["read", "write", "start"]

# The workflow operations that append to the ledger. Shared by the MCP bridge and the CLI so both
# classify a timed-out operation the same way; ``status`` is the one read.
WRITE_OPERATIONS: Final[frozenset[str]] = frozenset(
    {"start", "publish_work", "check", "respond", "receipt"}
)

# Bounds chosen against the 512-byte ASCII ceiling in ``yoetz.mcp.summaries``: an error identity
# clause plus a reason/location clause plus a directive plus a guidance pointer has to fit, with
# the identity never the part that gets dropped.
_MAX_DIRECTIVE_BYTES: Final = 232
_MAX_NUDGE_BYTES: Final = 128
_GUIDANCE_URI_PATTERN: Final = re.compile(
    r"^yoetz://guidance/[a-z][a-z0-9-]*\.md(?:#[a-z0-9][a-z0-9-]*)?$", re.ASCII
)
_TOKEN_PATTERN: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)


@dataclass(frozen=True, slots=True)
class RecoveryDirective:
    """One frozen continuation: what happened, what to do next, and where the rule is written."""

    token: str
    directive: str
    guidance_uri: str | None = None
    nudge: str | None = None


_WORKFLOW_ERRORS: Final = "yoetz://guidance/workflow.md#errors-and-continuations"
_WORKFLOW_RECOVERY: Final = "yoetz://guidance/workflow.md#recovery-decision-table-02"
_TEMPLATES_SETUP: Final = "yoetz://guidance/request-templates.md#setup-and-consent"
_PUBLICATION_SETS: Final = "yoetz://guidance/publication-policy.md#set-valued-reference-lists"
_PUBLICATION_RECOVERY: Final = (
    "yoetz://guidance/publication-policy.md#operation-specific-recovery-and-templates"
)

_DIRECTIVES: Final = (
    # --- timeout family (issue #669) -------------------------------------------------------
    RecoveryDirective(
        token="read_timeout_new_identity",
        directive=(
            "This read timed out and requested no write, so nothing committed. Repeat the same "
            "read intent with a NEW request_id, preserving view, filter, cursor, and limit. "
            "Reusing a timed-out read identity is rejected."
        ),
        guidance_uri=_WORKFLOW_RECOVERY,
        nudge="An unreadable response is not proof a record is absent.",
    ),
    RecoveryDirective(
        token="write_timeout_same_identity",
        directive=(
            "This write timed out and MAY already have committed. Read status view=operation with "
            "filter.operation_request_id set to this exact request_id, then replay the same body "
            "once only when state=absent."
        ),
        guidance_uri=_WORKFLOW_RECOVERY,
        nudge="Do not mint a fresh request_id, task, or sibling to escape an ambiguous write.",
    ),
    RecoveryDirective(
        token="start_timeout_same_identity",
        directive=(
            "This start timed out and MAY already have committed. A lost start returns no session "
            "or writer id, so do not query operation status: replay the exact same start body once "
            "with this same request_id."
        ),
        guidance_uri=_WORKFLOW_RECOVERY,
        nudge="The start idempotency path returns the stored result or a typed boundary.",
    ),
    RecoveryDirective(
        token="service_replacement_exhausted",
        directive=(
            "An incompatible local service holder could not be superseded within the automatic "
            "budget. Run the exact authorized restart for this instance, then replay this request "
            "under its original identity rules."
        ),
        guidance_uri=_WORKFLOW_ERRORS,
        nudge="Never run service lifecycle commands an error did not name.",
    ),
    # --- setup and consent (issues #512, #740) ---------------------------------------------
    RecoveryDirective(
        token="vault_initialization_required",
        directive=(
            "The vault is uninitialized: a continuation, not a terminal error. Run the carried "
            "prepare command, show its danger text to the user, and wait for their exact "
            "decision. Replay this request once after setup reports ready."
        ),
        guidance_uri=_TEMPLATES_SETUP,
        nudge="Yoetz stores the secret locally; never request or transmit recovery material.",
    ),
    RecoveryDirective(
        token="consent_ceremony_required",
        directive=(
            "This operation needs a human decision that has not been made. Follow the carried "
            "prepare and review commands, then replay this exact request once after the ceremony "
            "reports a terminal decision."
        ),
        guidance_uri=_TEMPLATES_SETUP,
        nudge="Denial and expiry are answers; do not re-prepare to obtain a different one.",
    ),
    # --- authoring repairs (issues #266, #579) ----------------------------------------------
    RecoveryDirective(
        token="field_ownership_repair",
        directive=(
            "A field was sent under an event family that does not own it. Move the field to its "
            "owning family rather than deleting it; deleting it silently discards the record it "
            "carried."
        ),
        guidance_uri=_PUBLICATION_RECOVERY,
    ),
    RecoveryDirective(
        token="sorted_set_required",
        directive=(
            "A set-valued reference list must be ASCII-sorted and duplicate-free on the wire. "
            "Sort the named list and resubmit; the order is part of the canonical form, not a "
            "presentation choice."
        ),
        guidance_uri=_PUBLICATION_SETS,
    ),
    RecoveryDirective(
        token="input_correction_new_identity",
        directive=(
            "Yoetz rejected this request body before any write, so retryable=false covers only "
            "this exact body. Correct the named field per the schema hint and submit the corrected "
            "body once under a NEW request_id."
        ),
        guidance_uri=_WORKFLOW_ERRORS,
        nudge="A rejected body is not an ambiguous write; do not resend it unchanged.",
    ),
    RecoveryDirective(
        token="recovery_check_then_correct",
        directive=(
            "This body was rejected before any write, but whether an earlier request under this "
            "request_id already committed could not be checked. Read status view=operation for it "
            "once the service answers, then correct under a NEW request_id."
        ),
        guidance_uri=_WORKFLOW_RECOVERY,
        nudge="Do not resubmit until the original request_id has a known outcome.",
    ),
    # --- ledger and session state (issues #308, #326) ---------------------------------------
    RecoveryDirective(
        token="frontier_refresh_required",
        directive=(
            "The expected frontier no longer matches the ledger, so this write was not applied. "
            "Read status for the current frontier, set expected_frontier from it, and retry "
            "idempotently with this same request_id."
        ),
        guidance_uri=_PUBLICATION_RECOVERY,
        nudge="A frontier conflict means the ledger moved, not that your content was wrong.",
    ),
    RecoveryDirective(
        token="session_rebind_required",
        directive=(
            "This session is not usable for the request. Re-attach with an exact held session_id, "
            "or start with mode=create_or_attach using the canonical workspace and external ref "
            "pair. Inspect status before continuing."
        ),
        guidance_uri=_WORKFLOW_RECOVERY,
        nudge="A fresh conversation is not automatically a new task.",
    ),
    RecoveryDirective(
        token="operation_pending_inspect",
        directive=(
            "A prior operation under this identity is still pending. Read status view=operation "
            "once with the exact filter.operation_request_id, and replay only on an exact typed "
            "continuation whose approval has completed."
        ),
        guidance_uri=_WORKFLOW_RECOVERY,
        nudge="Retain and report a pending or quarantined boundary; do not claim completion.",
    ),
    # --- local installation state (issues #220, #237) ---------------------------------------
    RecoveryDirective(
        token="service_holder_busy",
        directive=(
            "A local service is already running and holding this instance's singleton. It is "
            "alive, not failed. Query its status before any lifecycle action, and never match a "
            "process by path or name to stop it."
        ),
        guidance_uri=_WORKFLOW_ERRORS,
    ),
    RecoveryDirective(
        token="resource_integrity_repair",
        directive=(
            "An installed resource does not match the reviewed manifest, so Yoetz refused to read "
            "it. Reinstall from a verified artifact; in a source checkout regenerate through the "
            "owning resource script."
        ),
        guidance_uri=_WORKFLOW_ERRORS,
        nudge="Do not hand-edit a generated resource to clear this.",
    ),
    RecoveryDirective(
        token="storage_root_unsafe",
        directive=(
            "The workspace or its Git metadata has an unsafe ancestor: a symlink, a foreign owner, "
            "or a writable parent. Pass a fully resolved path to a repository owned by the current "
            "user."
        ),
        guidance_uri=_WORKFLOW_ERRORS,
    ),
)

RECOVERY_DIRECTIVES: Final[Mapping[str, RecoveryDirective]] = MappingProxyType(
    {entry.token: entry for entry in _DIRECTIVES}
)

CONTINUATION_TOKENS: Final[frozenset[str]] = frozenset(RECOVERY_DIRECTIVES)


# Protocol reason codes whose recovery is fully determined by the reason alone. The map itself is
# held literally in ``yoetz.protocol.errors`` (a dependency root that cannot import this module),
# because that is where every ``PublicOperationError`` attaches the token at construction; this
# module owns the directive each value stands for and checks the two against each other below.
_REASON_CONTINUATIONS: Final[Mapping[str, str]] = REASON_CODE_CONTINUATIONS

# Reasons the reason code alone cannot resolve, because the correct recovery genuinely differs by
# operation kind. Resolved in ``continuation_for_reason``; held here so the ratchet counts them as
# answered rather than missing.
_OPERATION_DEPENDENT_REASONS: Final[frozenset[str]] = frozenset({"request_timeout"})

# A second, disjoint vocabulary. Lifecycle, instance, and ceremony reasons are raised by the CLI
# and service adapters (``yoetz.cli.exits``), never by the protocol validator, so they are not
# members of ``PROTOCOL_REASON_CODES`` and the ratchet below does not range over them. Keeping
# them apart is the point: mixing the namespaces is what let three CLI reasons masquerade as
# protocol reasons while this registry was being written.
_LOCAL_REASON_CONTINUATIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "human_authority_unavailable": "consent_ceremony_required",
        "human_authorization_required": "consent_ceremony_required",
        "human_authorization_stale": "consent_ceremony_required",
        "manifest_digest_mismatch": "resource_integrity_repair",
        "pending_expired": "consent_ceremony_required",
        "repository_privacy_scope_unavailable": "consent_ceremony_required",
        "resource_counts_invalid": "resource_integrity_repair",
        "resource_digest_mismatch": "resource_integrity_repair",
        "resource_missing": "resource_integrity_repair",
        "service_already_running": "service_holder_busy",
        "support_digest_mismatch": "resource_integrity_repair",
        "support_resource_set_mismatch": "resource_integrity_repair",
        "trusted_console_required": "consent_ceremony_required",
        "unsafe_root": "storage_root_unsafe",
    }
)

# The ratchet's escape hatch (issue #739). A reason code here has been examined and found to need
# no directive beyond its own name: it is an internal invariant an agent cannot act on, or a
# structural validation reason whose field pointer and authoring hint already say what to fix.
# Membership is a decision, not a default — the gate in ``yoetz.protocol.errors`` fails the build
# when a newly registered reason code appears in neither this set nor the mapping above, so a new
# reason code cannot land with nothing to say.
REASON_CODE_DIRECTIVE_EXEMPTIONS: Final[frozenset[str]] = frozenset(
    {
        # Validation reasons: the field pointer plus the schema-derived authoring hint already
        # name the exact repair, and a generic directive would bury it.
        "accepted_record_shape_invalid",
        "actor_id_malformed",
        "actor_id_not_generated",
        "byte_order_mark_forbidden",
        "claim_revision_invalid",
        "claim_revision_mismatch",
        "commitment_only_object_kind",
        "duplicate_object_key",
        "empty_check_types",
        "empty_publication_channels",
        "empty_subject_state",
        "event_integer_out_of_range",
        "event_text_out_of_bounds",
        "evidence_digest_availability_invalid",
        "evidence_digest_binding_invalid",
        "evidence_digest_binding_required",
        "evidence_digest_provenance_invalid",
        "evidence_digest_subject_incompatible",
        "evidence_strength_unsupported",
        "finding_json_shape_invalid",
        "finding_priority_mismatch",
        "float_forbidden",
        "id_malformed_uuid",
        "id_not_ascii",
        "id_uuid_not_version_4",
        "id_uuid_wrong_variant",
        "id_wrong_length",
        "id_wrong_prefix",
        "id_wrong_type",
        "import_report_invalid",
        "input_not_bytes",
        "integer_out_of_safe_range",
        "integer_out_of_sqlite_range",
        "invalid_actor_type",
        "invalid_approved_check",
        "invalid_approved_check_policy",
        "invalid_chain",
        "invalid_check_types",
        "invalid_commitment",
        "invalid_cost_fields",
        "invalid_coverage_value",
        "invalid_digest",
        "invalid_duration",
        "invalid_event_enum",
        "invalid_event_schema",
        "invalid_event_value_type",
        "invalid_finding_kind",
        "invalid_finding_origin",
        "invalid_finding_policy_identity",
        "invalid_finding_provenance",
        "invalid_finding_subject_refs",
        "invalid_frontier",
        "invalid_json_pointer",
        "invalid_known_gap",
        "invalid_payload_ref",
        "invalid_projection_locator",
        "invalid_publication_channels",
        "invalid_ranked_findings",
        "invalid_receipt_conclusion",
        "invalid_receipt_document",
        "invalid_receipt_gap",
        "invalid_receipt_obligation",
        "invalid_receipt_redaction",
        "invalid_receipt_response",
        "invalid_receipt_section",
        "invalid_receipt_section_order",
        "invalid_receipt_version_slice",
        "invalid_runtime_attempt_evidence",
        "invalid_sampling_params",
        "invalid_semantic_dispatch_kind",
        "invalid_semantic_failure_class",
        "invalid_semantic_fallback_origin",
        "invalid_semantic_outcome_type",
        "invalid_semantic_provenance",
        "invalid_semantic_status_reason_pair",
        "invalid_subject_state",
        "invalid_timestamp",
        "invalid_token_usage",
        "invalid_utf8",
        "lone_surrogate",
        "malformed_json",
        "missing_payload_field",
        "nesting_too_deep",
        "no_obligations_reason_conflict",
        "noncanonical_integer_string",
        "nul_byte_forbidden",
        "object_key_not_string",
        "obligation_change_invalid",
        "obligation_resolution_invalid",
        "obligation_resolution_mismatch",
        "payload_redaction_mismatch",
        "receipt_coverage_mismatch",
        "receipt_gap_not_in_coverage",
        "receipt_json_shape_invalid",
        "redaction_target_required",
        "ref_mirror_mismatch",
        "response_fields_invalid",
        "runtime_attempt_evidence_json_shape_invalid",
        "semantic_provenance_json_shape_invalid",
        "set_member_not_ascii",
        "timestamp_not_utc",
        "timestamp_out_of_range",
        "timestamp_submillisecond_precision",
        "timestamp_timezone_missing",
        "unknown_payload_field",
        "unsupported_json_type",
        "unsupported_payload_type",
        # Internal invariants and defects. An agent cannot act on these; the correlation_id is the
        # recovery path, and inventing a directive would imply a repair that does not exist.
        "internal_error",
        "entry_digest_mismatch",
        "event_family_not_admitted",
        "engine_family_wrong_author",
        "frame_invalid",
        "frame_too_large",
        "ledger_assigned_field_in_request_identity",
        "method_forbidden",
        "not_an_accepted_envelope",
        "peer_untrusted",
        "provider_attempt_provenance_is_not_final",
        "public_error_invalid_correlation_id",
        "public_error_invalid_message",
        "public_error_missing_correlation_id",
        "read_projection_failed",
        "response_projection_failed",
        "unknown_event_schema",
        # Schema catalog integrity. Reported through resource_integrity_repair when it reaches an
        # agent surface; the remaining members are build-time integrity checks that never do.
        "schema_artifact_role_invalid",
        "schema_artifact_role_mismatch",
        "schema_bytes_invalid",
        "schema_catalog_incomplete",
        "schema_draft_unsupported",
        "schema_duplicate_identity",
        "schema_id_mismatch",
        "schema_instance_invalid",
        "schema_kind_mismatch",
        "schema_manifest_duplicate_path",
        "schema_manifest_invalid",
        "schema_manifest_member_mismatch",
        "schema_manifest_missing",
        "schema_name_invalid",
        "schema_not_found",
        "schema_path_unsafe",
        "schema_reference_unresolved",
        "schema_version_mismatch",
        # Owned by later sub-issues of #739: provider and semantic classification (#742), and the
        # observation-drain and privacy families whose directives depend on decisions not yet
        # made. Listed explicitly so the ratchet records them as pending, not as answered.
        "accepted_but_unresponsive",
        "dependency_changed",
        "import_publication_authority_required",
        "receipt_json_projection_blocked",
        "ownership_contended",
        "plan_version_conflict",
        "privacy_projection_unavailable",
        "privacy_receipt_not_durable",
        "protocol_mismatch",
        "repository_identity_mismatch",
        "repository_identity_required",
        "request_identity_conflict",
        "service_draining",
        "service_generation_changed",
        "service_incompatible",
        "service_unavailable",
        "workspace_task_exists",
    }
)


# One corrective phrase per claim-revision invariant. These are directive text reconstructed from a
# typed token, exactly like a continuation directive, so they live beside them rather than inside
# one renderer: before ADR-030 they were reachable only from the MCP text projector, and the CLI
# rendered no correction at all for the same rejection.
CLAIM_REVISION_CORRECTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "claim_id_must_be_fresh": "use a fresh claim_id",
        "claim_kind_must_match": "match claim_kind with every superseded claim",
        "limitation_refs_complete": (
            "include every relevant partial or failed result in limitation_refs"
        ),
        "limitation_refs_must_be_relevant_non_success_results": (
            "keep only relevant non-success results in limitation_refs"
        ),
        "replacement_must_change_effective_claim": (
            "change the replacement's effective claim meaning"
        ),
        "replacement_must_not_dispute": "do not combine supersedes_claim_refs with disputes_refs",
        "scope_overlap_required": "overlap obligation scope with every superseded claim",
        "supporting_refs_must_exclude_limitations": (
            "keep non-success result ids in limitation_refs rather than supporting_refs"
        ),
        "superseded_claim_must_be_effective": "supersede only an effective claim",
        "superseded_claim_must_exist": "name an existing claim in supersedes_claim_refs",
    }
)


def correction_for_invariant(invariant: object) -> str | None:
    """Return the frozen corrective phrase for a claim-revision invariant, or None."""

    if type(invariant) is not str:
        return None
    return CLAIM_REVISION_CORRECTIONS.get(invariant)


def directive_for(token: object) -> RecoveryDirective | None:
    """Return the frozen directive for a continuation token, or None when unregistered."""

    if type(token) is not str:
        return None
    return RECOVERY_DIRECTIVES.get(token)


_TIMEOUT_CONTINUATIONS: Final[Mapping[TimeoutOperationKind, str]] = MappingProxyType(
    {
        "read": "read_timeout_new_identity",
        "write": "write_timeout_same_identity",
        "start": "start_timeout_same_identity",
    }
)


def timeout_operation_kind(
    operation: object, *, write_operations: frozenset[str] = WRITE_OPERATIONS
) -> TimeoutOperationKind | None:
    """Classify a timed-out operation name, or None when the name is not a known operation.

    ``start`` is a write whose lost response carries no route ids, so it is its own kind: the
    generic write recovery ("read status view=operation") is an instruction a first start cannot
    follow, and the shipped guidance has always excepted it (replay the exact start once).
    """

    if type(operation) is not str:
        return None
    if operation == "start":
        return "start"
    if operation in write_operations:
        return "write"
    return "read"


def continuation_for_reason(
    reason_code: object, *, operation_kind: TimeoutOperationKind | None = None
) -> str | None:
    """Return the continuation token for a typed reason, or None when none is registered.

    ``operation_kind`` resolves the one reason whose recovery genuinely differs by operation
    kind: a timed-out read proves nothing committed, a timed-out write leaves the outcome unknown
    and must be recovered under its original identity, and a timed-out ``start`` must be replayed
    outright because the operation view needs ids it never returned (issue #669). When the caller
    cannot say which it was, no directive travels rather than the wrong one.
    """

    if type(reason_code) is not str:
        return None
    if reason_code == "request_timeout":
        if operation_kind is None:
            return None
        return _TIMEOUT_CONTINUATIONS[operation_kind]
    return _REASON_CONTINUATIONS.get(reason_code)


def continuation_for_local_reason(reason: object) -> str | None:
    """Return the continuation token for a CLI lifecycle, instance, or ceremony reason.

    Disjoint from ``continuation_for_reason``: these reasons are raised by local adapters and are
    not protocol reason codes. Callers hold one vocabulary or the other, never both.
    """

    if type(reason) is not str:
        return None
    return _LOCAL_REASON_CONTINUATIONS.get(reason)


def covered_reason_codes() -> frozenset[str]:
    """Return every protocol reason code the ratchet counts as answered.

    A reason is answered when it maps to a directive, resolves to one through the operation kind,
    or carries an explicit exemption. ``yoetz.protocol.errors`` compares this against the full
    reason-code vocabulary at import time, so a newly registered reason code cannot land with
    nothing to say.
    """

    return frozenset(
        set(_REASON_CONTINUATIONS) | _OPERATION_DEPENDENT_REASONS | REASON_CODE_DIRECTIVE_EXEMPTIONS
    )


def _check_registry() -> None:
    """Hold the registry to its own bounds at import time."""

    for token, entry in RECOVERY_DIRECTIVES.items():
        if _TOKEN_PATTERN.fullmatch(token) is None or entry.token != token:
            raise RuntimeError("recovery_directive_token_invalid")
        for text, bound in (
            (entry.directive, _MAX_DIRECTIVE_BYTES),
            (entry.nudge, _MAX_NUDGE_BYTES),
        ):
            if text is None:
                continue
            try:
                encoded = text.encode("ascii", errors="strict")
            except UnicodeEncodeError as exc:
                raise RuntimeError("recovery_directive_not_ascii") from exc
            if not 1 <= len(encoded) <= bound:
                raise RuntimeError("recovery_directive_out_of_bounds")
            if any(ord(character) <= 0x1F or ord(character) == 0x7F for character in text):
                raise RuntimeError("recovery_directive_control_character")
        if entry.guidance_uri is not None and (
            _GUIDANCE_URI_PATTERN.fullmatch(entry.guidance_uri) is None
        ):
            raise RuntimeError("recovery_directive_guidance_uri_invalid")
    mapped_tokens = set(_REASON_CONTINUATIONS.values()) | set(_LOCAL_REASON_CONTINUATIONS.values())
    if mapped_tokens - CONTINUATION_TOKENS:
        raise RuntimeError("recovery_reason_maps_to_unregistered_token")
    if set(_REASON_CONTINUATIONS) & REASON_CODE_DIRECTIVE_EXEMPTIONS:
        raise RuntimeError("recovery_reason_both_mapped_and_exempt")
    if _OPERATION_DEPENDENT_REASONS & (
        set(_REASON_CONTINUATIONS) | REASON_CODE_DIRECTIVE_EXEMPTIONS
    ):
        raise RuntimeError("recovery_reason_both_operation_dependent_and_fixed")
    # The two reason vocabularies are disjoint by construction. An overlap means a local adapter
    # reason was mistaken for a protocol reason code, which is how three CLI lifecycle reasons
    # were nearly registered as protocol reasons while this module was written.
    if set(_LOCAL_REASON_CONTINUATIONS) & (
        set(_REASON_CONTINUATIONS) | REASON_CODE_DIRECTIVE_EXEMPTIONS
    ):
        raise RuntimeError("recovery_local_reason_collides_with_protocol_reason")
    # ``yoetz.protocol.errors`` is a dependency root and cannot import this module, so it holds
    # the admitted token set literally. Locking the two here means a token can never be admitted
    # onto the wire without a directive behind it, nor a directive exist for a token the
    # normalizer would strip.
    if CONTINUATION_TOKENS != ADMITTED_CONTINUATION_TOKENS:
        raise RuntimeError("recovery_tokens_disagree_with_safe_detail_admission")
    # Every admitted invariant must have a correction, and no correction may name an invariant the
    # normalizer would strip. An invariant without one would reach an agent as a bare token.
    if frozenset(CLAIM_REVISION_CORRECTIONS) != ADMITTED_CLAIM_REVISION_INVARIANTS:
        raise RuntimeError("claim_revision_corrections_disagree_with_admitted_invariants")
    # Coverage ratchet (issue #739). Every reason code must resolve to a directive or carry an
    # explicit exemption, so a newly registered reason can never reach an agent as a bare token
    # with nothing to do about it. Widening the reason vocabulary without deciding what an agent
    # should do is exactly the regression this gate exists to fail on.
    if PROTOCOL_REASON_CODES - covered_reason_codes():
        raise RuntimeError("protocol_reason_code_without_recovery_disposition")
    if covered_reason_codes() - PROTOCOL_REASON_CODES:
        raise RuntimeError("recovery_disposition_for_unregistered_reason_code")


_check_registry()
