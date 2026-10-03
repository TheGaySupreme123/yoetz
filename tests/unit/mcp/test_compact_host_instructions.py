"""The Codex and Cursor hosts receive a compact initialize body with a stated byte cap (#918).

Codex code mode builds every advertised tool's description from the initialize instructions, the
tool description and a generated declaration, so the 19.8 KB `agent-instructions.md` document was
charged seven times on every turn. The compact body keeps the rules an agent needs before its first
guidance read and names where the full safety floor is; these tests lock the cap, those rules, the
shared route tail, and the unchanged Claude and generic bodies.
"""

from __future__ import annotations

import hashlib
from typing import cast

import pytest

from yoetz.mcp import server as bridge
from yoetz.mcp.descriptors import (
    CLAUDE_CODE_INITIALIZE_INSTRUCTIONS,
    COMPACT_INITIALIZE_INSTRUCTIONS,
    COMPACT_INSTRUCTIONS_BUDGET,
    COMPACT_INSTRUCTIONS_HOST_PROFILES,
    INITIALIZE_GUIDANCE_URIS,
    McpRouteProfile,
    advertised_surface_metrics,
    server_instructions,
)
from yoetz.mcp.resources import GUIDANCE_RESOURCES, read_resource
from yoetz.mcp.semantic_destination import (
    DISCLOSURE_PREFIX,
    MAX_DISCLOSURE_ENCODED_BYTES,
    SemanticDestinationDisclosure,
    disclose_semantic_destination,
)
from yoetz.ports.control import McpHostProfile

# The stated cap recorded on issue #918: the packaged compact body is at most 2 KB. The route tail
# and the policy-route destination disclosure (#479) ride on top and are never trimmed to fit.
COMPACT_BODY_CAP_BYTES = 2_048
# The Claude body as shipped by #789, byte for byte; this change must not move it.
CLAUDE_BODY_SHA256 = "sha256:5b6ee4d2e269f021299a6533cb3c3739e9738af100831b82dd1071b186f9bf02"
COMPACT_HOSTS: tuple[McpHostProfile, ...] = ("codex", "cursor")
# The longer of the two route lines the bridge appends to every body.
LONGEST_ROUTE_LINE = (
    "\n\nRoute profile: strict. "
    "This route will not request external AI-powered review for this process lifetime.\n"
)


def _ceiling_disclosure() -> SemanticDestinationDisclosure:
    padding = MAX_DISCLOSURE_ENCODED_BYTES - len(DISCLOSURE_PREFIX.encode("utf-8")) - 1
    return SemanticDestinationDisclosure("unknown", DISCLOSURE_PREFIX + "x" * padding + ".")


def _collapsed(text: str) -> str:
    return " ".join(text.split())


def test_the_codex_and_cursor_hosts_select_the_compact_body() -> None:
    assert frozenset(COMPACT_HOSTS) == COMPACT_INSTRUCTIONS_HOST_PROFILES
    for host in COMPACT_HOSTS:
        policy = server_instructions("policy", host_profile=host)
        assert policy == (
            COMPACT_INITIALIZE_INSTRUCTIONS + "\n\nRoute profile: policy. "
            "External AI-powered review follows the configured policy.\n"
        )


def test_the_compact_body_fits_the_stated_cap() -> None:
    budget = COMPACT_INSTRUCTIONS_BUDGET
    assert budget["packaged_max_encoded_bytes"] == COMPACT_BODY_CAP_BYTES
    assert COMPACT_INITIALIZE_INSTRUCTIONS.isascii()
    assert len(COMPACT_INITIALIZE_INSTRUCTIONS.encode("utf-8")) <= COMPACT_BODY_CAP_BYTES
    assert budget["max_encoded_bytes"] == (
        COMPACT_BODY_CAP_BYTES
        + len("\n\nRoute profile: policy. ")
        + len("External AI-powered review follows the configured policy.")
        + 1
        + MAX_DISCLOSURE_ENCODED_BYTES
        + 1
    )


