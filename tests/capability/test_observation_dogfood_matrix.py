"""Offline dogfood matrix for structural observation across synthetic Codex profiles.

Non-live: proves generic structural ingestion for an unknown future Codex version and for
older/current fixture profiles when present. Live codex-testing binary paths stay marked
``@pytest.mark.live`` and are skipped unless explicitly authorized.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.capability.evidence import (
    CapabilityCase,
    EvidenceOutcome,
    Observation,
    bytes_digest,
    capability_evidence_output_root,
    live_codex_authorized,
    record_and_write,
    runtime_capability_context,
)

from yoetz.adapters.importers.codex_jsonl import (
    SUPPORTED_CODEX_PROFILES,
    CodexCapabilityProfile,
    parse_codex_jsonl,
    profile_for_codex_version,
)
from yoetz.adapters.integrations.codex_session_stream import (
    SessionStreamReader,
    default_stream_profile,
    envelope_from_stream_record,
)
from yoetz.adapters.integrations.observation_local import (
    STREAM_MAPPING_VERSION,
    LocalObservationStore,
)
from yoetz.domain.observation import (
    ObservationCursor,
    ObservationGapCode,
    ObservationSource,
    ObservationStatusQuery,
)
from yoetz.protocol.canonical import canonical_digest

_TEST_REVISION = bytes_digest(Path(__file__).read_bytes())
_EMPTY = "hmac-sha256:" + ("0" * 64)


def _case(case_id: str, claim: str) -> CapabilityCase:
    return CapabilityCase(
        case_id=case_id,
        requirement_id="ADR-010.observation-dogfood",
        claim_id=claim,
        capability_family="codex_observation_dogfood",
        required_observation_codes=frozenset({"structural_ingest_ok"}),
        allowed_observation_codes=frozenset(
            {
                "structural_ingest_ok",
                "unknown_future_gap",
                "fixture_profile_present",
                "generic_mapping_used",
            }
        ),
    )


def test_synthetic_unknown_future_codex_version_generic_ingest(tmp_path: Path) -> None:
    evidence_root = capability_evidence_output_root(tmp_path)
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    session = store.session_commitment("future-codex")
    store.bind_session(workspace, session)

    path = tmp_path / "future.jsonl"
    path.write_bytes(
        b'{"type":"item.completed","item":{"id":"fx1","type":"command_execution",'
        b'"command":"echo","aggregated_output":"ok","exit_code":0,"status":"completed"}}\n'
    )
    reader = SessionStreamReader(
        session_commitment=session,
        profile=default_stream_profile(),
        cursor=ObservationCursor(
            source_generation=1,
            byte_position=0,
            event_position=0,
            last_source_commitment=_EMPTY,
            mapping_version=STREAM_MAPPING_VERSION,
        ),
    )
    advance = reader.advance(path)
    assert advance.envelopes
    for envelope in advance.envelopes:
        result = store.ingest(envelope)
        assert result.disposition.value in {"accepted", "duplicate"}
    # Opaque future-host gap recorded without inventing success; observation continues.
    from yoetz.domain.observation import ObservationEnvelope
    from yoetz.domain.values import JsonObject, Timestamp

    gap_env = ObservationEnvelope(
        session_commitment=session,
        event_kind="unsupported_event",
        source_identity="stream:future-opaque-v99",
        source=ObservationSource.CODEX_SESSION_STREAM,
        cursor=ObservationCursor(
            source_generation=advance.cursor.source_generation,
            byte_position=advance.cursor.byte_position + 8,
            event_position=advance.cursor.event_position + 1,
            last_source_commitment=advance.cursor.last_source_commitment,
            mapping_version=STREAM_MAPPING_VERSION,
        ),
        receipt_time=Timestamp("2026-07-22T22:30:00.000Z"),
        structural_payload=JsonObject(
            {"stream_kind": "future_wrapper", "codex_version": "99.0.0-future"}
        ),
        content_object_refs=(),
        gap_codes=(ObservationGapCode.UNSUPPORTED_EVENT.value,),
    )
    assert store.ingest(gap_env).disposition.value == "accepted"
    status = store.status(ObservationStatusQuery(workspace))
    assert status.source_coverage[ObservationSource.CODEX_SESSION_STREAM] is True
    assert ObservationGapCode.UNSUPPORTED_EVENT.value in status.gaps

    context = runtime_capability_context(
        fixture_digest=bytes_digest(b"synthetic-future-codex-obs"),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest(
            {"codex_version": "99.0.0-future", "mode": "generic_structural"}
        ),
        external_tool="codex",
        external_version="99.0.0-future",
        integration_channel="codex_session_stream",
    )
    evidence = record_and_write(
        _case("OBS-DOGFOOD-FUTURE", "E-013.observation-future-generic"),
        context,
        (
            Observation("structural_ingest_ok", boolean_value=True),
            Observation("generic_mapping_used", boolean_value=True),
            Observation("unknown_future_gap", boolean_value=True),
        ),
        EvidenceOutcome.PASS,
        (),
        output_root=evidence_root,
    )
    assert evidence.outcome is EvidenceOutcome.PASS


def test_fixture_profiles_older_or_current_when_present(tmp_path: Path) -> None:
    evidence_root = capability_evidence_output_root(tmp_path)
    profiles = tuple(sorted(SUPPORTED_CODEX_PROFILES))
    assert profiles, "expected at least one registered Codex profile"
    selected: list[CodexCapabilityProfile] = []
    for version in profiles:
        try:
            selected.append(profile_for_codex_version(version))
        except ValueError:
            continue
    assert selected
    store = LocalObservationStore(_state=tmp_path)
    workspace = store.workspace_commitment(str(tmp_path.resolve()))
    store.grant_consent(workspace)
    for index, profile in enumerate(selected[:2]):
        session = store.session_commitment(f"profile-{index}")
        store.bind_session(workspace, session)
        sample = (
            b'{"type":"item.completed","item":{"id":"p%d","type":"command_execution",'
            b'"command":"true","aggregated_output":"","exit_code":0,"status":"completed"}}\n'
            % index
        )
        parsed = parse_codex_jsonl(sample, profile=profile)
        assert parsed.records
        for record in parsed.records:
            envelope = envelope_from_stream_record(
                record,
                session_commitment=session,
                cursor=ObservationCursor(
                    source_generation=1,
                    byte_position=max(8, (index + 1) * 16),
                    event_position=index + 1,
                    last_source_commitment=_EMPTY,
                    mapping_version=STREAM_MAPPING_VERSION,
                ),
            )
            assert store.ingest(envelope).disposition.value in {"accepted", "duplicate"}

    context = runtime_capability_context(
        fixture_digest=bytes_digest(b"fixture-profile-obs-matrix"),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"profiles": list(profiles[:2])}),
        external_tool="codex",
        external_version=str(profiles[0]),
        integration_channel="codex_session_stream",
    )
    evidence = record_and_write(
        _case("OBS-DOGFOOD-PROFILES", "E-013.observation-fixture-profiles"),
        context,
        (
            Observation("structural_ingest_ok", boolean_value=True),
            Observation("fixture_profile_present", boolean_value=True),
        ),
        EvidenceOutcome.PASS,
        (),
        output_root=evidence_root,
    )
    assert evidence.outcome is EvidenceOutcome.PASS


@pytest.mark.live
def test_live_codex_observation_dogfood_optional(tmp_path: Path) -> None:
    if not live_codex_authorized():
        pytest.skip("live codex not authorized")
    pytest.skip("live codex-testing binary observation dogfood not required offline")
