"""The Codex skill's code-mode section works in a sandbox without `crypto` (issue #918).

Codex code mode runs Yoetz calls from JavaScript cells. Its sandbox has no `crypto`, yet every
`request_id` and event-draft id must be a strict prefixed UUIDv4: the DeepSWE v2 run recorded 45
`crypto is not defined` failures and hand-rolled ids the `request_id` pattern rejected. These
tests execute the skill's exact helper snippet in JavaScript runtimes without `crypto` and check
its output against the id patterns in the packaged schemas. They also keep the section's yield
guidance equal to the bridge's real call deadlines, so a semantic check returns in its own turn.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Final, cast

import pytest

from yoetz.mcp import server as bridge
from yoetz.protocol.schemas import load_schema_catalog, schema_document_for

_REPO_ROOT: Final = Path(__file__).resolve().parents[3]
_SKILL: Final = _REPO_ROOT / "skills" / "codex" / "yoetz" / "SKILL.md"
_PACKAGED_SKILL: Final = (
    _REPO_ROOT / "src" / "yoetz" / "resources" / "skills" / "codex" / "yoetz" / "SKILL.md"
)
_NODE: Final = shutil.which("node")
_SAMPLES: Final = 10_000
# Every id prefix the section tells the agent to mint with the helper.
_DRAFT_PREFIXES: Final = ("evt", "act", "res", "evd", "clm", "obl")
_JS_BLOCK: Final = re.compile(r"^```js\n(.*?)^```$", re.MULTILINE | re.DOTALL)

needs_node = pytest.mark.skipif(_NODE is None, reason="node is a contributor-only tool")


def _section() -> str:
    text = _SKILL.read_text(encoding="utf-8")
    start = text.index("\n## Code mode\n")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def _blocks() -> list[str]:
    return _JS_BLOCK.findall(_section())


def _block_containing(marker: str) -> str:
    matches = [block for block in _blocks() if marker in block]
    assert len(matches) == 1, marker
    return matches[0]


def _walk_patterns(value: object) -> Iterator[str]:
    # Catalog schemas are frozen: mappings are read-only proxies and arrays are tuples.
    if isinstance(value, Mapping):
        for key, item in cast(Mapping[str, object], value).items():
            if key == "pattern" and isinstance(item, str):
                yield item
            else:
                yield from _walk_patterns(item)
    elif isinstance(value, tuple | list):
        for item in cast(tuple[object, ...] | list[object], value):
            yield from _walk_patterns(item)


def _schema_id_pattern(prefix: str) -> re.Pattern[str]:
    found = {
        pattern
        for document in load_schema_catalog().documents
        for pattern in _walk_patterns(document.json_schema)
        if pattern.startswith(f"^{prefix}_")
    }
    assert len(found) == 1, (prefix, found)
    return re.compile(found.pop())


def _defs_pattern(name: str, version: str, key: str) -> re.Pattern[str]:
    schema = schema_document_for(name, version).json_schema
    definitions = cast(Mapping[str, Mapping[str, str]], schema["$defs"])
    return re.compile(definitions[key]["pattern"])


def _run_node(tmp_path: Path, name: str, source: str) -> str:
    assert _NODE is not None
    script = tmp_path / name
    script.write_text(source, encoding="utf-8")
    completed = subprocess.run(
        [_NODE, str(script)],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


_GENERATE: Final = f"""
if (typeof crypto !== "undefined" || typeof globalThis.crypto !== "undefined") {{
  throw new Error("crypto is present");
}}
const minted = {{ req: [], {", ".join(f"{p}: []" for p in _DRAFT_PREFIXES)} }};
for (let i = 0; i < {_SAMPLES}; i++) {{
  for (const prefix of Object.keys(minted)) minted[prefix].push(newId(prefix));
}}
"""


def _vm_driver(snippet: str) -> str:
    # A fresh V8 context holds only the JavaScript builtins: no `crypto`, `require` or `process`.
    body = snippet + _GENERATE + "JSON.stringify(minted);\n"
    return (
        'const vm = require("node:vm");\n'
        f"const out = vm.runInContext({json.dumps(body)}, vm.createContext({{}}));\n"
        "process.stdout.write(out);\n"
    )


def _module_driver(snippet: str) -> str:
    # Code-mode cells run as ES modules (strict mode); remove the Node global before the snippet.
    return (
        "delete globalThis.crypto;\n"
        + snippet
        + _GENERATE
        + "process.stdout.write(JSON.stringify(minted));\n"
    )


def test_the_packaged_skill_carries_the_same_section() -> None:
    assert _PACKAGED_SKILL.read_text(encoding="utf-8") == _SKILL.read_text(encoding="utf-8")
    assert "\n## Code mode\n" in _SKILL.read_text(encoding="utf-8")


@needs_node
@pytest.mark.parametrize("runtime", ["vm-context", "es-module"])
def test_the_uuid_helper_mints_schema_valid_unique_ids_without_crypto(
    runtime: str, tmp_path: Path
) -> None:
    snippet = _block_containing("const uuid4 =")
    assert "crypto" not in snippet
    driver = _vm_driver(snippet) if runtime == "vm-context" else _module_driver(snippet)
    name = "cell.mjs" if runtime == "es-module" else "vm.cjs"
    minted = cast(dict[str, list[str]], json.loads(_run_node(tmp_path, name, driver)))
    request_pattern = _defs_pattern("publish-work-request", "1.2.0", "request_id")
    event_pattern = _defs_pattern("event-draft", "1.2.0", "event_id")
    assert request_pattern.pattern == _schema_id_pattern("req").pattern
    assert event_pattern.pattern == _schema_id_pattern("evt").pattern
    assert len(minted["req"]) == _SAMPLES
    assert all(request_pattern.fullmatch(value) for value in minted["req"])
    assert all(event_pattern.fullmatch(value) for value in minted["evt"])
    for prefix in _DRAFT_PREFIXES:
        pattern = _schema_id_pattern(prefix)
        assert len(minted[prefix]) == _SAMPLES
        bad = [value for value in minted[prefix] if pattern.fullmatch(value) is None]
        assert bad == [], (prefix, bad[:3])
    everything = [value for values in minted.values() for value in values]
    assert len(set(everything)) == len(everything)
    uuids = {value.split("_", 1)[1] for value in everything}
    assert len(uuids) == len(everything)


def test_the_request_id_pattern_still_rejects_the_hand_rolled_workaround() -> None:
    # Example 2 of #918: four Math.random slices joined and cut to 36 characters. The helper is
    # the fix; relaxing the pattern is not.
    request_pattern = _defs_pattern("publish-work-request", "1.2.0", "request_id")
    hand_rolled = "req_" + "-".join(["9f86d081"] * 4)[:36]
    assert request_pattern.fullmatch(hand_rolled) is None
    assert request_pattern.fullmatch("req_00000000-0000-4000-8000-000000000000") is not None
    assert request_pattern.fullmatch("req_00000000-0000-1000-8000-000000000000") is None
    assert request_pattern.fullmatch("req_00000000-0000-4000-c000-000000000000") is None


@needs_node
def test_the_discovery_pattern_prints_only_the_declaration(tmp_path: Path) -> None:
    snippet = _block_containing("exec tool declaration:")
    description = (
        "# Yoetz: call start first\\n\\nInstructions.\\n\\nTool description.\\n\\n"
        "exec tool declaration:\\ndeclare const start: (args: StartRequest) => Promise<unknown>;"
    )
    driver = (
        'const vm = require("node:vm");\n'
        "const printed = [];\n"
        "const context = vm.createContext({\n"
        f'  ALL_TOOLS: [{{ name: "mcp__yoetz__start", description: "{description}" }}],\n'
        "  text: (value) => printed.push(value),\n"
        "});\n"
        f"vm.runInContext({json.dumps(snippet)}, context);\n"
        "process.stdout.write(JSON.stringify(printed));\n"
    )
    printed = cast(list[str], json.loads(_run_node(tmp_path, "discover.cjs", driver)))
    assert printed == [
        "exec tool declaration:\ndeclare const start: (args: StartRequest) => Promise<unknown>;"
    ]


@needs_node
def test_every_code_mode_snippet_is_valid_javascript(tmp_path: Path) -> None:
    blocks = _blocks()
    assert len(blocks) == 4
    driver = (
        'const vm = require("node:vm");\n'
        f"for (const code of {json.dumps(blocks)}) new vm.Script(`(async () => {{\\n${{code}}\\n}})`);\n"
        'process.stdout.write("ok");\n'
    )
    assert _run_node(tmp_path, "syntax.cjs", driver) == "ok"


def test_guidance_is_read_from_the_structured_text() -> None:
    snippet = _block_containing("mcp__yoetz__read_guidance")
    assert "structuredContent.text" in snippet
    assert "structuredContent.text" in " ".join(_section().split())


def test_the_yield_guidance_matches_the_bridge_call_deadlines() -> None:
    deadlines = dict(bridge._DEFAULT_RPC_DEADLINES_MS)  # pyright: ignore[reportPrivateUsage]
    section = " ".join(_section().split())
    assert f"`check` {deadlines['check']}" in section
    assert f"`respond` {deadlines['respond']}" in section
    assert f"`receipt` {deadlines['receipt']}" in section
    assert deadlines["start"] == deadlines["publish_work"] == deadlines["status"]
    assert f"`start`, `publish_work` and `status` {deadlines['status']} each" in section
    assert "`yield_time_ms` at or above the call's deadline" in section
    # Scoped to Yoetz calls: a big yield must not stall builds or tests.
    assert "Keep builds, tests and other non-Yoetz work in their own cells" in section
    assert "not a series of short waits" in section
    example = _block_containing("mcp__yoetz__check")
    pragma = re.match(r'// @exec: \{"yield_time_ms": (\d+)\}\n', example)
    assert pragma is not None
    assert int(pragma.group(1)) >= deadlines["check"]
    # The example keeps its request id for same-request recovery.
    assert "text(JSON.stringify({ request_id, result: r.structuredContent }))" in example
