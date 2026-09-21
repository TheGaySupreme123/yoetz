"""Required startup is scope-bound and preserves native approval and recovery."""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from yoetz.adapters.integrations.startup_gate import GateScope, GateStore
from yoetz.cli.startup_gate import bootstrap_tool, gate_output, handle_startup_gate
from yoetz.protocol.canonical import JsonValue
from yoetz.protocol.ids import IdKind, new_id


class Host:
    def __init__(self, root: Path, host: Literal["claude", "cursor"]):
        self.host: Literal["claude", "cursor"] = host
        self.root = root.resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.state = self.root / "state"
        self.session = "native-session"
        self.store = GateStore(host, self.session, str(self.workspace), root=self.state)
        self.route = (new_id(IdKind.TASK), new_id(IdKind.SESSION), new_id(IdKind.WRITER))
        self.ready = True
        self.probes = 0
        self.boundary("start")

    def boundary(self, kind: str) -> None:
        event = (
            {"start": "SessionStart", "prompt": "UserPromptSubmit", "end": "SessionEnd"}
            if self.host == "claude"
            else {"start": "sessionStart", "prompt": "beforeSubmitPrompt", "end": "sessionEnd"}
        )[kind]
        self.call(event)

    def call(self, event: str, **values: JsonValue) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "session_id": self.session,
            "conversation_id": self.session,
            "cwd": str(self.workspace),
            "workspace_roots": [str(self.workspace)],
            **values,
        }
        out = io.BytesIO()

        def probe(_scope: GateScope, _workspace: str) -> bool:
            self.probes += 1
            return self.ready

        assert (
            handle_startup_gate(
                host=self.host,
                event=event,
                stdin_bytes=json.dumps(payload).encode(),
                stdout=out,
                _state=self.state,
                probe=probe,
            )
            == 0
        )
        return json.loads(out.getvalue())

    def pre(self, tool: str, request: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return self.call(
            "PreToolUse" if self.host == "claude" else "beforeMCPExecution",
            tool_name=f"mcp__yoetz__{tool}" if self.host == "claude" else tool,
            mcp_server_name="yoetz",
            tool_input=request,
        )

    def post(self, tool: str, request: dict[str, JsonValue], result: dict[str, JsonValue]) -> None:
        self.call(
            "PostToolUse" if self.host == "claude" else "afterMCPExecution",
            tool_name=f"mcp__yoetz__{tool}" if self.host == "claude" else tool,
            mcp_server_name="yoetz",
            tool_input=request,
            **(
                {"tool_response": {"structuredContent": result}}
                if self.host == "claude"
                else {"result_json": json.dumps({"structuredContent": result})}
            ),
        )

    def start(self) -> None:
        request: dict[str, JsonValue] = {"request_id": new_id(IdKind.REQUEST)}
        self.pre("start", request)
        self.post(
            "start",
            request,
            {
                "request_id": request["request_id"],
                "ok": True,
                "task_id": self.route[0],
                "session_id": self.route[1],
                "writer_id": self.route[2],
            },
        )

    def publication(
        self,
        *,
        plan: bool = True,
        obligation: str | None = None,
        refs: list[str] | None = None,
        version: int = 1,
    ) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
        obligation = new_id(IdKind.OBLIGATION) if obligation is None else obligation
        refs = [obligation] if refs is None else refs
        drafts: list[JsonValue] = []
        if plan:
            drafts.append(
                {
                    "event_id": new_id(IdKind.EVENT),
                    "schema": {"name": "plan_published"},
                    "payload": {"plan_version": version, "obligation_refs": list(refs)},
                }
            )
        drafts.append(
            {
                "event_id": new_id(IdKind.EVENT),
                "schema": {"name": "obligation_published"},
                "payload": {
                    "obligation_id": obligation,
                    "status": "open",
                    "description": "private prose never persisted",
                },
            }
        )
        request: dict[str, JsonValue] = {
            "request_id": new_id(IdKind.REQUEST),
            "session_id": self.route[1],
            "writer_id": self.route[2],
            "event_drafts": drafts,
        }
        accepted: list[JsonValue] = []
        for draft in drafts:
            assert isinstance(draft, dict)
            schema = draft["schema"]
            assert isinstance(schema, dict)
            accepted.append(
                {
                    "event_id": draft["event_id"],
                    "schema_name": schema["name"],
                    "accepted_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                }
            )
        result: dict[str, JsonValue] = {
            "request_id": request["request_id"],
            "ok": True,
            "outcome": "accepted",
            "task_id": self.route[0],
            "session_id": self.route[1],
            "writer_id": self.route[2],
            "accepted_events": accepted,
        }
        return request, result

    def publish(self, **options: object) -> None:
        assert not options
        request, result = self.publication()
        self.pre("publish_work", request)
        self.post("publish_work", request, result)

    def denied(self, tool: str = "Read") -> bool:
        response = self.call(
            "PreToolUse" if self.host == "claude" else "preToolUse",
            tool_name=tool,
            tool_input={"file_path": "private-prose"},
        )
        if self.host == "cursor":
            return response.get("permission") == "deny"
        return "hookSpecificOutput" in response


@pytest.fixture(params=["claude", "cursor"])
def host(tmp_path: Path, request: pytest.FixtureRequest) -> Host:
    name = request.param
    assert name in {"claude", "cursor"}
    return Host(tmp_path, "claude" if name == "claude" else "cursor")


def test_investigation_and_delegation_denied_until_accepted_current_plan(host: Host) -> None:
    assert host.denied("Read") and host.denied("Agent") and host.denied("Task")
    host.start()
    assert host.denied()
    host.publish()
    assert not host.denied()
    assert host.probes == 1
    assert "private prose" not in host.store.path.read_text()


def test_old_mapping_and_stored_ready_flag_do_not_open_gate(host: Host) -> None:
    host.store.path.write_text('{"ready":true}')
    assert host.denied()


def test_new_scope_requires_fresh_plan_and_keeps_route(host: Host) -> None:
    host.start()
    host.publish()
    host.boundary("prompt")
    scope = host.store.read()
    assert scope is not None and scope.route == host.route
    assert host.denied()
    req, res = host.publication(version=2, refs=scope.plan_refs, obligation=scope.plan_refs[0])
    host.pre("publish_work", req)
    host.post("publish_work", req, res)
    assert not host.denied()


def test_new_session_boundary_requires_start_and_can_bind_new_task(host: Host) -> None:
    host.start()
    host.publish()
    host.boundary("start")
    old = host.store.read()
    assert old is not None and old.needs_start
    req, res = host.publication(version=2, refs=old.plan_refs, obligation=old.plan_refs[0])
    host.pre("publish_work", req)
    host.post("publish_work", req, res)
    assert host.denied()
    host.route = (new_id(IdKind.TASK), new_id(IdKind.SESSION), new_id(IdKind.WRITER))
    host.start()
    host.publish()
    assert not host.denied()


@pytest.mark.parametrize("outcome", ["failure", "dry_run", "wrong_route", "stale"])
def test_unaccepted_or_stale_plan_cannot_open_gate(host: Host, outcome: str) -> None:
    host.start()
    req, res = host.publication()
    if outcome == "failure":
        res.update(ok=False, error={"code": "INVALID_REQUEST"})
    elif outcome == "dry_run":
        res["outcome"] = "dry_run"
    elif outcome == "wrong_route":
        res["session_id"] = new_id(IdKind.SESSION)
    else:
        rows = res["accepted_events"]
        assert isinstance(rows, list)
        for row in rows:
            assert isinstance(row, dict)
            row["accepted_at"] = "2020-01-01T00:00:00.000Z"
    host.pre("publish_work", req)
    host.post("publish_work", req, res)
    assert host.denied()


def test_new_obligation_outside_effective_plan_closes_gate(host: Host) -> None:
    host.start()
    host.publish()
    req, res = host.publication(plan=False)
    host.pre("publish_work", req)
    host.post("publish_work", req, res)
    assert host.denied()
    scope = host.store.read()
    assert scope is not None
    req, res = host.publication(
        version=2, refs=scope.required_refs, obligation=scope.required_refs[0]
    )
    host.pre("publish_work", req)
    host.post("publish_work", req, res)
    assert not host.denied()


def test_live_probe_failure_denies_but_bootstrap_remains_available(host: Host) -> None:
    host.start()
    host.publish()
    host.ready = False
    assert host.denied()
    for tool in ("read_guidance", "status", "check", "respond", "receipt"):
        response = host.pre(tool, {})
        assert response == ({} if host.host == "claude" else {"permission": "ask"})


def test_corrupt_gate_state_does_not_deadlock_bootstrap(host: Host) -> None:
    host.store.path.write_bytes(b"broken")
    assert host.denied()
    assert host.pre("read_guidance", {}) == ({} if host.host == "claude" else {"permission": "ask"})


def test_lost_publication_recovers_without_replaying_complete_write(host: Host) -> None:
    host.start()
    req, _res = host.publication()
    host.pre("publish_work", req)
    host.boundary("prompt")
    assert host.denied()
    host.post(
        "status",
        {},
        {
            "ok": True,
            "view": "operation",
            "task_id": host.route[0],
            "session_id": host.route[1],
            "writer_id": host.route[2],
            "page": {"operation_request_id": req["request_id"], "state": "complete"},
        },
    )
    scope = host.store.read()
    assert scope is not None and not scope.pending and scope.required_refs
    assert host.denied()
    new_req, result = host.publication(
        version=2, refs=scope.required_refs, obligation=scope.required_refs[0]
    )
    host.pre("publish_work", new_req)
    host.post("publish_work", new_req, result)
    assert not host.denied()


def test_concurrent_scope_change_during_probe_denies(host: Host) -> None:
    host.start()
    host.publish()

    def change_scope(scope: GateScope, _workspace: str) -> bool:
        with host.store.locked():
            host.store.write(GateScope.fresh(scope))
        return True

    out = io.BytesIO()
    handle_startup_gate(
        host=host.host,
        event="PreToolUse" if host.host == "claude" else "preToolUse",
        stdin_bytes=json.dumps(
            {
                "session_id": host.session,
                "conversation_id": host.session,
                "cwd": str(host.workspace),
                "workspace_roots": [str(host.workspace)],
                "tool_name": "Read",
            }
        ).encode(),
        stdout=out,
        _state=host.state,
        probe=change_scope,
    )
    assert "deny" in out.getvalue().decode()


def test_native_permission_boundaries() -> None:
    assert gate_output("claude", "PreToolUse", True, "ready") == {}
    assert gate_output("cursor", "beforeMCPExecution", True, "ready") == {"permission": "ask"}
    assert gate_output("cursor", "preToolUse", False, "missing")["permission"] == "deny"


def test_recovery_is_exact_launcher_and_never_a_compound_shell_command() -> None:
    command = f"{sys.prefix}/bin/yoetz service restart"
    for suffix, allowed in (
        ("", True),
        ("; touch x", False),
        (" | cat", False),
        (" $(touch x)", False),
    ):
        assert (
            bootstrap_tool(
                {"tool_name": "Bash", "tool_input": {"command": command + suffix}},
                "claude",
                "PreToolUse",
            )
            is allowed
        )
    assert not bootstrap_tool(
        {"tool_name": "Bash", "tool_input": {"command": "/foreign/yoetz service restart"}},
        "claude",
        "PreToolUse",
    )


def test_foreign_cursor_workflow_name_cannot_bypass_gate(tmp_path: Path) -> None:
    host = Host(tmp_path, "cursor")
    result = host.call(
        "beforeMCPExecution", tool_name="start", mcp_server_name="foreign", tool_input={}
    )
    assert result["permission"] == "deny"


def test_session_end_preserves_unknown_request(host: Host) -> None:
    request: dict[str, JsonValue] = {"request_id": new_id(IdKind.REQUEST)}
    host.pre("start", request)
    host.boundary("end")
    scope = host.store.read()
    assert scope is not None and request["request_id"] in scope.pending
    assert host.denied()
