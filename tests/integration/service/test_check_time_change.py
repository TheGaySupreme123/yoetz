"""The check-time change reaches the review packet through the real composition (#883, ADR-031).

The repository is a real Git workspace edited the way the v2 benchmark's kea attempt did: shell
Python rewrites and a commit, with no native edit tool at all, so no observation capture exists.
The semantic evaluator, durable job ledger, object store, redaction and case builder are the
production ones; only the privacy coordinator is the recording fake from the non-dispatch suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import integration.service.test_semantic_non_dispatch as non_dispatch
import yoetz.observability.diagnostics as diagnostics_module
import yoetz.service.ready_composition as ready_composition_module
from builders.ledger_adapters import (
    FixedClock,
    MemoryObjects,
    append_command,
    memory_adapter,
    sqlite_adapter,
)
from builders.policy_cases import (
    clm,
    make_case,
    obl,
    obligation_record,
    record,
)
from yoetz.adapters.git_change_capture import GitChangeCaptureAdapter
from yoetz.adapters.memory.ledger import MemoryLedgerAdapter
from yoetz.adapters.privacy.local_enforcer import scan_exact_bytes
from yoetz.adapters.sqlite.repository import SqliteLedger
from yoetz.application.check import FinalSemanticEvaluation
from yoetz.application.check_change import record_task_change_base
from yoetz.application.egress import PrivacyCoordinator
from yoetz.application.semantic_case import (
    CHECK_TIME_CHANGE_ITEM_PREFIX,
    REVIEW_PACKET_ITEM_ID,
)
from yoetz.domain.events import (
    ClaimKind,
    ClaimRecordedPayload,
    ObligationPublishedPayload,
    ObligationStatus,
)
from yoetz.domain.privacy import (
    CandidateContext,
    PrivacyOutcome,
    PrivacyReason,
    ProviderBinding,
    ReviewContextProfile,
)
from yoetz.domain.receipts import (
    CHECK_TIME_CHANGE_BASE_UNAVAILABLE_GAP,
    CHECK_TIME_CHANGE_REDACTED_GAP,
    CHECK_TIME_CHANGE_TRUNCATED_GAP,
    CHECK_TIME_CHANGE_UNAVAILABLE_GAP,
)
from yoetz.ports.change_capture import CheckWorkspaceSource, check_workspace_source_scope
from yoetz.ports.ledger import FrozenCase
from yoetz.ports.objects import ObjectKind
from yoetz.ports.runtime import TaskRuntime
from yoetz.ports.start_catalog import StartCatalogPort
from yoetz.protocol.canonical import strict_json_parse
from yoetz.protocol.models import SemanticStatus

type _Evaluator = Callable[
    [FrozenCase, tuple[object, ...], TaskRuntime], Awaitable[FinalSemanticEvaluation]
]

_REPOSITORY: str = getattr(non_dispatch, "_REPOSITORY")
_INSTALLATION: str = getattr(non_dispatch, "_INSTALLATION")
_PROVIDER: ProviderBinding = getattr(non_dispatch, "_PROVIDER")
_Privacy = getattr(non_dispatch, "_Privacy")
_Catalog = getattr(non_dispatch, "_Catalog")
_route_for = getattr(non_dispatch, "_route_for")
_durable_semantic_case = getattr(non_dispatch, "_durable_semantic_case")
_SECRET = "ghp_" + "Q" * 36


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        (
            "git",
            "-c",
            "user.name=Yoetz Test",
            "-c",
            "user.email=yoetz@example.invalid",
            *arguments,
        ),
        cwd=repository,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        env={
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": os.fspath(repository),
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": os.defpath,
        },
    )


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "kea"
    repository.mkdir(mode=0o700)
    _git(repository, "init", "--quiet")
    (repository / "atomic-selectors.ts").write_text(
        "export function trackKey(key: number | string) {\n"
        "  return dependencies.get(String(key));\n"
        "}\n",
        encoding="utf-8",
    )
    (repository / ".gitignore").write_text("local.env\n", encoding="utf-8")
    _git(repository, "add", "--", "atomic-selectors.ts", ".gitignore")
    _git(repository, "commit", "--quiet", "-m", "baseline")
    return repository


def _python_rewrite(repository: Path, old: str, new: str) -> None:
    """Edit like the kea attempt: ``open(p).read().replace(...)`` in a shell Python call."""

    script = (
        "import sys\n"
        "path, old, new = sys.argv[1:]\n"
        "text = open(path).read()\n"
        "assert old in text\n"
        "open(path, 'w').write(text.replace(old, new))\n"
    )
    subprocess.run(
        (sys.executable, "-c", script, "atomic-selectors.ts", old, new),
        cwd=repository,
        check=True,
        capture_output=True,
    )


def _evaluator(privacy: object, runtime: TaskRuntime) -> _Evaluator:
    async def resolve_provider() -> ProviderBinding | None:
        return _PROVIDER

    factory = cast(
        "Callable[..., _Evaluator]",
        getattr(ready_composition_module, "_privacy_gated_semantic_evaluator"),
    )
    return factory(
        cast(PrivacyCoordinator, privacy),
        FixedClock(),
        _INSTALLATION,
        resolve_provider,
        cast(StartCatalogPort, _Catalog(_route_for(runtime.task_id, runtime.session_id))),
        ready_composition_module.IdPort(),
        change_capture=GitChangeCaptureAdapter(),
    )


def _change_text(candidate: CandidateContext) -> str:
    parts = [
        item.plaintext.decode("utf-8")
        for item in candidate.items
        if item.item_id.startswith(CHECK_TIME_CHANGE_ITEM_PREFIX)
    ]
    assert parts, "the packet carries no check-time change"
    return "".join(parts)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", os.fspath(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(diagnostics_module, "log_dir", lambda: tmp_path / "logs")


def _with_claim(frozen: FrozenCase) -> FrozenCase:
    """The durable lease, over a case holding the claim and obligation the change serves."""

    obligation = obligation_record(
        ObligationPublishedPayload(
            obl(1), "Keep numeric and string keys apart", "tests pass", ObligationStatus.OPEN
        ),
        1,
    )
    claim = record(
        ClaimRecordedPayload(
            clm(1),
            ClaimKind.COMPLETION,
            "Atomic selectors now track numeric and string keys separately",
            (),
            obligation_refs=(obl(1),),
        ),
        1,
    )
    case = make_case(
        obligations={obl(1): obligation}, claims={clm(1): claim}, extra_refs=(clm(1), obl(1))
    )
    frontier = frozen.lease.frontier
    case = replace(
        case,
        projection=replace(
            case.projection, frontier=frontier.sequence, head_digest=frontier.head_digest
        ),
        frontier=frontier,
    )
    return FrozenCase(case, frozen.lease)


async def _kea_case(
    adapter: MemoryLedgerAdapter | SqliteLedger, repository: Path
) -> tuple[FrozenCase, TaskRuntime]:
    frozen, runtime = await _durable_semantic_case(adapter)
    assert await record_task_change_base(
        runtime=runtime,
        port=GitChangeCaptureAdapter(),
        workspace=os.fspath(repository),
        clock=FixedClock(),
    )
    return _with_claim(frozen), runtime


@pytest.mark.anyio
@pytest.mark.parametrize(
    "adapter_factory", (memory_adapter, sqlite_adapter), ids=("memory", "sqlite")
)
async def test_shell_rewrites_and_commits_reach_the_packet_and_survive_replay(
    tmp_path: Path,
    adapter_factory: Callable[[object], MemoryLedgerAdapter | SqliteLedger],
) -> None:
    repository = _repository(tmp_path)
    adapter = adapter_factory(append_command())
    frozen, runtime = await _kea_case(adapter, repository)

    # 34 rewrites in the benchmark; two here, one committed and one left in the working tree.
    _python_rewrite(repository, "String(key)", "`${typeof key}:${String(key)}`")
    _git(repository, "commit", "--quiet", "-am", "keep numeric and string keys apart")
    _python_rewrite(repository, "dependencies.get", "dependencyMap.get")
    (repository / "atomic-selectors.test.ts").write_text(
        f"const fixtureToken = '{_SECRET}';\nexpect(trackKey(1)).not.toBe(trackKey('1'));\n",
        encoding="utf-8",
    )
    (repository / "local.env").write_text("IGNORED_CANARY=1\n", encoding="utf-8")

    privacy = _Privacy(task_id=runtime.task_id, profile=ReviewContextProfile.EXPANDED)
    evaluator = _evaluator(privacy, runtime)
    source = CheckWorkspaceSource(os.fspath(repository), _REPOSITORY)
    with check_workspace_source_scope(source):
        waiting = await evaluator(frozen, (), runtime)

    assert waiting.status is SemanticStatus.AWAITING_HUMAN
    candidate = privacy.candidates[0]
    envelope = strict_json_parse(
        next(item.plaintext for item in candidate.items if item.item_id == REVIEW_PACKET_ITEM_ID)
    )
    assert isinstance(envelope, dict)
    packet = cast(dict[str, object], envelope["review_packet"])
    excerpts = cast(list[dict[str, object]], packet["targeted_excerpts"])
    assert excerpts[0]["excerpt_item_id"] == f"{CHECK_TIME_CHANGE_ITEM_PREFIX}001"
    text = _change_text(candidate)
    assert "Base: the commit HEAD named when this task started." in text
    assert "`${typeof key}:${String(key)}`" in text  # committed rewrite
    assert "dependencyMap.get" in text  # uncommitted rewrite
    assert "expect(trackKey(1)).not.toBe(trackKey('1'));" in text  # untracked file
    assert "IGNORED_CANARY" not in text and "local.env" not in text
    # Capture-time redaction ran with the same detector as the egress never-send scan.
    assert _SECRET not in text and "[REDACTED]" in text
    assert all(scan_exact_bytes(item.plaintext) == () for item in candidate.items)
    assert CHECK_TIME_CHANGE_REDACTED_GAP in waiting.case_content_gaps
    assert CHECK_TIME_CHANGE_BASE_UNAVAILABLE_GAP not in waiting.case_content_gaps
    objects = cast(MemoryObjects, getattr(adapter, "_objects"))
    assert len(objects.refs_for_kind(ObjectKind.CHANGE_CAPTURE)) == 2  # base + one change

    # The agent keeps editing while the disclosure waits. The replay must review the frozen
    # capture, not re-read the tree: same case digest, same object, same packet text.
    _python_rewrite(repository, "dependencyMap.get", "dependencyMapAfterWait.get")
    assert waiting.operation_lease is not None
    privacy.resume_terminal = (PrivacyOutcome.HUMAN_DENIED, PrivacyReason.HUMAN_DENIED)
    with check_workspace_source_scope(source):
        resumed = await evaluator(FrozenCase(frozen.case, waiting.operation_lease), (), runtime)

    assert resumed.status is SemanticStatus.HUMAN_DENIED
    assert privacy.resume_calls == 1
    assert resumed.case_content_gaps == waiting.case_content_gaps
    assert len(objects.refs_for_kind(ObjectKind.CHANGE_CAPTURE)) == 2


@pytest.mark.anyio
async def test_connection_for_another_repository_is_never_read(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    adapter = memory_adapter(append_command())
    frozen, runtime = await _kea_case(adapter, repository)
    _python_rewrite(repository, "String(key)", "OTHER_REPOSITORY_CANARY")
    privacy = _Privacy(task_id=runtime.task_id, profile=ReviewContextProfile.EXPANDED)

    other = CheckWorkspaceSource(os.fspath(repository), "hmac-sha256:" + "e" * 64)
    with check_workspace_source_scope(other):
        waiting = await _evaluator(privacy, runtime)(frozen, (), runtime)

    assert CHECK_TIME_CHANGE_UNAVAILABLE_GAP in waiting.case_content_gaps
    candidate = privacy.candidates[0]
    assert not any(
        item.item_id.startswith(CHECK_TIME_CHANGE_ITEM_PREFIX) for item in candidate.items
    )
    assert all(b"OTHER_REPOSITORY_CANARY" not in item.plaintext for item in candidate.items)
    objects = cast(MemoryObjects, getattr(adapter, "_objects"))
    assert len(objects.refs_for_kind(ObjectKind.CHANGE_CAPTURE)) == 1  # the base only


@pytest.mark.anyio
async def test_task_without_a_recorded_base_discloses_the_head_base(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    frozen, runtime = await _durable_semantic_case(memory_adapter(append_command()))
    frozen = _with_claim(frozen)
    _python_rewrite(repository, "String(key)", "HEAD_BASE_MARKER")
    privacy = _Privacy(task_id=runtime.task_id, profile=ReviewContextProfile.EXPANDED)

    with check_workspace_source_scope(CheckWorkspaceSource(os.fspath(repository), _REPOSITORY)):
        waiting = await _evaluator(privacy, runtime)(frozen, (), runtime)

    assert CHECK_TIME_CHANGE_BASE_UNAVAILABLE_GAP in waiting.case_content_gaps
    text = _change_text(privacy.candidates[0])
    assert "HEAD_BASE_MARKER" in text
    assert "commits made during the task may be missing" in text


@pytest.mark.anyio
async def test_change_larger_than_the_packet_is_truncated_and_disclosed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    adapter = memory_adapter(append_command())
    frozen, runtime = await _kea_case(adapter, repository)
    # About 150 KiB of diff: more than the whole packet's excerpt budget can carry.
    for index in range(20):
        (repository / f"module_{index:02d}.ts").write_text(
            "".join(f"export const value{line} = {line};\n" for line in range(240)),
            encoding="utf-8",
        )
    privacy = _Privacy(task_id=runtime.task_id, profile=ReviewContextProfile.EXPANDED)

    with check_workspace_source_scope(CheckWorkspaceSource(os.fspath(repository), _REPOSITORY)):
        waiting = await _evaluator(privacy, runtime)(frozen, (), runtime)

    assert CHECK_TIME_CHANGE_TRUNCATED_GAP in waiting.case_content_gaps
    text = _change_text(privacy.candidates[0])
    # The header names every changed file, including the ones whose hunks did not fit.
    for index in range(20):
        assert f"  A module_{index:02d}.ts (+240 -0) untracked" in text
    assert "[Yoetz check-time change, part 1 of " in text


@pytest.mark.anyio
async def test_no_trusted_workspace_source_leaves_the_case_as_before(tmp_path: Path) -> None:
    del tmp_path
    frozen, runtime = await _durable_semantic_case(memory_adapter(append_command()))
    frozen = _with_claim(frozen)
    privacy = _Privacy(task_id=runtime.task_id, profile=ReviewContextProfile.EXPANDED)

    waiting = await _evaluator(privacy, runtime)(frozen, (), runtime)

    assert not {
        CHECK_TIME_CHANGE_UNAVAILABLE_GAP,
        CHECK_TIME_CHANGE_BASE_UNAVAILABLE_GAP,
    } & set(waiting.case_content_gaps)
    assert not any(
        item.item_id.startswith(CHECK_TIME_CHANGE_ITEM_PREFIX)
        for item in privacy.candidates[0].items
    )
