"""Six-operation Codex capability evidence.

Non-live cells prove the installed MCP server advertises exactly six frozen tool descriptors and
that the strict-local application path completes start→publish→check without network or provider
secrets. Driving the same slice through interactive/exec Codex requires ``YOETZ_LIVE_CODEX=1``.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import cast

import pytest

from builders.ledger_adapters import ownership_fence
from builders.start_application import (
    MemoryStartRuntime,
    protocol_id,
    start_composition,
    start_request,
)
from capability.evidence import (
    CapabilityCase,
    EvidenceOutcome,
    Observation,
    bytes_digest,
    live_codex_authorized,
    record_and_write,
    runtime_capability_context,
)
from yoetz.application.egress import PrivacyCoordinator
from yoetz.application.service import Application, VerificationPolicy
from yoetz.domain.events import RuntimeProfile
from yoetz.domain.privacy import CandidateContext
from yoetz.domain.receipts import PolicyVersionEntry, ReceiptVersionSlice, SchemaVersionEntry
from yoetz.domain.values import Frontier
from yoetz.mcp.descriptors import TOOL_DESCRIPTOR_SET_DIGEST, TOOL_DESCRIPTORS
from yoetz.mcp.server import list_tools
from yoetz.ports.diagnostics import RuntimeCapability
from yoetz.ports.importer import ImporterPort, ImportStatusSnapshot
from yoetz.ports.runtime import BundleRuntimePort, RouteCommand, TaskRuntime
from yoetz.protocol.canonical import JsonValue, canonical_digest
from yoetz.protocol.models import CheckRequest, FrontierModel, PublishWorkRequest

_TEST_REVISION = bytes_digest(Path(__file__).read_bytes())
_VERSION = "0.139.0"
_DIGEST = "sha256:" + "7" * 64
_EXPECTED = ("start", "publish_work", "check", "respond", "status", "receipt")


class _IdleImporter:
    async def status(self, session: str) -> ImportStatusSnapshot:
        from yoetz.domain.values import session_id

        return ImportStatusSnapshot(session_id(session), 0, 0, (), ())


class _StrictLocalRuntime(MemoryStartRuntime):
    async def route(self, command: RouteCommand) -> TaskRuntime:
        assert command.writer_id is not None
        task_id, resources = next(iter(self.resources.items()))
        ledger, objects = resources
        assert (command.session_id, command.writer_id) in self.owners
        return TaskRuntime(
            task_id,
            command.session_id,
            command.writer_id,
            frozenset(
                {
                    RuntimeCapability.WRITE,
                    RuntimeCapability.STRUCTURAL_READ,
                    RuntimeCapability.PAYLOAD_READ,
                }
            ),
            ledger,
            objects,
            cast(ImporterPort, _IdleImporter()),
            "0.1.0",
            "0.1.0",
            "0.1",
            "1.0.0",
            ownership_fence(),
        )


class _NoDisclosurePrivacy:
    async def prepare_local_disclosure(self, candidate: CandidateContext) -> object:
        raise AssertionError("six-tools capability cell must stay local")

    async def close(self) -> None:
        return None


async def _semantic_forbidden(frozen: object, findings: object) -> object:
    del frozen, findings
    raise AssertionError("strict-local must never invoke the semantic evaluator")


def _versions() -> ReceiptVersionSlice:
    return ReceiptVersionSlice(
        package_name="yoetz",
        package_version="0.1.0",
        protocol_version="0.1",
        engine_version="0.1.0",
        projection_version="0.1.0",
        object_format_version="yoetz-object/1",
        catalog_schema_version="1",
        bundle_schema_version="1",
        policy_versions=(
            PolicyVersionEntry("research-evidence", "0.1.0"),
            PolicyVersionEntry("work-integrity", "0.1.0"),
        ),
        schema_versions=(SchemaVersionEntry("receipts/receipt-document", "1.0.0"),),
        resource_manifest_digest=_DIGEST,
    )


def _request_base(request_id: str) -> dict[str, JsonValue]:
    return {
        "protocol_version": "0.1",
        "schema_version": "1.0.0",
        "request_id": request_id,
        "actor": {"actor_id": "harness:test", "actor_type": "harness"},
        "client": {"kind": "test_client", "version": "0.1.0", "integration": "local_cli"},
    }


def _frontier(value: Frontier | FrontierModel) -> JsonValue:
    if isinstance(value, Frontier):
        return cast(JsonValue, dict(value.as_wire().items()))
    return cast(JsonValue, value.model_dump(mode="json"))


def _deny_network(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("strict-local six-tools cell must never attempt network egress")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_installed_server_advertises_exactly_six_frozen_tools(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    tools = await list_tools()
    names = tuple(tool.name for tool in tools)
    assert names == _EXPECTED
    assert tuple(item.name for item in TOOL_DESCRIPTORS) == _EXPECTED
    context = runtime_capability_context(
        fixture_digest=bytes_digest(TOOL_DESCRIPTOR_SET_DIGEST.encode("ascii")),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"descriptor_set": TOOL_DESCRIPTOR_SET_DIGEST}),
        external_tool="codex",
        external_version=_VERSION,
        integration_channel="codex_mcp_stdio",
        protocol_version="2025-11-25",
    )
    evidence = record_and_write(
        CapabilityCase(
            case_id="SIX-001",
            requirement_id="ADR-005.six-tools",
            claim_id="E-002.six-tools",
            capability_family="codex_six_tools",
            required_observation_codes=frozenset(
                {"tool_count", "descriptor_set_digest", "names_match"}
            ),
            allowed_observation_codes=frozenset(
                {"tool_count", "descriptor_set_digest", "names_match"}
            ),
        ),
        context,
        (
            Observation("tool_count", integer_value=len(names)),
            Observation("descriptor_set_digest", digest_value=TOOL_DESCRIPTOR_SET_DIGEST),
            Observation("names_match", boolean_value=True),
        ),
        EvidenceOutcome.PASS,
        output_root=evidence_root,
    )
    assert evidence.outcome is EvidenceOutcome.PASS


@pytest.mark.anyio
async def test_strict_local_six_operation_slice_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    monkeypatch.setattr(socket, "socket", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", _deny_network)

    start_app, _start_runtime, clock, catalog = start_composition()
    runtime = _StrictLocalRuntime(clock, _start_runtime.ids)
    app = Application(
        start_catalog=catalog.delegate,
        runtime=cast(BundleRuntimePort, runtime),
        clock=clock,
        ids=_start_runtime.ids,
        verification_policy=VerificationPolicy(semantic="disabled", max_findings=3),
        privacy=cast(PrivacyCoordinator, _NoDisclosurePrivacy()),
        status_cursor_key=b"six-tools-capability-cursor-key",
        waiver_policy_digest=_DIGEST,
        semantic_evaluator=_semantic_forbidden,
        disclosure_scope_for=lambda binding, source: (_ for _ in ()).throw(
            AssertionError("no disclosure")
        ),
        receipt_version_resolver=lambda _: _versions(),
        waiver_authorizer=lambda _: False,
        import_publication_authorizer=lambda _: False,
        profile=RuntimeProfile.STRICT_LOCAL,
        policy_packs=("research-evidence/0.1.0", "work-integrity/0.1.0"),
        version_manifest=start_app.version_manifest,
    )
    started = await app.start(start_request(600, title="Six-tools strict-local slice"))
    obligation_id = protocol_id("obl_", 601)
    obligation_event_id = protocol_id("evt_", 602)
    publish_wire = {
        **_request_base(protocol_id("req_", 603)),
        "session_id": started.session_id,
        "writer_id": started.writer_id,
        "expected_frontier": _frontier(started.frontier),
        "event_drafts": (
            {
                "event_id": obligation_event_id,
                "schema": {"name": "obligation_published", "version": "1.0.0"},
                "occurred_at": "2026-07-19T12:00:00.000Z",
                "causal_parents": (),
                "payload": {
                    "obligation_id": obligation_id,
                    "description": "Publish a result for the six-tools exercise.",
                    "acceptance_criteria": "A result is recorded in the task ledger.",
                    "evidence_expectation": "A linked immutable result record.",
                    "requested_items": ({"item_kind": "change", "value": "six-tools-result"},),
                    "status": "open",
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
            {
                "event_id": protocol_id("evt_", 604),
                "schema": {"name": "claim_recorded", "version": "1.0.0"},
                "occurred_at": "2026-07-19T12:00:01.000Z",
                "causal_parents": (obligation_event_id,),
                "payload": {
                    "claim_id": protocol_id("clm_", 605),
                    "claim_kind": "completion",
                    "statement": "The six-tools exercise is complete.",
                    "supporting_refs": (obligation_id,),
                    "obligation_refs": (obligation_id,),
                },
                "artifact_refs": (),
                "evidence_refs": (),
            },
        ),
    }
    published = await app.publish_work(PublishWorkRequest.model_validate(publish_wire))
    checked = await app.check(
        CheckRequest.model_validate(
            {
                **_request_base(protocol_id("req_", 606)),
                "session_id": started.session_id,
                "writer_id": started.writer_id,
                "expected_frontier": _frontier(published.result_frontier),
                "mode": "deterministic_only",
                "max_findings": "3",
            }
        )
    )
    assert checked is not None

    context = runtime_capability_context(
        fixture_digest=bytes_digest(b"strict-local-six-tools"),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"profile": "strict-local"}),
        external_tool="codex",
        external_version=_VERSION,
        integration_channel="local_cli",
    )
    evidence = record_and_write(
        CapabilityCase(
            case_id="SIX-002",
            requirement_id="ADR-005.six-tools",
            claim_id="E-002.six-tools-strict-local",
            capability_family="codex_six_tools",
            required_observation_codes=frozenset(
                {"network_denied", "workflow_completed", "provider_secret_absent"}
            ),
            allowed_observation_codes=frozenset(
                {"network_denied", "workflow_completed", "provider_secret_absent"}
            ),
        ),
        context,
        (
            Observation("network_denied", boolean_value=True),
            Observation("workflow_completed", boolean_value=True),
            Observation("provider_secret_absent", boolean_value=True),
        ),
        EvidenceOutcome.PASS,
        output_root=evidence_root,
    )
    assert evidence.outcome is EvidenceOutcome.PASS


@pytest.mark.live
def test_live_codex_drives_six_tools(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    context = runtime_capability_context(
        fixture_digest=bytes_digest(b"live-six-tools"),
        test_revision=_TEST_REVISION,
        config_profile_digest=canonical_digest({"cell": "live_six_tools"}),
        external_tool="codex",
        external_version=_VERSION,
        integration_channel="codex_mcp_stdio",
        protocol_version="2025-11-25",
    )
    if not live_codex_authorized():
        evidence = record_and_write(
            CapabilityCase(
                case_id="SIX-LIVE-001",
                requirement_id="ADR-005.six-tools",
                claim_id="E-002.six-tools-live",
                capability_family="codex_six_tools",
                required_observation_codes=frozenset({"live_authorized"}),
                allowed_observation_codes=frozenset({"live_authorized"}),
            ),
            context,
            (Observation("live_authorized", boolean_value=False),),
            EvidenceOutcome.UNSUPPORTED,
            ("live_codex_not_authorized",),
            output_root=evidence_root,
        )
        assert evidence.outcome is EvidenceOutcome.UNSUPPORTED
        return
    pytest.fail("live Codex six-tools slice authorized; drive tools before claiming pass")