@pytest.mark.parametrize("host", COMPACT_HOSTS)
@pytest.mark.parametrize(
    ("profile", "disclosure"),
    [
        ("policy", None),
        ("policy", disclose_semantic_destination(None)),
        ("policy", _ceiling_disclosure()),
        ("strict", None),
        ("strict", _ceiling_disclosure()),
    ],
    ids=["policy", "policy-unknown", "policy-ceiling", "strict", "strict-ignores-disclosure"],
)
def test_every_rendering_fits_the_cap_plus_its_route_tail(
    host: McpHostProfile,
    profile: McpRouteProfile,
    disclosure: SemanticDestinationDisclosure | None,
) -> None:
    rendered = server_instructions(profile, host_profile=host, semantic_destination=disclosure)
    assert rendered.isascii()
    assert rendered.startswith(COMPACT_INITIALIZE_INSTRUCTIONS + f"\n\nRoute profile: {profile}. ")
    assert len(rendered.encode("utf-8")) <= COMPACT_INSTRUCTIONS_BUDGET["max_encoded_bytes"]
    if disclosure is None or profile == "strict":
        # Without a disclosure the whole served block stays within the 2 KB target plus the
        # route line, which is shared verbatim with every other host.
        assert DISCLOSURE_PREFIX not in rendered
        assert len(rendered.encode("utf-8")) <= COMPACT_BODY_CAP_BYTES + len(LONGEST_ROUTE_LINE)
    else:
        # The privacy disclosure (#479) is carried whole, at the end, as on every other host.
        assert rendered.rstrip("\n").endswith(disclosure.sentence)


@pytest.mark.parametrize("host", COMPACT_HOSTS)
@pytest.mark.parametrize("profile", ["policy", "strict"])
def test_the_route_and_disclosure_tail_is_composed_identically_for_every_body(
    host: McpHostProfile, profile: McpRouteProfile
) -> None:
    disclosure = disclose_semantic_destination(None)
    generic_document = read_resource(INITIALIZE_GUIDANCE_URIS[0]).decode("utf-8").rstrip()
    generic = server_instructions(profile, semantic_destination=disclosure)
    claude = server_instructions(profile, host_profile="claude", semantic_destination=disclosure)
    compact = server_instructions(profile, host_profile=host, semantic_destination=disclosure)
    tail = generic.removeprefix(generic_document)
    assert tail.startswith(f"\n\nRoute profile: {profile}. ")
    assert claude == CLAUDE_CODE_INITIALIZE_INSTRUCTIONS + tail
    assert compact == COMPACT_INITIALIZE_INSTRUCTIONS + tail


def test_the_compact_body_says_when_to_call_start_and_never_to_claim_it_early() -> None:
    heading, _, body = COMPACT_INITIALIZE_INSTRUCTIONS.partition("\n\n")
    assert heading == "# Yoetz: call start first"
    trigger = _collapsed(body.split("\n\n")[0])
    assert trigger.startswith(
        "If this session will edit files, run state-changing commands, or delegate, call "
        "`start` before that work."
    )
    assert (
        "If material work already began without a task, call `start` now, publish it as a "
        "plan, and disclose the uncovered prefix in the receipt." in trigger
    )
    assert "If the tool list shows only names, load the `start` schema first." in trigger
    assert "Read-only questions skip it." in trigger
    text = _collapsed(COMPACT_INITIALIZE_INSTRUCTIONS)
    assert "Never claim Yoetz is active before `start` returns" in text
    assert "never invent a ledger task, id, finding, verdict or receipt" in text
    assert "If `start` fails, follow its typed continuation, then ask the user" in text
    assert "do not work without a task" in text


def test_the_compact_body_names_every_guidance_document_and_how_to_read_it() -> None:
    text = _collapsed(COMPACT_INITIALIZE_INSTRUCTIONS)
    # The full safety floor is one read away, and the body says to read it before `start`.
    assert (
        "Before the first `start`, call `read_guidance` on "
        "`yoetz://guidance/agent-instructions.md` (the full safety floor) and "
        "`yoetz://guidance/workflow.md`." in text
    )
    for resource in GUIDANCE_RESOURCES:
        assert f"`{resource.uri}`" in text, resource.uri
        assert read_resource(resource.uri), resource.uri
    assert "`yoetz://guidance/publication-policy.md` before the first `publish_work`" in text
    assert "`yoetz://guidance/coverage-and-receipts.md` before the first `check`" in text
    assert "Do not list resources to find them" in text
    assert "call `start` on an empty guidance body" in text


