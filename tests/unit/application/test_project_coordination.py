"""Focused project coordination lifecycle and admission tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest

from builders.ledger_adapters import FixedClock, FixedIds, MemoryObjects
from yoetz.application.coordination import (
    CoordinationDetector,
    CoordinationParticipant,
    CoordinationRuntime,
    DeclaredCoordinationInput,
    EncryptedCoordinationDetailStore,
    InMemoryCoordinationStore,
    LedgerCoordinationInputProvider,
)
from yoetz.application.projects import (
    CreateProjectCommand,
    EncryptedProjectTextStore,
    InMemoryProjectCatalog,
    ProjectApplication,
    ProjectCommandError,
    ProjectGrantCommand,
    ProjectRevokeCommand,
    ProjectStatus,
    RoutedEncryptedProjectTextStore,
)
from yoetz.domain.coordination import (
    COORDINATION_DETAIL_FORMAT,
    CoordinationDetection,
    CoordinationError,
    CoordinationErrorCode,
    MemberKind,
    OverlapKind,
    ProjectTextRef,
    SessionHealth,
    WorkState,
    canonical_resource_identity,
    coordination_detection_identity,
)
from yoetz.domain.events import CoordinationObligationDeclaredPayload
from yoetz.domain.privacy import DataCategory, DataClass, LocalDisclosureSink
from yoetz.domain.values import JsonObject, event_id, obligation_id, task_id
from yoetz.ports.objects import ObjectKind
from yoetz.ports.start_catalog import SessionState, TaskSourceProvenance
from yoetz.protocol.canonical import canonical_digest
from yoetz.protocol.ids import IdKind, new_id


def _commitment(seed: str) -> str:
    return "hmac-sha256:" + seed * 64


@dataclass
class _TextStore:
    ids: FixedIds

    async def put(
        self,
        project_id: str,
        field: str,
        plaintext: str,
        *,
        owner_task_id: str,
        route_generation: int,
    ) -> ProjectTextRef:
        del field
        return ProjectTextRef(
            self.ids.new(IdKind.OBJECT),
            canonical_digest({"project_id": project_id, "text": plaintext}),
            len(plaintext.encode()),
            owner_task_id,
            route_generation,
            "sha256:" + "a" * 64,
        )

    async def read(self, reference: ProjectTextRef) -> str:
        del reference
        return "redacted"


class _Catalog(InMemoryProjectCatalog):
    def __init__(self, provenance: dict[str, TaskSourceProvenance]) -> None:
        super().__init__()
        self.provenance = provenance

    async def task_source_provenance(self, task_id: str) -> TaskSourceProvenance | None:
        return self.provenance.get(task_id)


@dataclass
class _GrantApproval:
    approved: bool = True

    async def authorize(
        self,
        project_id: str,
        membership_generation: int,
        action: str,
        audit_record_id: str,
    ) -> bool:
        assert project_id.startswith("prj_")
        assert membership_generation > 0
        assert action in {"grant", "revoke"}
        assert audit_record_id.startswith("evt_")
        return self.approved

    async def consume(
        self,
        project_id: str,
        membership_generation: int,
        action: str,
        audit_record_id: str,
    ) -> None:
        assert project_id.startswith("prj_")
        assert membership_generation > 0
        assert action == "grant"
        assert audit_record_id.startswith("evt_")


def _allow_text_disclosure(*_args: object) -> bool:
    return True


def _allow_coordination_source(*_args: object) -> bool:
    return True


def _provenance(task_id: str, workspace: str, repository: str) -> TaskSourceProvenance:
    return TaskSourceProvenance(
        task_id,
        workspace,
        _commitment("e"),
        repository,
        1,
        "sha256:" + "b" * 64,
    )


@pytest.mark.anyio
async def test_general_project_lifecycle_is_generation_fenced() -> None:
    ids = FixedIds()
    task_one = new_id(IdKind.TASK)
    task_two = new_id(IdKind.TASK)
    workspace_one = _commitment("1")
    workspace_two = _commitment("2")
    repository = _commitment("3")
    catalog = _Catalog(
        {
            task_one: _provenance(task_one, workspace_one, repository),
            task_two: _provenance(task_two, workspace_two, repository),
        }
    )
    consent = {workspace_one: True, workspace_two: True}
    app = ProjectApplication(
        catalog,
        ids=ids,
        clock=FixedClock(),
        text_store=_TextStore(ids),
        workspace_consent=lambda workspace: consent.get(workspace, False),
        grant_authorizer=_GrantApproval(),
        text_disclosure_authorizer=_allow_text_disclosure,
        coordination_source_authorizer=_allow_coordination_source,
    )

    project = await app.create(
        CreateProjectCommand(
            "Research",
            owner_task_id=task_one,
            owner_route_generation=1,
        )
    )
    assert project.title_ref is not None
    assert "Research" not in str(project.as_wire())

    await app.grant(ProjectGrantCommand(project.project_id, 1))
    member_one = await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_one,
        source_workspace_commitment=workspace_one,
    )
    assert member_one.member_commitment_or_id == task_one
    project = await catalog.project_state(project.project_id)
    assert project is not None and project.membership_generation == 2

    await app.grant(ProjectGrantCommand(project.project_id, 2))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_two,
        source_workspace_commitment=workspace_two,
    )
    project = await catalog.project_state(project.project_id)
    assert project is not None and project.membership_generation == 3
    await app.grant(ProjectGrantCommand(project.project_id, 3))

    view = await app.status_for_task(task_one, project=project.project_id)
    assert isinstance(view, ProjectStatus)
    assert view.project.project_id == project.project_id
    assert {item.task_id for item in view.memberships} == {task_one, task_two}

    revoked = await app.revoke(ProjectRevokeCommand(project.project_id, 3))
    assert revoked.state.value == "revoked"
    with pytest.raises(ProjectCommandError) as error:
        await app.admit(
            source_task_id=task_one,
            source_workspace_commitment=workspace_one,
            project=project.project_id,
            expected_generation=3,
        )
    assert error.value.code is CoordinationErrorCode.GRANT_REVOKED


@pytest.mark.anyio
async def test_prebirth_opt_out_does_not_materialize_implicit_project() -> None:
    catalog = InMemoryProjectCatalog()
    app = ProjectApplication(catalog, ids=FixedIds())
    repository = _commitment("a")

    assert await catalog.repository_auto_grouping_enabled(repository) is True
    assert await app.opt_out(repository) is None
    assert catalog.projects == {}
    assert await catalog.repository_auto_grouping_enabled(repository) is False
    assert await catalog.ensure_repository_project_if_auto_grouping_enabled(repository) is None
    assert catalog.projects == {}
    project = await catalog.ensure_repository_project(repository)
    assert project.auto_grouping is False
    assert await catalog.repository_auto_grouping_enabled(repository) is False
    assert await app.opt_in(repository) is not None
    project = await catalog.repository_state(repository)
    assert project is not None and project.auto_grouping is True
    assert await catalog.repository_auto_grouping_enabled(repository) is True


@pytest.mark.anyio
async def test_status_omits_member_without_own_workspace_consent() -> None:
    ids = FixedIds()
    task_one = new_id(IdKind.TASK)
    task_two = new_id(IdKind.TASK)
    workspace_one = _commitment("4")
    workspace_two = _commitment("5")
    repository = _commitment("6")
    catalog = _Catalog(
        {
            task_one: _provenance(task_one, workspace_one, repository),
            task_two: _provenance(task_two, workspace_two, repository),
        }
    )
    consent = {workspace_one: True, workspace_two: True}
    app = ProjectApplication(
        catalog,
        ids=ids,
        text_store=_TextStore(ids),
        workspace_consent=lambda workspace: consent.get(workspace, False),
        grant_authorizer=_GrantApproval(),
        text_disclosure_authorizer=_allow_text_disclosure,
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await app.create(
        title="Private",
        owner_task_id=task_one,
        owner_route_generation=1,
    )
    await app.grant(ProjectGrantCommand(project.project_id, 1))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_one,
        source_workspace_commitment=workspace_one,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_two,
        source_workspace_commitment=workspace_two,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))

    consent[workspace_two] = False
    view = await app.status_for_task(task_one, project=project.project_id)
    assert isinstance(view, ProjectStatus)
    assert {item.task_id for item in view.memberships} == {task_one}


@pytest.mark.anyio
async def test_live_member_identity_is_fenced_when_consent_revokes_during_read() -> None:
    task_one = new_id(IdKind.TASK)
    task_two = new_id(IdKind.TASK)
    workspace_one = _commitment("a")
    workspace_two = _commitment("b")
    repository = _commitment("c")
    consent = {workspace_one: True, workspace_two: True}

    class _RevokingCatalog(_Catalog):
        async def task_work_state(self, task_id: str) -> WorkState:
            if task_id == task_two:
                # This runs after _authorized_member_views admits task_two and before it returns
                # the task's identity.  The final source fence must drop that stale identity.
                consent[workspace_two] = False
            return WorkState.OPEN

        async def task_session_states(self, task_id: str) -> tuple[SessionState, ...]:
            return (
                SessionState(
                    task_id,
                    new_id(IdKind.SESSION),
                    SessionHealth.ACTIVE,
                    datetime(2026, 9, 5, tzinfo=UTC),
                ),
            )

    catalog = _RevokingCatalog(
        {
            task_one: _provenance(task_one, workspace_one, repository),
            task_two: _provenance(task_two, workspace_two, repository),
        }
    )
    app = ProjectApplication(
        catalog,
        ids=FixedIds(),
        text_store=_TextStore(FixedIds()),
        workspace_consent=lambda workspace: consent.get(workspace, False),
        grant_authorizer=_GrantApproval(),
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await app.create(
        title="revocation race",
        owner_task_id=task_one,
        owner_route_generation=1,
    )
    await app.grant(ProjectGrantCommand(project.project_id, 1))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_one,
        source_workspace_commitment=workspace_one,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_two,
        source_workspace_commitment=workspace_two,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))

    assert (
        await app.live_admitted_member_task_ids(
            task_one,
            project=project.project_id,
            expected_generation=current.membership_generation,
        )
        == ()
    )


@pytest.mark.anyio
async def test_admission_cannot_substitute_another_consented_workspace() -> None:
    task = new_id(IdKind.TASK)
    actual_workspace = _commitment("7")
    other_workspace = _commitment("8")
    repository = _commitment("9")
    catalog = _Catalog({task: _provenance(task, actual_workspace, repository)})
    app = ProjectApplication(
        catalog,
        ids=FixedIds(),
        workspace_consent=lambda workspace: workspace == other_workspace,
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await catalog.ensure_repository_project(repository)
    with pytest.raises(ProjectCommandError) as error:
        await app.admit(
            source_task_id=task,
            source_workspace_commitment=other_workspace,
            project=project.project_id,
        )
    assert error.value.code is CoordinationErrorCode.CONSENT_REQUIRED


@pytest.mark.anyio
async def test_project_text_read_admits_requester_and_text_owner_separately() -> None:
    ids = FixedIds()
    owner = new_id(IdKind.TASK)
    reader = new_id(IdKind.TASK)
    owner_workspace = _commitment("1")
    reader_workspace = _commitment("2")
    repository = _commitment("3")
    catalog = _Catalog(
        {
            owner: _provenance(owner, owner_workspace, repository),
            reader: _provenance(reader, reader_workspace, repository),
        }
    )
    consent = {owner_workspace: True, reader_workspace: True}
    app = ProjectApplication(
        catalog,
        ids=ids,
        text_store=_TextStore(ids),
        workspace_consent=lambda workspace: consent.get(workspace, False),
        grant_authorizer=_GrantApproval(),
        text_disclosure_authorizer=_allow_text_disclosure,
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await app.create(
        title="source-bound",
        owner_task_id=owner,
        owner_route_generation=1,
    )
    await app.grant(ProjectGrantCommand(project.project_id, 1))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=owner,
        source_workspace_commitment=owner_workspace,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=reader,
        source_workspace_commitment=reader_workspace,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))
    assert (
        await app.project_text_for(reader, project=project.project_id, field="title") == "redacted"
    )
    consent[owner_workspace] = False
    with pytest.raises(ProjectCommandError) as error:
        await app.project_text_for(reader, project=project.project_id, field="title")
    assert error.value.code is CoordinationErrorCode.CONSENT_REQUIRED


@pytest.mark.anyio
async def test_repository_project_text_owner_must_match_repository_identity() -> None:
    ids = FixedIds()
    owner = new_id(IdKind.TASK)
    foreign = new_id(IdKind.TASK)
    workspace_owner = _commitment("1")
    workspace_foreign = _commitment("2")
    repository = _commitment("3")
    foreign_repository = _commitment("4")
    catalog = _Catalog(
        {
            owner: _provenance(owner, workspace_owner, repository),
            foreign: _provenance(foreign, workspace_foreign, foreign_repository),
        }
    )
    app = ProjectApplication(
        catalog,
        ids=ids,
        text_store=_TextStore(ids),
        workspace_consent=lambda _workspace: True,
        text_disclosure_authorizer=_allow_text_disclosure,
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await catalog.ensure_repository_project(repository)
    foreign_ref = await _TextStore(ids).put(
        project.project_id,
        "title",
        "foreign title",
        owner_task_id=foreign,
        route_generation=1,
    )
    await catalog.record_project_text_refs(
        project.project_id,
        title_ref=foreign_ref,
        description_ref=None,
    )

    with pytest.raises(ProjectCommandError) as error:
        await app.project_text_for(owner, project=project.project_id, field="title")
    assert error.value.code is CoordinationErrorCode.CONSENT_REQUIRED


@pytest.mark.anyio
async def test_general_project_text_amend_requires_current_member_grant() -> None:
    ids = FixedIds()
    owner = new_id(IdKind.TASK)
    workspace = _commitment("5")
    repository = _commitment("6")
    catalog = _Catalog({owner: _provenance(owner, workspace, repository)})
    app = ProjectApplication(
        catalog,
        ids=ids,
        text_store=_TextStore(ids),
        workspace_consent=lambda _workspace: True,
        grant_authorizer=_GrantApproval(),
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await app.create(
        title="initial",
        owner_task_id=owner,
        owner_route_generation=1,
    )
    await app.grant(ProjectGrantCommand(project.project_id, 1))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=owner,
        source_workspace_commitment=workspace,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None and current.membership_generation == 2

    with pytest.raises(ProjectCommandError) as error:
        await app.amend(
            project_id=project.project_id, title="without current grant", owner_task_id=owner
        )
    assert error.value.code is CoordinationErrorCode.GRANT_REVOKED

    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))
    amended = await app.amend(
        project_id=project.project_id, title="with current grant", owner_task_id=owner
    )
    assert amended.title_ref is not None
    assert amended.title_ref.owner_task_id == owner


@pytest.mark.anyio
async def test_configured_source_policy_denial_blocks_coordination_admission() -> None:
    task = new_id(IdKind.TASK)
    workspace = _commitment("7")
    repository = _commitment("8")
    catalog = _Catalog({task: _provenance(task, workspace, repository)})
    configured_categories = {DataCategory.BOUNDED_STRUCTURAL_METADATA}
    configured_classes = {DataClass.PUBLIC_STRUCTURAL}

    async def source_policy(
        _task_id: str,
        _workspace: str,
        _project: str,
    ) -> bool:
        return {
            DataCategory.BOUNDED_STRUCTURAL_METADATA,
            DataCategory.FINDING_SUMMARY,
        }.issubset(configured_categories) and DataClass.PUBLIC_STRUCTURAL in configured_classes

    app = ProjectApplication(
        catalog,
        ids=FixedIds(),
        workspace_consent=lambda _workspace: True,
        coordination_source_authorizer=source_policy,
    )
    project = await catalog.ensure_repository_project(repository)
    with pytest.raises(ProjectCommandError) as error:
        await app.admit(
            source_task_id=task,
            source_workspace_commitment=workspace,
            project=project.project_id,
        )
    assert error.value.code is CoordinationErrorCode.CONSENT_REQUIRED

    configured_categories.add(DataCategory.FINDING_SUMMARY)
    admission = await app.admit(
        source_task_id=task,
        source_workspace_commitment=workspace,
        project=project.project_id,
    )
    assert admission.allowed


@pytest.mark.anyio
async def test_coordination_resource_detail_requires_both_source_owner_policies() -> None:
    ids = FixedIds()
    left_task = new_id(IdKind.TASK)
    right_task = new_id(IdKind.TASK)
    workspace_left = _commitment("9")
    workspace_right = _commitment("a")
    repository = _commitment("b")

    class _RotatingCatalog(_Catalog):
        route_generation = 1

        async def task_route_generation(self, task_id: str) -> int:
            del task_id
            return self.route_generation

    catalog = _RotatingCatalog(
        {
            left_task: _provenance(left_task, workspace_left, repository),
            right_task: _provenance(right_task, workspace_right, repository),
        }
    )
    resource_calls: list[str] = []
    allowed_resource_tasks = {left_task}

    async def resource_policy(
        task_id_value: str,
        _workspace: str,
        sink: object,
        _purpose: str,
    ) -> bool:
        resource_calls.append(task_id_value)
        return sink is not None and task_id_value in allowed_resource_tasks

    app = ProjectApplication(
        catalog,
        ids=ids,
        workspace_consent=lambda _workspace: True,
        coordination_source_authorizer=_allow_coordination_source,
        coordination_resource_disclosure_authorizer=resource_policy,
    )
    project = await catalog.ensure_repository_project(repository)
    detection_id = new_id(IdKind.EVENT)
    detail_ref = await _TextStore(ids).put(
        project.project_id,
        "description",
        "details",
        owner_task_id=left_task,
        route_generation=1,
    )
    resource_identity = canonical_resource_identity("src/a.py", repository_commitment=repository)
    detection = CoordinationDetection(
        detection_id,
        project.project_id,
        1,
        left_task,
        right_task,
        OverlapKind.INTEGRATION,
        (resource_identity,),
        right_task,
        detail_ref=detail_ref,
    )
    store = InMemoryCoordinationStore()
    await store.put_detection(detection)
    app.detection_store = store

    class _DetailReader:
        async def read_details(self, reference: ProjectTextRef) -> JsonObject:
            assert reference == detail_ref
            return JsonObject(
                {
                    "left_resources": ("src/a.py",),
                    "right_resources": ("src/a.py",),
                }
            )

    app.coordination_detail_reader = _DetailReader()
    withheld = await app.coordination_resource_detail_for(
        left_task,
        project=project.project_id,
        detection_id=detection_id,
        sink=LocalDisclosureSink.AGENT_CONTEXT,
    )
    assert withheld is not None
    assert withheld.resource_paths is None
    assert withheld.source_disclosure_permitted is False
    assert resource_calls == [left_task, right_task]

    allowed_resource_tasks.add(right_task)
    resource_calls.clear()
    revealed = await app.coordination_resource_detail_for(
        left_task,
        project=project.project_id,
        detection_id=detection_id,
        sink=LocalDisclosureSink.AGENT_CONTEXT,
    )
    assert revealed is not None
    assert revealed.resource_paths == ("src/a.py",)
    assert revealed.source_disclosure_permitted is True
    assert resource_calls == [left_task, right_task, left_task, right_task]

    class _RevokingDetailReader:
        async def read_details(self, reference: ProjectTextRef) -> JsonObject:
            assert reference == detail_ref
            allowed_resource_tasks.clear()
            return JsonObject(
                {
                    "left_resources": ("src/a.py",),
                    "right_resources": ("src/a.py",),
                }
            )

    resource_calls.clear()
    allowed_resource_tasks.update({left_task, right_task})
    app.coordination_detail_reader = _RevokingDetailReader()
    revoked_after_read = await app.coordination_resource_detail_for(
        left_task,
        project=project.project_id,
        detection_id=detection_id,
        sink=LocalDisclosureSink.AGENT_CONTEXT,
    )
    assert revoked_after_read is not None
    assert revoked_after_read.resource_paths is None
    assert revoked_after_read.source_disclosure_permitted is False
    assert resource_calls == [left_task, right_task, left_task, right_task]

    case_insensitive_detection_id = new_id(IdKind.EVENT)
    case_insensitive_identity = canonical_resource_identity(
        "Src/A.py",
        repository_commitment=repository,
        case_sensitive=False,
    )
    case_insensitive_detection = CoordinationDetection(
        case_insensitive_detection_id,
        project.project_id,
        1,
        left_task,
        right_task,
        OverlapKind.INTEGRATION,
        (case_insensitive_identity,),
        right_task,
        detail_ref=detail_ref,
    )
    await store.put_detection(case_insensitive_detection)

    class _CaseInsensitiveDetailReader:
        async def read_details(self, reference: ProjectTextRef) -> JsonObject:
            assert reference == detail_ref
            return JsonObject(
                {
                    "left_resources": ("Src/A.py",),
                    "right_resources": ("src/a.py",),
                    "case_sensitive": False,
                }
            )

    resource_calls.clear()
    allowed_resource_tasks.update({left_task, right_task})
    app.coordination_detail_reader = _CaseInsensitiveDetailReader()
    case_insensitive = await app.coordination_resource_detail_for(
        left_task,
        project=project.project_id,
        detection_id=case_insensitive_detection_id,
        sink=LocalDisclosureSink.AGENT_CONTEXT,
    )
    assert case_insensitive is not None
    assert case_insensitive.resource_paths == ("Src/A.py",)
    assert case_insensitive.source_disclosure_permitted is True

    class _RotatingDetailReader:
        async def read_details(self, reference: ProjectTextRef) -> JsonObject:
            assert reference == detail_ref
            catalog.route_generation = 2
            return JsonObject(
                {
                    "left_resources": ("Src/A.py",),
                    "right_resources": ("src/a.py",),
                    "case_sensitive": False,
                }
            )

    resource_calls.clear()
    app.coordination_detail_reader = _RotatingDetailReader()
    rotated_after_read = await app.coordination_resource_detail_for(
        left_task,
        project=project.project_id,
        detection_id=case_insensitive_detection_id,
        sink=LocalDisclosureSink.AGENT_CONTEXT,
    )
    assert rotated_after_read is not None
    assert rotated_after_read.resource_paths is None
    assert rotated_after_read.source_disclosure_permitted is False
    assert resource_calls == [left_task, right_task, left_task, right_task]


@pytest.mark.anyio
async def test_repository_membership_expands_to_admitted_tasks_and_commitment_resources() -> None:
    ids = FixedIds()
    task_one = new_id(IdKind.TASK)
    task_two = new_id(IdKind.TASK)
    repository = _commitment("a")
    workspace_one = _commitment("b")
    workspace_two = _commitment("c")
    catalog = _Catalog(
        {
            task_one: _provenance(task_one, workspace_one, repository),
            task_two: _provenance(task_two, workspace_two, repository),
        }
    )
    app = ProjectApplication(
        catalog,
        ids=ids,
        text_store=_TextStore(ids),
        workspace_consent=lambda _workspace: True,
        grant_authorizer=_GrantApproval(),
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await app.create(
        title="Repository group",
        owner_task_id=task_one,
        owner_route_generation=1,
    )
    await app.grant(ProjectGrantCommand(project.project_id, 1))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.REPOSITORY,
        member_commitment_or_id=repository,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))

    view = await app.project_view_for(task_two, project=project.project_id)
    assert isinstance(view, ProjectStatus)
    assert {item.task_id for item in view.memberships} == {task_one, task_two}

    detector_store = InMemoryCoordinationStore()
    app_with_detections = ProjectApplication(
        catalog,
        ids=ids,
        text_store=_TextStore(ids),
        workspace_consent=lambda _workspace: True,
        grant_authorizer=_GrantApproval(),
        detection_store=detector_store,
        coordination_source_authorizer=_allow_coordination_source,
    )
    detection_id = new_id(IdKind.EVENT)
    detection = CoordinationDetection(
        detection_id,
        project.project_id,
        current.membership_generation,
        task_one,
        task_two,
        # A direct construction here models the detector's structural output.
        OverlapKind.INTEGRATION,
        (canonical_resource_identity("src/a.py", repository_commitment=repository),),
        task_two,
    )
    await detector_store.put_detection(detection)
    visible = await app_with_detections.project_detections_for(
        project.project_id,
        visible_task_ids=(task_one, task_two),
        expected_generation=current.membership_generation,
    )
    assert visible == (detection,)
    assert "src/a.py" not in str(detection.as_wire())
    status = await app_with_detections.project_view_for(task_two, project=project.project_id)
    assert isinstance(status, ProjectStatus)
    assert len(status.detections) == 1
    assert status.detections[0]["detection_id"] == detection_id
    assert set(cast(list[str], status.detections[0]["task_ids"])) == {task_one, task_two}


def test_forged_authority_field_is_rejected() -> None:
    with pytest.raises(ProjectCommandError) as error:
        from yoetz.application.projects import project_request_from_json

        project_request_from_json(
            {"operation": "grant", "project_id": new_id(IdKind.PROJECT), "authority": "local_human"}
        )
    assert error.value.code is CoordinationErrorCode.GRANT_REQUIRED


@pytest.mark.anyio
async def test_detector_has_one_identity_and_two_idempotent_deliveries() -> None:
    ids = FixedIds()
    task_one = new_id(IdKind.TASK)
    task_two = new_id(IdKind.TASK)
    repository = _commitment("7")
    workspace_one = _commitment("8")
    workspace_two = _commitment("9")
    catalog = _Catalog(
        {
            task_one: _provenance(task_one, workspace_one, repository),
            task_two: _provenance(task_two, workspace_two, repository),
        }
    )
    app = ProjectApplication(
        catalog,
        ids=ids,
        text_store=_TextStore(ids),
        workspace_consent=lambda _workspace: True,
        grant_authorizer=_GrantApproval(),
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await app.create(
        title="Overlap",
        owner_task_id=task_one,
        owner_route_generation=1,
    )
    await app.grant(ProjectGrantCommand(project.project_id, 1))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_one,
        source_workspace_commitment=workspace_one,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_two,
        source_workspace_commitment=workspace_two,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))

    store = InMemoryCoordinationStore()
    detector = CoordinationDetector(app, store)
    left = DeclaredCoordinationInput(
        task_one,
        project.project_id,
        repository,
        workspace_one,
        1,
        ("src/a.py",),
    )
    right = DeclaredCoordinationInput(
        task_two,
        project.project_id,
        repository,
        workspace_two,
        1,
        ("src/a.py",),
    )
    deliveries = await detector.detect(left, right)
    assert len(deliveries) == 2
    assert len(store.delivery_rows) == 2
    detection = await store.get_detection(deliveries[0].detection_id)
    assert detection is not None and detection.advice_only and not detection.obligation_declared
    assert await store.obligation(detection.detection_id, task_one) is None
    assert await store.obligation(detection.detection_id, task_two) is None
    await detector.redeliver(deliveries[0].detection_id)
    assert len(store.delivery_rows) == 2


@pytest.mark.anyio
async def test_unrelated_repositories_never_overlap_raw_paths_or_plan_labels() -> None:
    ids = FixedIds()
    task_one = new_id(IdKind.TASK)
    task_two = new_id(IdKind.TASK)
    repository_one = _commitment("a")
    repository_two = _commitment("b")
    workspace_one = _commitment("c")
    workspace_two = _commitment("d")
    assert repository_one != repository_two
    catalog = _Catalog(
        {
            task_one: _provenance(task_one, workspace_one, repository_one),
            task_two: _provenance(task_two, workspace_two, repository_two),
        }
    )
    app = ProjectApplication(
        catalog,
        ids=ids,
        text_store=_TextStore(ids),
        workspace_consent=lambda _workspace: True,
        grant_authorizer=_GrantApproval(),
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await app.create(
        title="Cross repository", owner_task_id=task_one, owner_route_generation=1
    )
    await app.grant(ProjectGrantCommand(project.project_id, 1))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_one,
        source_workspace_commitment=workspace_one,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_two,
        source_workspace_commitment=workspace_two,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))

    detector = CoordinationDetector(app, InMemoryCoordinationStore())
    left = DeclaredCoordinationInput(
        task_one,
        project.project_id,
        repository_one,
        workspace_one,
        1,
        ("src/shared.py",),
        structured_items=({"resource_identity": "src/shared.py", "operation_kind": "edit"},),
    )
    right = DeclaredCoordinationInput(
        task_two,
        project.project_id,
        repository_two,
        workspace_two,
        1,
        ("src/shared.py",),
        structured_items=({"resource_identity": "src/shared.py", "operation_kind": "edit"},),
    )
    assert await detector.detect(left, right) == ()
    assert left.resource_identities() != right.resource_identities()
    assert left.plan_identities() != right.plan_identities()


@pytest.mark.anyio
async def test_unobservable_coverage_is_admitted_without_pair_or_path_identity() -> None:
    task_one = new_id(IdKind.TASK)
    task_two = new_id(IdKind.TASK)
    repository = _commitment("a")
    workspace_one = _commitment("b")
    workspace_two = _commitment("c")
    consent = {workspace_one: True, workspace_two: True}
    catalog = _Catalog(
        {
            task_one: _provenance(task_one, workspace_one, repository),
            task_two: _provenance(task_two, workspace_two, repository),
        }
    )
    app = ProjectApplication(
        catalog,
        ids=FixedIds(),
        workspace_consent=lambda workspace: consent.get(workspace, False),
        grant_authorizer=_GrantApproval(),
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await catalog.ensure_repository_project(repository)
    store = InMemoryCoordinationStore()
    detector = CoordinationDetector(app, store)

    class _NoopRuntime:
        async def route(self, _command: object) -> object:
            raise AssertionError("coverage-only input must not open a task runtime")

        async def release(self, _runtime: object) -> None:
            return None

    runtime = CoordinationRuntime(
        app,
        detector,
        LedgerCoordinationInputProvider(app, _NoopRuntime()),  # type: ignore[arg-type]
    )
    app.detection_store = store
    input_without_paths = DeclaredCoordinationInput(
        task_one,
        project.project_id,
        repository,
        workspace_one,
        1,
        source_has_attributable_paths=False,
    )
    await runtime.record_unobservable_coverage((input_without_paths,), expected_generation=None)
    rows = await store.coverage_for(project.project_id, 1)
    assert len(rows) == 1
    assert rows[0].task_id == task_one
    assert rows[0].coverage == "unobservable"
    assert rows[0].gap_code.value == "not_observable"
    assert set(rows[0].as_wire()) == {
        "coverage_id",
        "project_id",
        "task_id",
        "membership_generation",
        "coverage",
        "gap_code",
    }
    assert not await store.list_detections(project.project_id)
    assert await app.coordination_coverage_for(task_one, project=project.project_id) == rows

    consent[workspace_two] = False
    input_unconsented = DeclaredCoordinationInput(
        task_two,
        project.project_id,
        repository,
        workspace_two,
        1,
        source_has_attributable_paths=False,
    )
    await runtime.record_unobservable_coverage((input_unconsented,), expected_generation=None)
    assert tuple(row.task_id for row in await store.coverage_for(project.project_id, 1)) == (
        task_one,
    )
    await app.opt_out(repository)
    await runtime.record_unobservable_coverage((input_without_paths,), expected_generation=None)
    assert await app.coordination_coverage_for(task_one, project=project.project_id) == ()


@pytest.mark.anyio
async def test_declared_pair_appends_context_before_each_terminal_delivery_and_shared_work_resolves() -> (
    None
):
    ids = FixedIds()
    task_one = new_id(IdKind.TASK)
    task_two = new_id(IdKind.TASK)
    repository = _commitment("a")
    workspace_one = _commitment("b")
    workspace_two = _commitment("c")
    catalog = _Catalog(
        {
            task_one: _provenance(task_one, workspace_one, repository),
            task_two: _provenance(task_two, workspace_two, repository),
        }
    )
    app = ProjectApplication(
        catalog,
        ids=ids,
        text_store=_TextStore(ids),
        workspace_consent=lambda _workspace: True,
        grant_authorizer=_GrantApproval(),
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await app.create(
        title="Declared overlap", owner_task_id=task_one, owner_route_generation=1
    )
    await app.grant(ProjectGrantCommand(project.project_id, 1))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_one,
        source_workspace_commitment=workspace_one,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))
    await app.link(
        project_id=project.project_id,
        member_kind=MemberKind.TASK,
        member_commitment_or_id=task_two,
        source_workspace_commitment=workspace_two,
    )
    current = await catalog.project_state(project.project_id)
    assert current is not None
    await app.grant(ProjectGrantCommand(project.project_id, current.membership_generation))
    store = InMemoryCoordinationStore()
    context_calls: list[tuple[str, int]] = []

    class _Writer:
        async def record_context(
            self,
            detection: CoordinationDetection,
            recipient: CoordinationParticipant,
            source: CoordinationParticipant,
        ) -> str:
            del source
            # The context append is ordered before put_advice creates the terminal delivery row.
            context_calls.append((recipient.task_id, len(store.delivery_rows)))
            return new_id(IdKind.EVENT)

    detector = CoordinationDetector(app, store, context_writer=_Writer())
    obligation_one = new_id(IdKind.OBLIGATION)
    obligation_two = new_id(IdKind.OBLIGATION)
    left = DeclaredCoordinationInput(
        task_one,
        project.project_id,
        repository,
        workspace_one,
        1,
        ("src/a.py",),
    )
    right = DeclaredCoordinationInput(
        task_two,
        project.project_id,
        repository,
        workspace_two,
        1,
        ("src/a.py",),
    )
    resource_identity = canonical_resource_identity("src/a.py", repository_commitment=repository)
    detection_id = coordination_detection_identity(
        project_id_value=project.project_id,
        membership_generation=current.membership_generation,
        left_task_id=min(task_one, task_two),
        right_task_id=max(task_one, task_two),
        resource_identities=(resource_identity,),
    )
    advice = await detector.detect(
        left,
        right,
        coordination_declarations=(
            CoordinationObligationDeclaredPayload(
                event_id(detection_id),
                project.project_id,
                current.membership_generation,
                task_id(task_one),
                obligation_id(obligation_one),
            ),
            CoordinationObligationDeclaredPayload(
                event_id(detection_id),
                project.project_id,
                current.membership_generation,
                task_id(task_two),
                obligation_id(obligation_two),
            ),
        ),
    )
    assert len(advice) == 2
    assert context_calls == [(task_one, 0), (task_two, 1)]
    assert len(store.delivery_rows) == 2
    assert advice[0].detection_id == detection_id
    first_state = await store.obligation(detection_id, task_one)
    assert first_state is not None and first_state.obligation_id == obligation_one
    await detector.disposition(detection_id, task_one, disposition="shared_work")
    resolved = await detector.qualify_resolution(
        detection_id,
        task_one,
        qualifying_check=True,
        overlap_cleared=False,
    )
    assert resolved.resolved


@pytest.mark.anyio
async def test_detector_rejects_stale_task_route_declarations() -> None:
    ids = FixedIds()
    task_one = new_id(IdKind.TASK)
    task_two = new_id(IdKind.TASK)
    repository = _commitment("d")
    workspace_one = _commitment("e")
    workspace_two = _commitment("f")
    catalog = _Catalog(
        {
            task_one: _provenance(task_one, workspace_one, repository),
            task_two: _provenance(task_two, workspace_two, repository),
        }
    )
    app = ProjectApplication(
        catalog,
        ids=ids,
        text_store=_TextStore(ids),
        workspace_consent=lambda _workspace: True,
        coordination_source_authorizer=_allow_coordination_source,
    )
    project = await app.create(
        title="Stale route",
        owner_task_id=task_one,
        owner_route_generation=1,
    )
    store = InMemoryCoordinationStore()
    detector = CoordinationDetector(app, store)
    left = DeclaredCoordinationInput(
        task_one,
        project.project_id,
        repository,
        workspace_one,
        2,
        ("src/a.py",),
    )
    right = DeclaredCoordinationInput(
        task_two,
        project.project_id,
        repository,
        workspace_two,
        1,
        ("src/a.py",),
    )
    with pytest.raises(CoordinationError) as error:
        await detector.detect(left, right)
    assert getattr(error.value, "code", None) is CoordinationErrorCode.GENERATION_MISMATCH


@pytest.mark.anyio
async def test_project_and_coordination_text_use_encrypted_owned_objects() -> None:
    ids = FixedIds()
    objects = MemoryObjects(ids)
    owner = new_id(IdKind.TASK)
    project = new_id(IdKind.PROJECT)
    text_store = EncryptedProjectTextStore(objects, clock=FixedClock())
    title = await text_store.put(
        project,
        "title",
        "secret title",
        owner_task_id=owner,
        route_generation=4,
    )
    assert title.owner_task_id == owner
    assert title.route_generation == 4
    assert await text_store.read(title) == "secret title"
    assert all(
        ref.metadata.kind is ObjectKind.PROJECT_TEXT
        for ref in objects.refs_for_kind(ObjectKind.PROJECT_TEXT)
    )
    details = EncryptedCoordinationDetailStore(objects, clock=FixedClock())
    detail = await details.put_details(
        new_id(IdKind.EVENT),
        JsonObject({"format": COORDINATION_DETAIL_FORMAT, "path": "src/a.py"}),
        owner_task_id=owner,
        route_generation=4,
    )
    assert detail.owner_task_id == owner
    assert "src/a.py" not in str(detail.as_wire())
    assert await details.read_details(detail) == JsonObject(
        {"format": COORDINATION_DETAIL_FORMAT, "path": "src/a.py"}
    )

    routed_calls: list[tuple[str, int]] = []

    async def resolve(task_id: str, route_generation: int) -> MemoryObjects:
        routed_calls.append((task_id, route_generation))
        return objects

    routed = RoutedEncryptedProjectTextStore(resolve, clock=FixedClock())
    routed_ref = await routed.put(
        project,
        "description",
        "routed description",
        owner_task_id=owner,
        route_generation=4,
    )
    assert await routed.read(routed_ref) == "routed description"
    assert routed_calls == [(owner, 4), (owner, 4)]
