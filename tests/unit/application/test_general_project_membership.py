"""One general project for repository, workspace, and task memberships."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from conformance.adapters.test_project_catalog_memberships import _sqlite_catalog_v4
from conformance.adapters.test_start_catalog_port import (
    _Clock,
    _command,
    _id,
    _memory_catalog,
)
from yoetz.application.projects import (
    InMemoryProjectCatalog,
    ProjectApplication,
    ProjectCommandError,
)
from yoetz.cli.bootstrap import _COORDINATION_CONTROL_GUIDANCE
from yoetz.domain.coordination import (
    CoordinationErrorCode,
    GrantState,
    MemberKind,
    ProjectMembership,
)
from yoetz.ports.start_catalog import StartIdentityInput, TaskSourceProvenance
from yoetz.protocol.errors import PublicOperationError
from yoetz.protocol.ids import IdKind, new_id


def _commitment(letter: str) -> str:
    return "hmac-sha256:" + letter * 64


def _provenance(task_id: str, repository: str, workspace: str) -> TaskSourceProvenance:
    return TaskSourceProvenance(
        task_id=task_id,
        workspace_ref_commitment=workspace,
        external_ref_commitment=_commitment("c"),
        repository_privacy_commitment=repository,
        route_generation=1,
        route_identity_digest="sha256:" + "d" * 64,
    )


async def _granted_general(catalog: InMemoryProjectCatalog) -> str:
    project_id = new_id(IdKind.PROJECT)
    await catalog.create_general_project(project_id)
    await catalog.record_coordination_grant(
        project_id,
        1,
        grant_state=GrantState.ACTIVE,
        audit_ref="audit-general",
    )
    return project_id


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("kind", [MemberKind.REPOSITORY, MemberKind.WORKSPACE])
async def test_second_general_link_is_refused_for_repository_and_workspace(
    kind: MemberKind,
) -> None:
    catalog = InMemoryProjectCatalog()
    task_id = new_id(IdKind.TASK)
    repository = _commitment("a")
    workspace = _commitment("b")
    catalog.provenance[task_id] = _provenance(task_id, repository, workspace)
    app = ProjectApplication(catalog, ids=object())  # type: ignore[arg-type]
    first = await _granted_general(catalog)
    second = await _granted_general(catalog)
    member = repository if kind is MemberKind.REPOSITORY else workspace
    await app.link(project_id=first, member_kind=kind, member_commitment_or_id=member)
    with pytest.raises(ProjectCommandError) as refusal:
        await app.link(project_id=second, member_kind=kind, member_commitment_or_id=member)
    assert refusal.value.code is CoordinationErrorCode.GENERAL_MEMBERSHIP_CONFLICT
    assert await catalog.list_task_project_ids(task_id) == (first,)
    resolved = await app._resolve_project_for_task(task_id)
    assert resolved == first


@pytest.mark.anyio
async def test_cross_kind_and_late_provenance_stay_in_one_general_project() -> None:
    catalog = InMemoryProjectCatalog()
    repository = _commitment("a")
    workspace = _commitment("b")
    task_id = new_id(IdKind.TASK)
    app = ProjectApplication(catalog, ids=object(), workspace_consent=lambda _workspace: True)  # type: ignore[arg-type]
    first = await _granted_general(catalog)
    second = await _granted_general(catalog)
    await app.link(
        project_id=first,
        member_kind=MemberKind.REPOSITORY,
        member_commitment_or_id=repository,
    )
    catalog.provenance[task_id] = _provenance(task_id, repository, workspace)
    with pytest.raises(ProjectCommandError) as workspace_refusal:
        await app.link(
            project_id=second,
            member_kind=MemberKind.WORKSPACE,
            member_commitment_or_id=workspace,
        )
    assert workspace_refusal.value.code is CoordinationErrorCode.GENERAL_MEMBERSHIP_CONFLICT
    with pytest.raises(ProjectCommandError) as task_refusal:
        await app.link(
            project_id=second,
            member_kind=MemberKind.TASK,
            member_commitment_or_id=task_id,
            source_workspace_commitment=workspace,
        )
    assert task_refusal.value.code is CoordinationErrorCode.GENERAL_MEMBERSHIP_CONFLICT
    assert str(second) not in str(task_refusal.value)
    assert await catalog.list_task_project_ids(task_id) == (first,)


@pytest.mark.anyio
async def test_same_project_replay_unbind_and_implicit_repository_project() -> None:
    catalog = InMemoryProjectCatalog()
    repository = _commitment("a")
    workspace = _commitment("e")
    task_id = new_id(IdKind.TASK)
    catalog.provenance[task_id] = _provenance(task_id, repository, workspace)
    app = ProjectApplication(catalog, ids=object(), workspace_consent=lambda _workspace: True)  # type: ignore[arg-type]
    general = await _granted_general(catalog)
    repository_project = await catalog.ensure_repository_project(repository)
    first = await app.link(
        project_id=general,
        member_kind=MemberKind.REPOSITORY,
        member_commitment_or_id=repository,
    )
    await catalog.record_coordination_grant(
        general, 2, grant_state=GrantState.ACTIVE, audit_ref="audit-replay"
    )
    replay = await app.link(
        project_id=general,
        member_kind=MemberKind.REPOSITORY,
        member_commitment_or_id=repository,
    )
    assert replay.membership_generation == first.membership_generation
    linked = await catalog.list_task_project_ids(task_id)
    assert repository_project.project_id in linked
    assert general in linked
    assert await app._resolve_project_for_task(task_id) == general
    await app.unlink(
        project_id=general,
        member_kind=MemberKind.REPOSITORY,
        member_commitment_or_id=repository,
    )
    other = await _granted_general(catalog)
    await app.link(
        project_id=other,
        member_kind=MemberKind.REPOSITORY,
        member_commitment_or_id=repository,
    )
    assert await app._resolve_project_for_task(task_id) == other


@pytest.mark.anyio
async def test_preexisting_duplicate_rows_stay_and_status_names_the_repair() -> None:
    catalog = InMemoryProjectCatalog()
    repository = _commitment("a")
    task_id = new_id(IdKind.TASK)
    catalog.provenance[task_id] = _provenance(task_id, repository, _commitment("b"))
    first = await catalog.create_general_project(new_id(IdKind.PROJECT))
    second = await catalog.create_general_project(new_id(IdKind.PROJECT))
    for project in (first, second):
        catalog.projects[project.project_id].memberships.append(
            ProjectMembership(
                project.project_id,
                2,
                MemberKind.REPOSITORY,
                repository,
                datetime(2026, 9, 24, tzinfo=UTC),
            )
        )
    app = ProjectApplication(catalog, ids=object())  # type: ignore[arg-type]
    with pytest.raises(ProjectCommandError) as conflict:
        await app._resolve_project_for_task(task_id)
    assert conflict.value.code is CoordinationErrorCode.SELECTOR_CONFLICT
    assert await catalog.list_task_project_ids(task_id) == tuple(
        sorted((first.project_id, second.project_id))
    )
    remedy = _COORDINATION_CONTROL_GUIDANCE["selector_conflict"]
    assert "unlink" in remedy
    assert "dissolve" in remedy
    assert first.project_id not in remedy
    assert second.project_id not in remedy


@pytest.mark.anyio
@pytest.mark.parametrize("catalog_name", ["memory", "sqlite"])
async def test_catalogs_refuse_cross_kind_links_and_late_provenance(catalog_name: str) -> None:
    installation_id = _id(IdKind.INSTALLATION, 827)
    clock = _Clock(datetime(2026, 9, 24, tzinfo=UTC))
    if catalog_name == "memory":
        catalog, _state = _memory_catalog(installation_id, clock)
    else:
        catalog = _sqlite_catalog_v4(installation_id, clock)
    identity = await catalog.commit_identity(
        StartIdentityInput("Late provenance", "workspace-827", "external-827")
    )
    repository = _commitment("a")
    workspace = identity.workspace_ref_commitment
    assert workspace is not None
    first = _id(IdKind.PROJECT, 8271)
    second = _id(IdKind.PROJECT, 8272)
    await catalog.create_general_project(first)
    await catalog.create_general_project(second)
    await catalog.record_project_membership(
        first, member_kind=MemberKind.REPOSITORY, member_commitment_or_id=repository
    )
    replay = await catalog.record_project_membership(
        first, member_kind=MemberKind.REPOSITORY, member_commitment_or_id=repository
    )
    assert replay.project_id == first
    # No task connects this workspace to the repository yet, so the link is still one project.
    await catalog.record_project_membership(
        second, member_kind=MemberKind.WORKSPACE, member_commitment_or_id=workspace
    )
    with pytest.raises(PublicOperationError) as late:
        await catalog.reserve_or_resume(
            await _command(
                catalog,
                operation_id=_id(IdKind.REQUEST, 827),
                title="Late provenance",
                workspace_ref="workspace-827",
                external_ref="external-827",
                repository_privacy_commitment=repository,
            )
        )
    assert late.value.safe_details.get("reason_code") == "general_project_membership_conflict"
    assert first not in late.value.safe_details.values()
    assert second not in late.value.safe_details.values()


@pytest.mark.anyio
@pytest.mark.parametrize("catalog_name", ["memory", "sqlite"])
async def test_catalogs_refuse_a_workspace_once_a_task_connects_it(catalog_name: str) -> None:
    installation_id = _id(IdKind.INSTALLATION, 829)
    clock = _Clock(datetime(2026, 9, 24, tzinfo=UTC))
    if catalog_name == "memory":
        catalog, _state = _memory_catalog(installation_id, clock)
    else:
        catalog = _sqlite_catalog_v4(installation_id, clock)
    repository = _commitment("b")
    started = await catalog.reserve_or_resume(
        await _command(
            catalog,
            operation_id=_id(IdKind.REQUEST, 829),
            title="Connected task",
            workspace_ref="workspace-829",
            external_ref="external-829",
            repository_privacy_commitment=repository,
        )
    )
    source = await catalog.task_source_provenance(started.task_id)
    assert source is not None and source.workspace_ref_commitment is not None
    first = _id(IdKind.PROJECT, 8291)
    second = _id(IdKind.PROJECT, 8292)
    await catalog.create_general_project(first)
    await catalog.create_general_project(second)
    await catalog.record_project_membership(
        first, member_kind=MemberKind.REPOSITORY, member_commitment_or_id=repository
    )
    with pytest.raises(PublicOperationError) as refusal:
        await catalog.record_project_membership(
            second,
            member_kind=MemberKind.WORKSPACE,
            member_commitment_or_id=source.workspace_ref_commitment,
        )
    assert refusal.value.safe_details.get("reason_code") == "general_project_membership_conflict"
    assert await catalog.list_task_project_ids(started.task_id) == (first,)


@pytest.mark.anyio
async def test_concurrent_general_links_commit_only_one() -> None:
    installation_id = _id(IdKind.INSTALLATION, 828)
    catalog, _state = _memory_catalog(installation_id, _Clock(datetime(2026, 9, 24, tzinfo=UTC)))
    repository = _commitment("a")
    first = _id(IdKind.PROJECT, 8281)
    second = _id(IdKind.PROJECT, 8282)
    await catalog.create_general_project(first)
    await catalog.create_general_project(second)

    async def link(project_id: str) -> str:
        try:
            await catalog.record_project_membership(
                project_id,
                member_kind=MemberKind.REPOSITORY,
                member_commitment_or_id=repository,
            )
        except PublicOperationError as exc:
            return str(exc.safe_details.get("reason_code"))
        return "committed"

    results = await asyncio.gather(link(first), link(second))
    assert sorted(results) == ["committed", "general_project_membership_conflict"]
