"""Claims conformance: docs/public-claims.json binds every public statement to real evidence.

Grounded entirely in the reviewed ``docs/public-claims.json`` claim map (schema
``yoetz.public-claims/1``) plus the real ADR set and spec-tree requirement files it cites. Every
claim must point to an existing ADR/spec requirement, an existing public surface document, and a
plausibly typed test or fixture path, with at least one declared evidence kind actually backed by a
same-shaped path. Several specifically named claims -- the structural-receipt, one-attempt-credential,
privacy-audit-storage, adapter-composition, assisted-review, review-packet, and no-raw-traceback
claims -- are additionally checked against their exact frozen wording, so a future silent widening or
narrowing of one of these promises is caught here rather than only in prose review.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CLAIMS_PATH = _REPO_ROOT / "docs" / "public-claims.json"

_EVIDENCE_PREFIXES = {
    "conformance_test": "tests/conformance/",
    "unit_test": "tests/unit/",
    "integration_test": "tests/integration/",
    "subprocess_test": "tests/subprocess/",
    "packaging_test": "tests/packaging/",
    "capability_run": "tests/capability/",
    "property_test": "tests/property/",
}
_KNOWN_TEST_PREFIXES = tuple(_EVIDENCE_PREFIXES.values())
_KNOWN_EVIDENCE_KINDS = frozenset({*_EVIDENCE_PREFIXES, "fixture"})
_KNOWN_RELEASE_STATUSES = frozenset({"not_yet_evidenced", "evidenced"})
_OVERCLAIM_TOKENS = (
    "definitely",
    "100%",
    "guarantee",
    "proven",
    "always correct",
    "certainly true",
)
_OVERCLAIM_PATTERNS = tuple(
    re.compile(r"(?<![a-z])" + re.escape(token) + r"(?![a-z])") for token in _OVERCLAIM_TOKENS
)


def _load_claims() -> list[dict[str, object]]:
    document = json.loads(_CLAIMS_PATH.read_text(encoding="utf-8"))
    assert document["schema"] == "yoetz.public-claims/1"
    claims = cast(list[dict[str, object]], document["claims"])
    assert claims
    return claims


def _claims_by_id() -> dict[str, dict[str, object]]:
    return {cast(str, claim["claim_id"]): claim for claim in _load_claims()}


def _is_fixture_path(path: str) -> bool:
    return path.startswith("fixtures/") and path.endswith(".case.json")


def test_claim_entries_cover_public_statements() -> None:
    """Every claim is uniquely identified, ADR/spec-backed, and points at real evidence paths."""

    claims = _load_claims()
    claim_ids = [cast(str, claim["claim_id"]) for claim in claims]
    assert len(claim_ids) == len(set(claim_ids))
    assert claim_ids == sorted(claim_ids, key=str.encode)

    for claim in claims:
        claim_id = cast(str, claim["claim_id"])
        assert cast(str, claim["statement"]).strip(), claim_id

        requirements = cast(list[str], claim["requirements"])
        assert requirements, claim_id
        for requirement in requirements:
            if requirement.startswith("ADR-"):
                matches = list(_REPO_ROOT.joinpath("docs", "adr").glob(f"{requirement}-*.md"))
                assert matches, (claim_id, requirement)
            else:
                assert (_REPO_ROOT / requirement).is_file(), (claim_id, requirement)

        surfaces = cast(list[str], claim["surfaces"])
        assert surfaces, claim_id
        for surface in surfaces:
            assert (_REPO_ROOT / surface).is_file(), (claim_id, surface)

        tests = cast(list[str], claim["tests"])
        assert tests, claim_id
        assert len(tests) == len(set(tests)), claim_id
        for test_path in tests:
            assert test_path.startswith(_KNOWN_TEST_PREFIXES) or _is_fixture_path(test_path), (
                claim_id,
                test_path,
            )

        evidence_kinds = cast(list[str], claim["evidence_kinds"])
        assert evidence_kinds, claim_id
        assert set(evidence_kinds) <= _KNOWN_EVIDENCE_KINDS, claim_id
        assert list(evidence_kinds) == sorted(set(evidence_kinds), key=str.encode), claim_id
        # At least one declared evidence kind is actually backed by a same-shaped test/fixture path
        # -- a claim never lists an evidence kind with nothing concrete behind it.
        assert any(
            (
                _is_fixture_path(test_path)
                if kind == "fixture"
                else test_path.startswith(_EVIDENCE_PREFIXES[kind])
            )
            for kind in evidence_kinds
            for test_path in tests
        ), claim_id

        assert cast(list[str], claim["limitations"]), claim_id


def test_claim_words_do_not_outrun_evidence() -> None:
    """Wording stays inside its own bounding qualifiers and matches the pinned named promises."""

    claims = _claims_by_id()

    for claim in claims.values():
        claim_id = cast(str, claim["claim_id"])
        statement = cast(str, claim["statement"]).lower()
        for token, pattern in zip(_OVERCLAIM_TOKENS, _OVERCLAIM_PATTERNS, strict=True):
            assert pattern.search(statement) is None, (claim_id, token)

    # Structural-receipt claims cover every reserved terminal decision and physical attempt, name
    # the sole pre-dispatch no-receipt exception, reject nonterminal states as a finished outcome,
    # and keep the request-body commitment distinct from credential metadata/HTTP-TLS framing.
    receipts_statement = cast(str, claims["privacy.structural_egress_receipts"]["statement"])
    assert "initial audit-reservation failure occurring before preview" in receipts_statement
    assert (
        "awaiting-human, approved, and receipt-repair states are never represented as a "
        "finished outcome" in receipts_statement
    )
    assert "excludes credential metadata and HTTP/TLS framing" in receipts_statement
    assert set(cast(list[str], claims["privacy.structural_egress_receipts"]["limitations"])) >= {
        "initial_reservation_failure_is_sole_no_receipt_exception",
        "nonterminal_states_never_a_finished_outcome",
    }

    # Privacy claims bind exactly one credential callback to each physical provider attempt.
    assert "consumed once by the custom transport" in cast(
        str, claims["privacy.one_attempt_provider_credentials"]["statement"]
    )

    # Privacy-audit storage claims bind content-bearing proposals to owning task-bundle encrypted
    # objects and never imply v0.1 has taskless content-encryption storage.
    audit_storage = claims["privacy.audit_content_storage_boundary"]
    assert "owning task bundle" in cast(str, audit_storage["statement"])
    assert "no_taskless_content_encryption_in_v01" in cast(list[str], audit_storage["limitations"])

    # Adapter claims are limited to closed reviewed-bundled composition with no injected ambient
    # handles, and explicitly disclaim OS/process sandbox isolation.
    adapters = claims["privacy.policy_approved_outbound_only"]
    assert "no repository, database, environment, or transcript handle" in cast(
        str, adapters["statement"]
    )
    assert set(cast(list[str], adapters["limitations"])) >= {
        "not_os_or_process_sandbox_isolation",
        "reviewed_bundled_adapters_only",
    }

    # privacy.assisted_review_user_controlled maps its five named concepts to exact tests.
    assisted_review = claims["privacy.assisted_review_user_controlled"]
    assisted_statement = cast(str, assisted_review["statement"])
    for phrase in (
        "zero-egress local_only",
        "inspectable assisted_review recipe",
        "current data-use record",
        "without a repeated human prompt",
        "recommendation evidence, never a technical proof",
    ):
        assert phrase in assisted_statement, phrase
    assert cast(list[str], assisted_review["tests"]) == [
        "tests/conformance/privacy/test_privacy_profiles.py",
        "tests/subprocess/test_service_lock_and_confidential_unlock.py",
        "tests/unit/adapters/test_repository_identity.py",
        "tests/unit/privacy/test_policy_and_contracts.py",
    ]

    # semantic.review_packet_and_agent_loop maps its six named concepts to exact tests.
    review_loop = claims["semantic.review_packet_and_agent_loop"]
    review_loop_statement = cast(str, review_loop["statement"])
    for phrase in (
        "deterministic findings and their machine-readable bases",
        "bounded problem-local recorded excerpts",
        "explicit omission reason",
        "reviewer challenge",
        "respond, publish_work, and recheck loop",
        "no provider-driven source-fetch channel",
    ):
        assert phrase in review_loop_statement, phrase
    assert cast(list[str], review_loop["tests"]) == [
        "tests/conformance/honesty/test_adversarial_cases.py",
        "tests/integration/application/test_check.py",
        "tests/unit/application/test_semantic_case.py",
        "tests/unit/application/test_semantic_case_envelope.py",
    ]
    assert set(cast(list[str], review_loop["limitations"])) >= {
        "no_live_repository_or_filesystem_fetch",
        "no_model_waiver_authority",
    }

    # The v0.1 diagnostic claim permits only bounded structural identity and proves raw traceback
    # capture is absent -- never merely "owner-only" or "disabled by default".
    diagnostics = claims["privacy.no_raw_traceback_capture"]
    diagnostics_statement = cast(str, diagnostics["statement"])
    assert "bounded structural correlation and reason identity" in diagnostics_statement
    assert "not merely disabled by default" in diagnostics_statement
    assert "no_future_artifact_without_new_review" in cast(list[str], diagnostics["limitations"])


def test_skipped_or_unsupported_claims_are_flagged() -> None:
    """release_status is explicit and bounded; a stronger status always has real evidence files."""

    claims = _load_claims()
    statuses = {cast(str, claim["release_status"]) for claim in claims}
    assert statuses
    assert statuses <= _KNOWN_RELEASE_STATUSES
    pending = {
        cast(str, claim["claim_id"])
        for claim in claims
        if claim["release_status"] == "not_yet_evidenced"
    }
    assert pending == {
        "integration.codex_exact_version_support",
        "recovery.machine_bound_vs_portable",
        "support.structural_subject_state_capture",
    }

    for claim in claims:
        claim_id = cast(str, claim["claim_id"])
        status = cast(str, claim["release_status"])
        assert status, claim_id
        if status != "not_yet_evidenced":
            # A claim is never silently upgraded past honest evidence: every one of its declared
            # test/fixture paths must exist for real once it claims anything stronger.
            for test_path in cast(list[str], claim["tests"]):
                assert (_REPO_ROOT / test_path).is_file(), (claim_id, test_path)
            assert any(
                test_path.startswith(_KNOWN_TEST_PREFIXES)
                and not test_path.startswith("tests/capability/")
                for test_path in cast(list[str], claim["tests"])
            ), claim_id