def test_the_compact_body_keeps_cadence_consent_and_honesty_rules() -> None:
    text = _collapsed(COMPACT_INITIALIZE_INSTRUCTIONS)
    # Ceremony unchanged: every cadence step stays named.
    assert (
        "Cadence: `start` once, `publish_work` per material transition, `check` after the "
        "completion claim and evidence, `respond` per finding, `receipt` last." in text
    )
    assert "`respond` records a disposition; it does not clear a finding." in text
    assert "On `retryable: false`, follow only the typed `continuation`." in text
    assert "If Yoetz is unavailable, say no live record or receipt exists." in text
    # Disclosure and consent boundaries.
    assert (
        "Publish only material, state-bound facts, never hidden reasoning, transcripts, "
        "credentials, secrets or whole files." in text
    )
    assert (
        "Setup, privacy, credential and recommendation changes need the user's explicit "
        "approval of that exact action; never handle a vault secret." in text
    )
    assert "Recover through `status`, never Yoetz databases or source." in text
    # Coverage-honest wording.
    assert (
        "Yoetz records only what participants publish; a clean check does not mean the work is "
        "correct." in text
    )
    assert "Keep the final answer no stronger than the receipt's weakest coverage." in text


def test_the_claude_body_is_unchanged_and_the_generic_host_keeps_the_full_document() -> None:
    digest = "sha256:" + hashlib.sha256(CLAUDE_CODE_INITIALIZE_INSTRUCTIONS.encode()).hexdigest()
    assert digest == CLAUDE_BODY_SHA256
    assert server_instructions("policy", host_profile="claude").startswith(
        CLAUDE_CODE_INITIALIZE_INSTRUCTIONS + "\n\nRoute profile: policy. "
    )
    document = read_resource(INITIALIZE_GUIDANCE_URIS[0]).decode("utf-8").rstrip()
    assert server_instructions("policy", host_profile="generic") == server_instructions("policy")
    assert server_instructions("policy").startswith(document + "\n\nRoute profile: policy. ")
    assert COMPACT_INITIALIZE_INSTRUCTIONS != CLAUDE_CODE_INITIALIZE_INSTRUCTIONS


def test_the_compact_surface_costs_a_fraction_of_the_generic_one() -> None:
    generic = advertised_surface_metrics("policy")
    for host in COMPACT_HOSTS:
        compact = advertised_surface_metrics("policy", host_profile=host)
        assert compact["instructions_encoded_bytes"] == len(
            server_instructions("policy", host_profile=host).encode("utf-8")
        )
        # One copy per advertised tool of the 19.8 KB document becomes one compact copy per tool.
        saved = generic["replicated_encoded_bytes"] - compact["replicated_encoded_bytes"]
        assert saved == generic["tool_count"] * (
            generic["instructions_encoded_bytes"] - compact["instructions_encoded_bytes"]
        )
        assert compact["instructions_encoded_bytes"] * 9 < generic["instructions_encoded_bytes"]


@pytest.mark.parametrize("host", COMPACT_HOSTS)
def test_the_bridge_runtime_serves_the_compact_body(host: McpHostProfile) -> None:
    disclosure = disclose_semantic_destination(None)
    policy = bridge.build_bridge_runtime(
        "policy", host_profile=host, semantic_destination=disclosure
    )
    assert policy.instructions == server_instructions(
        "policy", host_profile=host, semantic_destination=disclosure
    )
    assert policy.instructions.startswith(COMPACT_INITIALIZE_INSTRUCTIONS)
    strict = bridge.build_bridge_runtime("strict", host_profile=host)
    assert strict.instructions == server_instructions("strict", host_profile=host)


def test_an_unknown_host_profile_is_still_rejected_rather_than_given_a_body() -> None:
    from typing import Any

    with pytest.raises(ValueError, match="mcp_host_profile_invalid"):
        server_instructions("policy", host_profile=cast(Any, "codex-code-mode"))
