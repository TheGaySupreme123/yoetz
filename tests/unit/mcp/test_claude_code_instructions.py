"""Claude-host initialize instructions fit the observed Claude Code cap (issue #789).

Claude Code desktop keeps the first 2,048 characters of an MCP server's initialize
``instructions`` and appends a literal ``… [truncated]`` marker. The packaged document served to
every other host is several times that size, so on Claude the block ended mid-sentence before the
one rule that matters: call ``start`` first. These tests lock the host-specific body, its budget
arithmetic, and the byte identity of every other host's text.
"""

from __future__ import annotations

import pytest

from yoetz.mcp import server as bridge
from yoetz.mcp.descriptors import (
    CLAUDE_CODE_INITIALIZE_INSTRUCTIONS,
    CLAUDE_CODE_INSTRUCTIONS_BUDGET,
    INITIALIZE_GUIDANCE_URIS,
    server_instructions,
)
from yoetz.mcp.resources import read_resource
from yoetz.mcp.semantic_destination import (
    DISCLOSURE_PREFIX,
    MAX_DISCLOSURE_ENCODED_BYTES,
    SemanticDestinationDisclosure,
    disclose_semantic_destination,
)

CAP = CLAUDE_CODE_INSTRUCTIONS_BUDGET["observed_host_cap_chars"]
TRUNCATION_MARKER = "[truncated]"


def _ceiling_disclosure() -> SemanticDestinationDisclosure:
    """A disclosure of exactly the admissible ceiling, longer than any catalog rendering."""

    padding = MAX_DISCLOSURE_ENCODED_BYTES - len(DISCLOSURE_PREFIX.encode("utf-8")) - 1
    return SemanticDestinationDisclosure("unknown", DISCLOSURE_PREFIX + "x" * padding + ".")


def _sentences(paragraph: str) -> list[str]:
    return [item.strip() for item in paragraph.split(". ") if item.strip()]


def test_the_first_two_sentences_are_the_trigger_and_the_late_start_rule() -> None:
    heading, _, body = CLAUDE_CODE_INITIALIZE_INSTRUCTIONS.partition("\n\n")
    assert heading == "# Yoetz: call start first"
    first, second, *_rest = _sentences(body.split("\n\n")[0])
    assert first.startswith(
        "If this session will edit files, run state-changing commands, or delegate"
    )
    assert first.endswith("call `start` before that work")
    assert second.startswith("If material work already began without a task, call `start` now")
    assert "disclose the uncovered prefix in the receipt" in second


def test_the_claude_text_names_the_deferred_schema_load_step_and_the_catalog() -> None:
    text = CLAUDE_CODE_INITIALIZE_INSTRUCTIONS
    assert "ToolSearch `select:mcp__yoetz__start`" in text
    assert "plugin-prefixed name" in text
    assert "Read-only questions skip it." in text
    # Everything that no longer fits is one read_guidance call away; both URIs stay named so the
    # agent that only sees this block can still find the safety floor and the workflow.
    assert "`read_guidance`" in text
    assert "yoetz://guidance/agent-instructions.md" in text
    assert "yoetz://guidance/workflow.md" in text
    assert "do not list resources to find them" in text
    assert "Never claim Yoetz is active before `start` returns" in text
    assert "a clean check does not mean the work is correct" in text


def test_budget_arithmetic_derives_the_packaged_bound_from_the_observed_cap() -> None:
    budget = CLAUDE_CODE_INSTRUCTIONS_BUDGET
    assert budget["max_chars"] <= budget["observed_host_cap_chars"] == 2_048
    route_line = "\n\nRoute profile: policy. "
    policy_tail = "External AI-powered review follows the configured policy."
    joiner, trailing_newline = 1, 1
    assert budget["packaged_max_chars"] == (
        budget["max_chars"]
        - len(route_line)
        - len(policy_tail)
        - joiner
        - MAX_DISCLOSURE_ENCODED_BYTES
        - trailing_newline
    )
    assert CLAUDE_CODE_INITIALIZE_INSTRUCTIONS.isascii()
    assert len(CLAUDE_CODE_INITIALIZE_INSTRUCTIONS) <= budget["packaged_max_chars"]


@pytest.mark.parametrize(
    "disclosure",
    [None, disclose_semantic_destination(None), _ceiling_disclosure()],
    ids=["no-disclosure", "unknown-destination", "ceiling-disclosure"],
)
def test_policy_instructions_for_claude_fit_under_the_cap_with_any_disclosure(
    disclosure: SemanticDestinationDisclosure | None,
) -> None:
    rendered = server_instructions("policy", host_profile="claude", semantic_destination=disclosure)
    assert len(rendered) <= CAP
    assert rendered.startswith(CLAUDE_CODE_INITIALIZE_INSTRUCTIONS + "\n\nRoute profile: policy. ")
    assert rendered.endswith("\n")
    if disclosure is not None:
        # The privacy disclosure (#479) is never the part a capped host cuts.
        assert rendered.rstrip("\n").endswith(disclosure.sentence)
    assert TRUNCATION_MARKER not in rendered


def test_strict_instructions_for_claude_fit_under_the_cap_and_ignore_the_disclosure() -> None:
    strict = server_instructions("strict", host_profile="claude")
    assert len(strict) <= CAP
    assert strict == (
        CLAUDE_CODE_INITIALIZE_INSTRUCTIONS
        + "\n\nRoute profile: strict. This route will not request external AI-powered review for "
        "this process lifetime.\n"
    )
    assert strict == server_instructions(
        "strict", host_profile="claude", semantic_destination=_ceiling_disclosure()
    )


@pytest.mark.parametrize("host_profile", ["generic", "codex", "cursor"])
def test_every_other_host_keeps_the_packaged_document_byte_for_byte(host_profile: str) -> None:
    from typing import cast

    from yoetz.ports.control import McpHostProfile

    host = cast(McpHostProfile, host_profile)
    document = read_resource(INITIALIZE_GUIDANCE_URIS[0]).decode("utf-8").rstrip()
    policy = server_instructions("policy", host_profile=host)
    assert policy == server_instructions("policy")
    assert policy.startswith(document + "\n\nRoute profile: policy. ")
    assert server_instructions("strict", host_profile=host) == server_instructions("strict")
    disclosure = disclose_semantic_destination(None)
    assert server_instructions(
        "policy", host_profile=host, semantic_destination=disclosure
    ) == server_instructions("policy", semantic_destination=disclosure)
    # The full document is the thing Claude cannot render; it must stay over the cap here so a
    # future edit does not silently swap the hosts' bodies.
    assert len(policy) > CAP


def test_the_claude_bridge_runtime_carries_the_compact_text() -> None:
    disclosure = disclose_semantic_destination(None)
    policy = bridge.build_bridge_runtime(
        "policy", host_profile="claude", semantic_destination=disclosure
    )
    assert policy.instructions == server_instructions(
        "policy", host_profile="claude", semantic_destination=disclosure
    )
    assert len(policy.instructions) <= CAP
    strict = bridge.build_bridge_runtime("strict", host_profile="claude")
    assert strict.instructions == server_instructions("strict", host_profile="claude")
    generic = bridge.build_bridge_runtime("policy", semantic_destination=disclosure)
    assert generic.instructions == server_instructions("policy", semantic_destination=disclosure)


def test_an_unknown_host_profile_is_rejected_before_any_text_is_composed() -> None:
    from typing import Any, cast

    with pytest.raises(ValueError, match="mcp_host_profile_invalid"):
        server_instructions("policy", host_profile=cast(Any, "desktop"))
