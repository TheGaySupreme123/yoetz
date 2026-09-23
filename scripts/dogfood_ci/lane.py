#!/usr/bin/env python3
"""One unattended dogfood lane: provision, set up, connect one host, probe, drain, dispose.

Run from a checkout on a throwaway GitHub-hosted runner (Linux, macOS, or Ubuntu under WSL 2):

    uv run --no-project --python 3.14.6 python scripts/dogfood_ci/lane.py \\
        --host codex --evidence "$RUNNER_TEMP/evidence" --host-path "$(command -v codex)"

The lane walks the product's own path in order and records every step as bounded JSON under
``--evidence``: build the wheel and provision a pinned disposable instance
(``scripts/provision_test_instance.py``), start its service, initialize the passphrase vault,
bind the reviewed Fireworks Responses profile and store its credential, grant an assisted-review
repository privacy recipe, connect the selected host through ``yoetz setup run`` (or the
documented no-OS-presence substitutes where a runner cannot answer LocalAuthentication), grant
observation, run a deterministic ledger probe (start, publish, check with AI-powered review,
receipt, hook carrier probes, drain), run one tiny native agent session against the same
instance, drain again, restart and unlock the service, and dispose.

Verdict: the lane is *catastrophic* (exit 1) when an install, setup, connection, ledger, or
service-lifecycle step fails, or when observation reports a service or storage failure after
the agent ran. A native agent that never calls Yoetz, times out, or exits nonzero is recorded and
reported, not treated as catastrophic, unless ``--strict-agent`` is set: model compliance is not
what this lane certifies. A green lane means the product installed and its main paths did not
break; it is not evidence that Yoetz is correct, useful, or free of defects.

Secrets arrive only through the environment and are used once: ``DOGFOOD_VAULT_PASSPHRASE``
(generated when absent), ``FIREWORKS_API_KEY`` (AI-powered review and the Codex/Claude agent
model), ``CURSOR_API_KEY`` (the Cursor agent), ``DOGFOOD_OS_PASSWORD`` (the runner account's
password for the Linux PAM artifact review). Every saved file is redacted against those values
and against the runner's home, checkout, and instance paths. Nothing here touches an everyday
installation: the instance is pinned to its own root and disposed at the end.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import secrets as pysecrets
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, cast

_HERE: Final = Path(__file__).resolve().parent
_REPO_ROOT: Final = _HERE.parents[1]

_spec = importlib.util.spec_from_file_location("dogfood_ceremony", _HERE / "ceremony.py")
if _spec is None or _spec.loader is None:  # pragma: no cover - packaging error
    raise SystemExit("ceremony.py missing beside lane.py")
_ceremony = importlib.util.module_from_spec(_spec)
sys.modules["dogfood_ceremony"] = _ceremony
_spec.loader.exec_module(_ceremony)
Reply = _ceremony.Reply
run_ceremony = _ceremony.run_ceremony

HOSTS: Final = ("codex", "claude", "cursor")
CONNECTION_MODES: Final = ("auto", "setup-run", "plugin-dir", "mcp-only", "none")
SEMANTIC_MODEL_DEFAULT: Final = "accounts/fireworks/models/glm-5p3-flash"
CURSOR_MODEL_DEFAULT: Final = "gpt-5.6-luna-low"
FIREWORKS_OPENAI_BASE: Final = "https://api.fireworks.ai/inference/v1"
FIREWORKS_ANTHROPIC_BASE: Final = "https://api.fireworks.ai/inference"
_ACTOR: Final = {"actor_id": "harness:dogfood-ci", "actor_type": "harness"}
_CLIENT: Final = {
    "kind": "cooperative_agent",
    "version": "dogfood-ci/1",
    "integration": "cooperative_mcp",
}
_TAIL_BYTES: Final = 4000

# Prompt contract: every regex below names a product prompt this lane answers. The unit test
# renders the real prompts (typer/click and the trusted-console stems) and asserts each regex
# still matches, so a product wording change fails the test instead of the first CI run.
PROMPT_PASSPHRASE: Final = r"Passphrase \("
PROMPT_CONFIRM_PASSPHRASE: Final = r"Confirm passphrase"
PROMPT_PROVIDER_CREDENTIAL: Final = r"Provider credential: "
PROMPT_DECISION: Final = r"Decision \[approve/deny(?:/edit)?\]: "
PROMPT_PRIVACY_RECOMMENDED: Final = r"Use this recommended privacy policy\? \[Y/n\]: "
PROMPT_PRIVACY_CHOICE: Final = r"Choose a privacy option \[\d\]: "
PROMPT_PRIVACY_CREATE: Final = r"Create this exact privacy proposal .*\? \[y/N\]: "
PROMPT_PAM_PASSWORD: Final = r"Password for .*: "
_PROMPT_TEMPLATE: Final = (
    "You are a small integration probe. Use only the Yoetz MCP tools, whose names contain "
    "'{tool_hint}'. Do exactly these steps and nothing else. "
    "1) Call the start tool with protocol_version '0.1', schema_version '1.0.0', a fresh "
    "request_id of the form req_<random uuid4>, mode 'create', task_title 'dogfood native probe', "
    "workspace_ref '{workspace}', external_ref '{external_ref}', requested_view 'compact', "
    "actor {{actor_id: 'harness:dogfood-native', actor_type: 'harness'}}, and client "
    "{{kind: 'cooperative_agent', version: '0.1.0', integration: 'cooperative_mcp'}}. "
    "2) Call publish_work once with the session_id, writer_id and frontier that start returned "
    "as expected_frontier, and one event draft: event_id evt_<uuid4>, schema "
    "{{name: 'plan_published', version: '1.0.0'}}, occurred_at the current UTC time with "
    "millisecond precision, causal_parents [], artifact_refs [], evidence_refs [], payload "
    "{{plan_version: 1, summary: 'native probe', obligation_refs: [], "
    "no_obligations_reason: 'single_atomic_change'}}. "
    "3) Call receipt with task_id, session_id, writer_id, the frontier returned by publish_work "
    "as expected_frontier, format 'markdown', include 'standard', redaction_profile "
    "'default_local_export'. Then answer with the single word DONE. Do not create or edit files."
)


class LaneAbort(Exception):
    """A catastrophic step already recorded; stop the lane and go to teardown."""


@dataclass(slots=True)
class Step:
    name: str
    phase: str
    status: str
    reason: str | None
    summary: dict[str, Any]
    output_file: str | None
    exit_code: int | None = None
    duration_ms: int = 0


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _rid() -> str:
    return "req_" + str(uuid.uuid4())


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4()}"


def _find_key(value: object, key: str) -> object | None:
    """Depth-first search for the first mapping entry named ``key``."""

    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        if key in mapping:
            return mapping[key]
        for item in mapping.values():
            found = _find_key(item, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in cast(list[object], value):
            found = _find_key(item, key)
            if found is not None:
                return found
    return None


def _find_frontier(value: object) -> dict[str, str] | None:
    """Return the first ``{sequence, head_digest}`` object found in a result."""

    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        if {"sequence", "head_digest"} <= set(mapping) and isinstance(mapping["sequence"], str):
            return {
                "sequence": mapping["sequence"],
                "head_digest": cast(str, mapping["head_digest"]),
            }
        for name in ("result_frontier", "frontier", "subject_frontier"):
            if name in mapping:
                found = _find_frontier(mapping[name])
                if found is not None:
                    return found
        for item in mapping.values():
            found = _find_frontier(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in cast(list[object], value):
            found = _find_frontier(item)
            if found is not None:
                return found
    return None


def _parse_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except ValueError:
        # Some commands print a human line before the JSON; try the last JSON-looking line.
        for line in reversed(stripped.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                    break
                except ValueError:
                    continue
        else:
            return None
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None


def _tail(text: str, limit: int = _TAIL_BYTES) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("YOETZ_")}
    if extra:
        env.update(extra)
    return env


def _os_cell() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    wsl = "microsoft" in platform.release().lower() or os.environ.get("WSL_DISTRO_NAME") is not None
    return f"{'wsl2-' if wsl else ''}{system}-{machine}"


class Lane:
    def __init__(self, args: argparse.Namespace) -> None:
        self.host: str = args.host
        self.checkout = Path(args.checkout).resolve()
        self.base = Path(args.base).expanduser().resolve()
        self.tag: str = args.tag
        self.evidence = Path(args.evidence).expanduser().resolve()
        self.python: str = args.python
        self.strict_agent: bool = args.strict_agent
        self.skip_agent: bool = args.skip_agent
        self.skip_restart: bool = args.skip_restart
        self.agent_timeout: float = args.agent_timeout
        self.semantic_model: str = args.semantic_model
        self.cursor_model: str = args.cursor_model
        self.allow_dirty: bool = args.allow_dirty
        self.host_path: str | None = args.host_path
        self.home = Path.home()
        default_roots = {"codex": ".codex", "claude": ".claude", "cursor": ".cursor"}
        self.host_config_root = (
            Path(args.host_config_root).expanduser().resolve()
            if args.host_config_root
            else self.home / default_roots[self.host]
        )
        self.project = (
            Path(args.project).expanduser().resolve()
            if args.project
            else self.home / f".yz-dogfood-{self.tag}" / "project"
        )
        self.stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.os_cell = _os_cell()
        self.is_darwin = platform.system() == "Darwin"
        self.passphrase = os.environ.get("DOGFOOD_VAULT_PASSPHRASE") or (
            "dogfood-" + pysecrets.token_urlsafe(24)
        )
        self.fireworks_key = os.environ.get("FIREWORKS_API_KEY", "")
        self.cursor_key = os.environ.get("CURSOR_API_KEY", "")
        self.os_password = os.environ.get("DOGFOOD_OS_PASSWORD", "")
        self.connection_mode = self._resolve_connection_mode(args.connection_mode)
        self.launcher: Path | None = None
        self.root: Path | None = None
        self.service_proc: subprocess.Popen[bytes] | None = None
        self.steps: list[Step] = []
        self.identity: dict[str, Any] = {}
        self.semantic: dict[str, Any] = {"configured": bool(self.fireworks_key)}
        self.agent: dict[str, Any] = {"ran": False}
        self.observation: dict[str, Any] = {}
        self.ledger: dict[str, Any] = {}
        self.plugin_dir: Path | None = None
        self.mcp_tool_hint = "yoetz"
        self._counter = 0
        secret_values = [
            self.passphrase,
            self.fireworks_key,
            self.cursor_key,
            self.os_password,
            os.environ.get("ANTHROPIC_API_KEY", ""),
            os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
            os.environ.get("CODEX_API_KEY", ""),
            os.environ.get("OPENAI_API_KEY", ""),
        ]
        self._secret_values = [value for value in secret_values if value]
        self._path_tokens: list[tuple[str, str]] = []
        for value, token in (
            (os.environ.get("RUNNER_TEMP", ""), "<runner-temp>"),
            (str(self.base), "<base>"),
            (str(self.checkout), "<checkout>"),
            (str(self.evidence), "<evidence>"),
            (str(self.home), "<home>"),
        ):
            if value:
                self._path_tokens.append((value, token))
        self._path_tokens.sort(key=lambda item: len(item[0]), reverse=True)

    # ------------------------------------------------------------------ plumbing

    def _resolve_connection_mode(self, requested: str) -> str:
        if requested != "auto":
            return requested
        if self.host == "codex":
            return "setup-run"
        if self.is_darwin or not self.os_password:
            return "plugin-dir" if self.host == "claude" else "mcp-only"
        return "setup-run"

    def redact(self, text: str) -> str:
        for value in sorted(set(self._secret_values), key=len, reverse=True):
            text = text.replace(value, "<secret>")
        for value, token in self._path_tokens:
            text = text.replace(value, token)
        return text

    def _save(self, name: str, text: str, suffix: str = ".txt") -> str:
        self._counter += 1
        path = self.evidence / f"{self._counter:02d}-{name}{suffix}"
        path.write_text(self.redact(text), encoding="utf-8")
        return path.name

    def _record(
        self,
        name: str,
        phase: str,
        *,
        status: str,
        exit_code: int | None = None,
        duration_ms: int = 0,
        reason: str | None = None,
        summary: dict[str, Any] | None = None,
        stdout: str = "",
        stderr: str = "",
        fatal: bool = False,
    ) -> Step:
        body = {
            "step": name,
            "phase": phase,
            "status": status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "reason": reason,
            "stdout": _parse_json(stdout) or _tail(stdout),
            "stderr": _tail(stderr),
        }
        output_file = self._save(name, json.dumps(body, indent=2, sort_keys=True), ".json")
        step = Step(
            name=name,
            phase=phase,
            status=status,
            exit_code=exit_code,
            duration_ms=duration_ms,
            reason=reason,
            summary=self._redact_obj(summary or {}),
            output_file=output_file,
        )
        self.steps.append(step)
        marker = {"pass": "ok", "fail": "FAIL", "skip": "skip", "info": "info"}[status]
        sys.stdout.write(f"[{marker}] {phase}/{name}" + (f" ({reason})" if reason else "") + "\n")
        sys.stdout.flush()
        if fatal and status == "fail":
            raise LaneAbort(name)
        return step

    def _redact_obj(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return {k: self._redact_obj(v) for k, v in cast(dict[str, Any], value).items()}
        if isinstance(value, list):
            return [self._redact_obj(v) for v in cast(list[Any], value)]
        return value

    def _run(
        self,
        argv: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout: float = 300.0,
    ) -> tuple[int, str, str, int]:
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - argv assembled from validated inputs
                argv,
                cwd=cwd,
                env=_clean_env(env),
                input=stdin.encode("utf-8") if stdin is not None else None,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"").decode("utf-8", errors="replace")
            err = (exc.stderr or b"").decode("utf-8", errors="replace")
            return 124, out, err + "\n<timeout>", int((time.monotonic() - started) * 1000)
        except FileNotFoundError as exc:
            return 127, "", str(exc), int((time.monotonic() - started) * 1000)
        return (
            completed.returncode,
            completed.stdout.decode("utf-8", errors="replace"),
            completed.stderr.decode("utf-8", errors="replace"),
            int((time.monotonic() - started) * 1000),
        )

    def _yoetz(
        self,
        name: str,
        phase: str,
        args: list[str],
        *,
        cwd: Path | None = None,
        stdin: str | None = None,
        fatal: bool = False,
        expect_zero: bool = True,
        summary: dict[str, Any] | None = None,
        timeout: float = 300.0,
    ) -> tuple[Step, dict[str, Any] | None]:
        assert self.launcher is not None
        # Repository-bound views (privacy grant, provider readiness, host admission) read the
        # working directory, so every product command runs from the probe project unless a
        # caller names another directory.
        rc, out, err, ms = self._run(
            [str(self.launcher), *args],
            cwd=self.project if cwd is None else cwd,
            stdin=stdin,
            timeout=timeout,
        )
        parsed = _parse_json(out)
        status = "pass" if (rc == 0 or not expect_zero) else "fail"
        reason = None if status == "pass" else f"exit_{rc}"
        step = self._record(
            name,
            phase,
            status=status,
            exit_code=rc,
            duration_ms=ms,
            reason=reason,
            summary=summary,
            stdout=out,
            stderr=err,
            fatal=fatal,
        )
        return step, parsed

    def _ceremony_step(
        self,
        name: str,
        phase: str,
        argv: list[str],
        replies: list[Any],
        *,
        cwd: Path | None = None,
        fatal: bool = False,
        timeout: float = 240.0,
    ) -> tuple[Step, dict[str, Any] | None]:
        started = time.monotonic()
        result = run_ceremony(
            argv,
            replies,
            cwd=self.project if cwd is None else cwd,
            env=_clean_env(),
            timeout=timeout,
        )
        ms = int((time.monotonic() - started) * 1000)
        parsed = _parse_json(result.transcript.replace("\r\n", "\n"))
        status = "pass" if result.exit_code == 0 else "fail"
        reason = (
            None
            if status == "pass"
            else ("timeout" if result.timed_out else f"exit_{result.exit_code}")
        )
        step = self._record(
            name,
            phase,
            status=status,
            exit_code=result.exit_code,
            duration_ms=ms,
            reason=reason,
            summary={"answered": result.answered},
            stdout=result.transcript,
            fatal=fatal,
        )
        return step, parsed

    def _wait_for_service(self, name: str, phase: str, *, timeout: float = 90.0) -> dict[str, Any]:
        """Poll ``service status`` until the service answers; a bounded wait on real state."""

        assert self.launcher is not None
        deadline = time.monotonic() + timeout
        last_err = ""
        started = time.monotonic()
        while True:
            rc, out, err, _ = self._run(
                [str(self.launcher), "service", "status", "--json"], cwd=self.project
            )
            parsed = _parse_json(out)
            if rc == 0 and parsed is not None:
                self._record(
                    name,
                    phase,
                    status="pass",
                    exit_code=rc,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    summary={
                        "state": parsed.get("state"),
                        "state_reason": parsed.get("state_reason"),
                        "vault_mode": parsed.get("vault_mode"),
                        "service_generation": parsed.get("service_generation"),
                    },
                    stdout=out,
                )
                return parsed
            last_err = err or out
            if time.monotonic() >= deadline:
                self._record(
                    name,
                    phase,
                    status="fail",
                    exit_code=rc,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    reason="service_not_ready",
                    stderr=last_err,
                    fatal=True,
                )
            time.sleep(1.0)

    # ------------------------------------------------------------------ phases

    def phase_install(self) -> None:
        phase = "install"
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.project.mkdir(parents=True, exist_ok=True)
        if not (self.project / ".git").exists():
            self._run(["git", "-C", str(self.project), "init", "-q"])
            self._run(
                [
                    "git",
                    "-C",
                    str(self.project),
                    "-c",
                    "user.name=dogfood",
                    "-c",
                    "user.email=dogfood@example.invalid",
                    "commit",
                    "-q",
                    "--allow-empty",
                    "-m",
                    "dogfood probe workspace",
                ]
            )
        self.host_config_root.mkdir(mode=0o700, parents=True, exist_ok=True)

        provision = [
            sys.executable,
            str(self.checkout / "scripts" / "provision_test_instance.py"),
            "create",
            "--base",
            str(self.base),
            "--tag",
            self.tag,
            "--checkout",
            str(self.checkout),
            "--lifecycle",
            "disposable",
            "--expires-in",
            "6",
            "--python",
            self.python,
            "--json",
        ]
        if self.allow_dirty:
            provision.append("--allow-dirty")
        rc, out, err, ms = self._run(provision, timeout=900.0)
        parsed = _parse_json(out)
        if rc != 0 or parsed is None:
            self._record(
                "provision",
                phase,
                status="fail",
                exit_code=rc,
                duration_ms=ms,
                reason="provision_failed",
                stdout=out,
                stderr=err,
                fatal=True,
            )
            return
        self.launcher = Path(cast(str, parsed["launcher"]))
        self.root = Path(cast(str, parsed["isolated_root"]))
        self.identity = {
            "source_ref": parsed.get("source_ref"),
            "source_state": parsed.get("source_state"),
            "package_version": parsed.get("package_version"),
            "package_digest": parsed.get("package_digest"),
            "installation_id": parsed.get("installation_id"),
            "lifecycle": parsed.get("lifecycle"),
        }
        self._record(
            "provision",
            phase,
            status="pass",
            exit_code=rc,
            duration_ms=ms,
            summary=self.identity,
            stdout=out,
            stderr=err,
        )

        _, isolation = self._yoetz(
            "isolation", phase, ["service", "isolation", "--json"], fatal=True
        )
        if isolation is not None and isolation.get("mode") != "isolated":
            self._record(
                "isolation_mode",
                phase,
                status="fail",
                reason="not_isolated",
                summary={"mode": isolation.get("mode"), "binding": isolation.get("binding")},
                fatal=True,
            )
        self._yoetz("instance_status", phase, ["instance", "status", "--json"], fatal=True)
        _, version = self._yoetz("version", phase, ["version", "--json"], fatal=True)
        if version is not None:
            self.identity["version"] = version

        assert self.launcher is not None
        log_path = self.evidence / "service.log"
        self.service_proc = subprocess.Popen(  # noqa: S603 - fixed launcher argv
            [str(self.launcher), "service", "run"],
            cwd=self.home,
            env=_clean_env(),
            stdin=subprocess.DEVNULL,
            stdout=log_path.open("wb"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        status = self._wait_for_service("service_start", phase)
        if status.get("vault_mode") != "uninitialized":
            self._record(
                "vault_precondition",
                phase,
                status="fail",
                reason="vault_not_fresh",
                summary={"vault_mode": status.get("vault_mode")},
                fatal=True,
            )

        self._ceremony_step(
            "vault_initialize",
            phase,
            [str(self.launcher), "service", "initialize-passphrase", "--json"],
            [
                Reply(PROMPT_PASSPHRASE, self.passphrase),
                Reply(PROMPT_CONFIRM_PASSPHRASE, self.passphrase),
            ],
            fatal=True,
        )
        ready = self._wait_for_service("service_ready", phase)
        if ready.get("state") != "ready":
            self._record(
                "vault_ready",
                phase,
                status="fail",
                reason=str(ready.get("state_reason") or ready.get("state")),
                fatal=True,
            )

        self._yoetz(
            "provider_endpoint",
            phase,
            [
                "provider",
                "endpoint",
                "--provider",
                "fireworks",
                "--model",
                self.semantic_model,
                "--no-interactive",
                "--json",
            ],
            fatal=True,
        )
        if self.fireworks_key:
            self._ceremony_step(
                "provider_credential",
                phase,
                [str(self.launcher), "provider", "credential", "set", "--json"],
                [
                    Reply(PROMPT_PROVIDER_CREDENTIAL, self.fireworks_key),
                    Reply(PROMPT_PASSPHRASE, self.passphrase),
                    Reply(PROMPT_DECISION, "approve", secret=False),
                ],
                fatal=True,
            )
        else:
            self._record(
                "provider_credential",
                phase,
                status="skip",
                reason="FIREWORKS_API_KEY_unset",
            )

        recipe = "3" if self.fireworks_key else "1"
        self._ceremony_step(
            "privacy_setup",
            phase,
            [str(self.launcher), "privacy", "setup"],
            [
                Reply(PROMPT_PRIVACY_RECOMMENDED, "n", secret=False),
                Reply(PROMPT_PRIVACY_CHOICE, recipe, secret=False),
                Reply(PROMPT_PRIVACY_CREATE, "y", secret=False),
                Reply(PROMPT_DECISION, "approve", secret=False),
                Reply(PROMPT_PASSPHRASE, self.passphrase),
            ],
            cwd=self.project,
            fatal=True,
        )

        _, provider_status = self._yoetz(
            "provider_status", phase, ["provider", "status", "--json"], expect_zero=False
        )
        if provider_status is not None:
            self.semantic["semantic_ready"] = provider_status.get("semantic_ready")
            self.semantic["blockers"] = [
                {"condition": b.get("condition"), "state": b.get("state")}
                for b in cast(list[dict[str, Any]], provider_status.get("blockers") or [])
            ]
            if self.fireworks_key and provider_status.get("semantic_ready") is not True:
                self._record(
                    "semantic_ready",
                    phase,
                    status="fail",
                    reason="semantic_not_ready_with_credential",
                    summary={"blockers": self.semantic["blockers"]},
                    fatal=True,
                )
        _, setup_status = self._yoetz(
            "setup_status", phase, ["setup", "status", "--json"], expect_zero=False
        )
        if setup_status is not None:
            self.identity["platform"] = setup_status.get("platform")

    # ---- host connection

    def _host_flag(self) -> str:
        return "cursor-cli" if self.host == "cursor" else self.host

    def _write_codex_provider_config(self) -> None:
        config_path = self.host_config_root / "config.toml"
        existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
        if "[model_providers.fireworks]" in existing:
            return
        block = (
            f'model = "{self.semantic_model}"\n'
            'model_provider = "fireworks"\n'
            'model_reasoning_effort = "low"\n'
            "\n"
            "[model_providers.fireworks]\n"
            'name = "Fireworks AI"\n'
            f'base_url = "{FIREWORKS_OPENAI_BASE}"\n'
            'env_key = "FIREWORKS_API_KEY"\n'
            'wire_api = "responses"\n'
        )
        config_path.write_text(block + ("\n" + existing if existing else ""), encoding="utf-8")

    def phase_connect(self) -> None:
        phase = "connect"
        assert self.launcher is not None
        if self.connection_mode == "none":
            self._record("host_connect", phase, status="skip", reason="connection_mode_none")
        elif self.connection_mode == "setup-run":
            self._connect_setup_run(phase)
        elif self.connection_mode == "plugin-dir":
            self._connect_plugin_dir(phase)
        elif self.connection_mode == "mcp-only":
            self._connect_mcp_only(phase)
        self._yoetz(
            "observe_grant",
            phase,
            ["observe", "grant", "--workspace", str(self.project)],
            fatal=True,
        )
        self._observe_status("observe_status_after_grant", phase)

    def _connect_setup_run(self, phase: str) -> None:
        assert self.launcher is not None
        if self.host == "codex" and self.fireworks_key:
            self._write_codex_provider_config()
        target = [
            "--host",
            self._host_flag(),
            "--host-config-root",
            str(self.host_config_root),
            "--project",
            str(self.project),
            "--route-profile",
            "policy",
        ]
        if self.host_path:
            target[2:2] = ["--host-path", self.host_path]
        _, preview = self._yoetz(
            "host_preview",
            phase,
            ["setup", "run", *target, "--non-interactive", "--json"],
            fatal=True,
        )
        plan = cast(dict[str, Any], (preview or {}).get("plan") or {})
        request_id = plan.get("request_id")
        digest = plan.get("preview_digest")
        if not isinstance(request_id, str) or not isinstance(digest, str):
            self._record(
                "host_preview_plan",
                phase,
                status="fail",
                reason="preview_without_plan",
                summary={"outcome": (preview or {}).get("outcome")},
                fatal=True,
            )
            return
        self.identity["host_version"] = plan.get("host_version")
        accept = [
            str(self.launcher),
            "setup",
            "run",
            *target,
            "--request-id",
            request_id,
            "--preview-digest",
            digest,
            "--accept",
            "--non-interactive",
            "--json",
        ]
        if plan.get("requires_os_presence") is False or self.host == "codex":
            _, report = self._yoetz("host_accept", phase, accept[1:], fatal=True)
        else:
            if not self.os_password:
                self._record(
                    "host_accept",
                    phase,
                    status="fail",
                    reason="os_presence_required_without_DOGFOOD_OS_PASSWORD",
                    fatal=True,
                )
                return
            _, report = self._ceremony_step(
                "host_accept",
                phase,
                accept,
                [Reply(PROMPT_PAM_PASSWORD, self.os_password)],
                fatal=True,
            )
        outcome = (report or {}).get("outcome")
        if outcome not in {"completed", "unchanged"}:
            self._record(
                "host_accept_outcome",
                phase,
                status="fail",
                reason=f"outcome_{outcome}",
                summary={"reason": (report or {}).get("reason")},
                fatal=True,
            )
        status_args = ["setup", "status", *target[:-2], "--json"]
        self._yoetz("host_status", phase, status_args, expect_zero=False)
        if self.host == "codex":
            mcp_status = [
                "integrate",
                "codex",
                "mcp",
                "status",
                "--codex-home",
                str(self.host_config_root),
                "--json",
            ]
            if self.host_path:
                mcp_status[4:4] = ["--codex-path", self.host_path]
            self._yoetz("codex_mcp_status", phase, mcp_status, expect_zero=False)
        elif self.host == "claude":
            self._yoetz(
                "claude_plugin_status",
                phase,
                [
                    "integrate",
                    "claude",
                    "plugin",
                    "status",
                    "--claude-config-root",
                    str(self.host_config_root),
                    "--json",
                ],
                expect_zero=False,
            )
            self.mcp_tool_hint = "yoetz"
        else:
            self._yoetz(
                "cursor_plugin_status",
                phase,
                [
                    "integrate",
                    "cursor",
                    "plugin",
                    "status",
                    "--cursor-config-root",
                    str(self.host_config_root),
                    "--json",
                ],
                expect_zero=False,
            )

    def _connect_plugin_dir(self, phase: str) -> None:
        if self.host != "claude":
            self._record(
                "host_connect", phase, status="fail", reason="plugin_dir_is_claude_only", fatal=True
            )
            return
        self.plugin_dir = self.base / f"{self.tag}-claude-plugin"
        if self.plugin_dir.exists():
            shutil.rmtree(self.plugin_dir)
        _, export = self._yoetz(
            "claude_plugin_export",
            phase,
            [
                "integrate",
                "claude",
                "plugin",
                "export",
                "--output-root",
                str(self.plugin_dir),
                "--development-enabled",
                "--project-root",
                str(self.project),
                "--mcp-ownership",
                "plugin-managed",
                "--route-profile",
                "policy",
                "--json",
            ],
            fatal=True,
        )
        self.mcp_tool_hint = "plugin_yoetz_yoetz"
        self._record(
            "host_connect",
            phase,
            status="info",
            reason="development_export_not_marketplace_activation",
            summary={"artifact_digest": (export or {}).get("artifact_digest")},
        )

    def _connect_mcp_only(self, phase: str) -> None:
        if self.host != "cursor":
            self._record(
                "host_connect", phase, status="fail", reason="mcp_only_is_cursor_only", fatal=True
            )
            return
        target = [
            "--project-root",
            str(self.project),
            "--cursor-config-root",
            str(self.host_config_root),
            "--route-profile",
            "policy",
            "--project-binding",
            "registered-project",
        ]
        _, preview = self._yoetz(
            "cursor_project_mcp_preview",
            phase,
            ["integrate", "cursor", "project-mcp", "preview", *target, "--json"],
            fatal=True,
        )
        digest = (preview or {}).get("preview_digest")
        if not isinstance(digest, str):
            self._record(
                "cursor_project_mcp_digest",
                phase,
                status="fail",
                reason="no_preview_digest",
                fatal=True,
            )
            return
        self._yoetz(
            "cursor_project_mcp_install",
            phase,
            [
                "integrate",
                "cursor",
                "project-mcp",
                "install",
                *target,
                "--preview-digest",
                digest,
                "--accept",
                "--json",
            ],
            fatal=True,
        )
        self._record(
            "host_connect",
            phase,
            status="info",
            reason="mcp_only_no_hooks_os_presence_unavailable",
        )

    # ---- observation helpers

    def _observe_status(self, name: str, phase: str) -> dict[str, Any] | None:
        _, status = self._yoetz(
            name,
            phase,
            ["observe", "status", "--workspace", str(self.project), "--json"],
            expect_zero=False,
        )
        if status is not None:
            diagnostics = cast(dict[str, Any], status.get("hook_diagnostics") or {})
            self.observation[name] = {
                "mapping_present": status.get("mapping_present"),
                "recent_count": status.get("recent_count"),
                "source_coverage": status.get("source_coverage"),
                "diagnostic_reasons": diagnostics.get("reasons"),
                "quarantine_causes": status.get("quarantine_causes"),
                "delivery_causes": status.get("delivery_causes"),
                "pending_delivery_causes": status.get("pending_delivery_causes"),
            }
        return status

    def _observe_drain(self, name: str, phase: str, *, fatal: bool) -> dict[str, Any] | None:
        _, drain = self._yoetz(
            name,
            phase,
            ["observe", "drain", "--workspace", str(self.project), "--json"],
            expect_zero=False,
        )
        summary = {
            "terminal": (drain or {}).get("terminal"),
            "pending_after": (drain or {}).get("pending_after"),
            "acknowledged": (drain or {}).get("acknowledged"),
            "quarantined": (drain or {}).get("quarantined"),
            "reasons": (drain or {}).get("reasons"),
        }
        self.observation[name] = summary
        ok = (
            drain is not None
            and drain.get("terminal") == "drained"
            and drain.get("pending_after") == 0
        )
        self._record(
            f"{name}_verdict",
            phase,
            status="pass" if ok else "fail",
            reason=None if ok else "not_drained",
            summary=summary,
            fatal=fatal and not ok,
        )
        return drain

    @staticmethod
    def _catastrophic_diagnostics(status: dict[str, Any] | None) -> list[str]:
        if status is None:
            return ["observe_status_unavailable"]
        reasons = _find_key(status.get("hook_diagnostics"), "reasons")
        found: list[str] = []
        if isinstance(reasons, dict):
            for key in cast(dict[str, Any], reasons):
                if key.startswith(("service_unavailable", "storage_", "vault_locked")):
                    found.append(key)
        elif isinstance(reasons, list):
            for item in cast(list[Any], reasons):
                text = json.dumps(item) if not isinstance(item, str) else item
                if any(
                    token in text for token in ("service_unavailable", "storage_", "vault_locked")
                ):
                    found.append(text[:120])
        return found

    # ---- ledger probe

    def _request(self, extra: dict[str, Any]) -> str:
        body: dict[str, Any] = {
            "protocol_version": "0.1",
            "schema_version": "1.0.0",
            "request_id": _rid(),
            "actor": _ACTOR,
            "client": _CLIENT,
        }
        body.update(extra)
        return json.dumps(body)

    def phase_ledger_probe(self) -> None:
        phase = "ledger"
        assert self.launcher is not None
        _, started = self._yoetz(
            "ledger_start",
            phase,
            [
                "start",
                "--input",
                "-",
                "--json",
            ],
            cwd=self.project,
            stdin=self._request(
                {
                    "mode": "create",
                    "task_title": "dogfood ci deterministic probe",
                    "workspace_ref": str(self.project),
                    "external_ref": f"dogfood-ci-{self.host}-{self.stamp}",
                    "requested_view": "compact",
                }
            ),
            fatal=True,
        )
        if started is None:
            return
        session_id = started.get("session_id")
        writer_id = started.get("writer_id")
        task_id = started.get("task_id")
        frontier = _find_frontier(started)
        if (
            not all(isinstance(v, str) for v in (session_id, writer_id, task_id))
            or frontier is None
        ):
            self._record(
                "ledger_start_ids",
                phase,
                status="fail",
                reason="start_result_incomplete",
                fatal=True,
            )
            return
        self.ledger = {"session_id": session_id, "writer_id": writer_id, "task_id": task_id}
        ids = {"session_id": session_id, "writer_id": writer_id}
        obligation_id = _uid("obl")
        now = _now()
        drafts: list[dict[str, Any]] = [
            {
                "event_id": _uid("evt"),
                "schema": {"name": "plan_published", "version": "1.0.0"},
                "occurred_at": now,
                "causal_parents": [],
                "payload": {
                    "plan_version": 1,
                    "summary": "Deterministic dogfood probe: one bounded ledger round trip.",
                    "obligation_refs": [obligation_id],
                },
                "artifact_refs": [],
                "evidence_refs": [],
            },
            {
                "event_id": _uid("evt"),
                "schema": {"name": "obligation_published", "version": "1.0.0"},
                "occurred_at": now,
                "causal_parents": [],
                "payload": {
                    "obligation_id": obligation_id,
                    "description": "Record one probe round trip in the ledger.",
                    "acceptance_criteria": "The publish batch is accepted and a check runs.",
                    "evidence_expectation": "The check result recorded at the new frontier.",
                    "status": "open",
                },
                "artifact_refs": [],
                "evidence_refs": [],
            },
        ]
        publish_rid = _rid()
        publish_body: dict[str, Any] = {
            **ids,
            "expected_frontier": frontier,
            "event_drafts": drafts,
        }
        dry = json.loads(self._request(publish_body))
        dry["request_id"] = publish_rid
        dry["dry_run"] = True
        self._yoetz(
            "ledger_publish_dry_run",
            phase,
            ["publish-work", "--input", "-", "--json"],
            cwd=self.project,
            stdin=json.dumps(dry),
            fatal=True,
        )
        real = dict(dry)
        real["dry_run"] = False
        _, published = self._yoetz(
            "ledger_publish",
            phase,
            ["publish-work", "--input", "-", "--json"],
            cwd=self.project,
            stdin=json.dumps(real),
            fatal=True,
        )
        _, status = self._yoetz(
            "ledger_status",
            phase,
            ["status", "--input", "-", "--json"],
            cwd=self.project,
            stdin=self._request({**ids, "view": "compact", "limit": "10"}),
            fatal=True,
        )
        frontier = _find_frontier(status) or _find_frontier(published) or frontier
        check_body: dict[str, Any] = {**ids, "expected_frontier": frontier, "max_findings": "10"}
        if self.fireworks_key:
            check_body["mode"] = "semantic_required"
        _, checked = self._yoetz(
            "ledger_check",
            phase,
            ["check", "--input", "-", "--json"],
            cwd=self.project,
            stdin=self._request(check_body),
            fatal=True,
            timeout=420.0,
        )
        if checked is not None:
            provenance = checked.get("semantic_provenance")
            self.semantic.update(
                {
                    "status": checked.get("semantic_status"),
                    "reason": checked.get("semantic_reason"),
                    "provenance_present": provenance is not None,
                    "provenance_provider": _find_key(provenance, "provider_id")
                    if provenance is not None
                    else None,
                    "verdict": checked.get("verdict"),
                    "findings": len(cast(list[Any], checked.get("findings") or [])),
                }
            )
            frontier = _find_frontier(checked) or frontier
            if self.fireworks_key:
                attempted = checked.get("semantic_status") in {
                    "succeeded",
                    "refused",
                    "timeout",
                    "invalid",
                    "late",
                    "stale",
                    "unavailable",
                }
                self._record(
                    "semantic_attempt",
                    phase,
                    status="pass" if attempted else "fail",
                    reason=None
                    if attempted
                    else str(checked.get("semantic_reason") or checked.get("semantic_status")),
                    summary={
                        "semantic_status": checked.get("semantic_status"),
                        "semantic_reason": checked.get("semantic_reason"),
                        "provenance_present": provenance is not None,
                    },
                    fatal=True,
                )
        self._yoetz(
            "ledger_receipt",
            phase,
            ["receipt", "--input", "-", "--json"],
            cwd=self.project,
            stdin=self._request(
                {
                    **ids,
                    "task_id": task_id,
                    "expected_frontier": frontier,
                    "format": "markdown",
                    "include": "standard",
                    "redaction_profile": "default_local_export",
                }
            ),
            fatal=True,
        )
        self._hook_carrier_probes(phase)
        self._observe_drain("observe_drain_after_probe", phase, fatal=True)
        self._observe_status("observe_status_after_probe", phase)

    def _hook_carrier_probes(self, phase: str) -> None:
        session = f"dogfood-{self.host}-{self.stamp}"
        payloads: list[tuple[str, dict[str, Any]]]
        if self.host == "codex":
            carrier = ["hooks", "observe", "--workspace", "."]
            payloads = [
                ("SessionStart", {"session_id": session, "hook_event_name": "SessionStart"}),
                (
                    "PostToolUse",
                    {
                        "session_id": session,
                        "hook_event_name": "PostToolUse",
                        "tool_name": "shell",
                        "exit_status": 0,
                    },
                ),
            ]
        elif self.host == "claude":
            carrier = ["hooks", "claude-observe", "--workspace", str(self.project)]
            payloads = [
                (
                    "SessionStart",
                    {
                        "cwd": str(self.project),
                        "hook_event_name": "SessionStart",
                        "session_id": session,
                        "transcript_path": str(self.project / "transcript.jsonl"),
                    },
                ),
                (
                    "PostToolUse",
                    {
                        "cwd": str(self.project),
                        "hook_event_name": "PostToolUse",
                        "session_id": session,
                        "tool_name": "Bash",
                        "tool_input": {},
                        "tool_response": {},
                        "tool_use_id": "tool-1",
                        "transcript_path": str(self.project / "transcript.jsonl"),
                    },
                ),
            ]
        else:
            carrier = ["hooks", "cursor-observe", "--workspace", str(self.project)]
            payloads = [
                (
                    "sessionStart",
                    {
                        "conversation_id": session,
                        "hook_event_name": "sessionStart",
                        "workspace_roots": [str(self.project)],
                    },
                ),
                (
                    "stop",
                    {
                        "conversation_id": session,
                        "hook_event_name": "stop",
                        "workspace_roots": [str(self.project)],
                    },
                ),
            ]
        for event, payload in payloads:
            self._yoetz(
                f"hook_probe_{event}",
                phase,
                [*carrier, "--event", event],
                cwd=self.project,
                stdin=json.dumps(payload),
                fatal=False,
            )

    # ---- native agent

    def _agent_command(self) -> tuple[list[str] | None, dict[str, str], str | None]:
        prompt = _PROMPT_TEMPLATE.format(
            tool_hint=self.mcp_tool_hint,
            workspace=str(self.project),
            external_ref=f"native-{self.host}-{self.stamp}",
        )
        env: dict[str, str] = {}
        if self.host == "codex":
            exe = self.host_path or shutil.which("codex")
            if exe is None:
                return None, env, "codex_executable_missing"
            if self.fireworks_key:
                env["FIREWORKS_API_KEY"] = self.fireworks_key
            elif not (os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")):
                return None, env, "no_model_credential"
            env["CODEX_HOME"] = str(self.host_config_root)
            argv = [
                exe,
                "exec",
                "--json",
                "--skip-git-repo-check",
                "-C",
                str(self.project),
                "--sandbox",
                "workspace-write",
                "--dangerously-bypass-hook-trust",
                "-o",
                str(self.evidence / "agent-last-message.md"),
                prompt,
            ]
            return argv, env, None
        if self.host == "claude":
            exe = self.host_path or shutil.which("claude")
            if exe is None:
                return None, env, "claude_executable_missing"
            if self.fireworks_key:
                env.update(
                    {
                        "ANTHROPIC_BASE_URL": FIREWORKS_ANTHROPIC_BASE,
                        "ANTHROPIC_AUTH_TOKEN": self.fireworks_key,
                        "ANTHROPIC_CUSTOM_HEADERS": f"X-Fireworks-Api-Key: {self.fireworks_key}",
                        "ANTHROPIC_MODEL": self.semantic_model,
                        "ANTHROPIC_DEFAULT_SONNET_MODEL": self.semantic_model,
                        "ANTHROPIC_DEFAULT_OPUS_MODEL": self.semantic_model,
                        "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.semantic_model,
                        "ANTHROPIC_SMALL_FAST_MODEL": self.semantic_model,
                    }
                )
            elif not os.environ.get("ANTHROPIC_API_KEY"):
                return None, env, "no_model_credential"
            env.update(
                {
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                    "DISABLE_AUTOUPDATER": "1",
                    "DISABLE_TELEMETRY": "1",
                }
            )
            if self.host_config_root != self.home / ".claude":
                env["CLAUDE_CONFIG_DIR"] = str(self.host_config_root)
            argv = [
                exe,
                "-p",
                prompt,
                "--output-format",
                "json",
                "--dangerously-skip-permissions",
            ]
            if self.plugin_dir is not None:
                argv += ["--plugin-dir", str(self.plugin_dir)]
            return argv, env, None
        exe = self.host_path or shutil.which("cursor-agent") or shutil.which("agent")
        if exe is None:
            return None, env, "cursor_executable_missing"
        if not self.cursor_key:
            return None, env, "CURSOR_API_KEY_unset"
        env["CURSOR_API_KEY"] = self.cursor_key
        argv = [
            exe,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--force",
            "--trust",
            "--approve-mcps",
            "--model",
            self.cursor_model,
        ]
        return argv, env, None

    def phase_native_agent(self) -> None:
        phase = "native"
        if self.skip_agent:
            self._record("agent_run", phase, status="skip", reason="skip_agent")
            return
        argv, env, reason = self._agent_command()
        if argv is None:
            self._record("agent_run", phase, status="skip", reason=reason)
            self.agent = {"ran": False, "reason": reason}
            return
        rc, out, err, ms = self._run(argv, cwd=self.project, env=env, timeout=self.agent_timeout)
        output_file = self._save("agent-output", out or "", ".txt")
        stderr_file = self._save("agent-stderr", err or "", ".txt")
        done = "DONE" in out
        self.agent = {
            "ran": True,
            "exit_code": rc,
            "timed_out": rc == 124,
            "duration_ms": ms,
            "output_digest": _digest(out),
            "output_bytes": len(out.encode("utf-8")),
            "done_marker": done,
            "output_file": output_file,
            "stderr_file": stderr_file,
        }
        ok = rc == 0
        self._record(
            "agent_run",
            phase,
            status="pass" if ok else "fail",
            exit_code=rc,
            duration_ms=ms,
            reason=None if ok else ("agent_timeout" if rc == 124 else f"agent_exit_{rc}"),
            summary={"done_marker": done, "output_bytes": len(out)},
            stderr=err,
        )
        status = self._observe_status("observe_status_after_agent", phase)
        self._observe_drain("observe_drain_after_agent", phase, fatal=False)
        status = self._observe_status("observe_status_after_drain", phase) or status
        catastrophic = self._catastrophic_diagnostics(status)
        mapped = bool((status or {}).get("mapping_present"))
        self.agent["mapping_present"] = mapped
        self.agent["catastrophic_diagnostics"] = catastrophic
        self._record(
            "agent_observation",
            phase,
            status="fail" if catastrophic else "pass",
            reason=",".join(catastrophic) if catastrophic else None,
            summary={"mapping_present": mapped, "catastrophic_diagnostics": catastrophic},
            fatal=bool(catastrophic),
        )
        self._yoetz(
            "service_status_after_agent", phase, ["service", "status", "--json"], fatal=True
        )

    # ---- lifecycle

    def phase_lifecycle(self) -> None:
        phase = "lifecycle"
        assert self.launcher is not None
        if self.skip_restart:
            self._record("service_restart", phase, status="skip", reason="skip_restart")
            return
        self._yoetz(
            "service_restart", phase, ["service", "restart", "--json"], fatal=True, timeout=120.0
        )
        status = self._wait_for_service("service_status_after_restart", phase)
        if status.get("state") == "locked":
            self._ceremony_step(
                "service_unlock",
                phase,
                [str(self.launcher), "service", "unlock", "--json"],
                [Reply(PROMPT_PASSPHRASE, self.passphrase)],
                fatal=True,
            )
            status = self._wait_for_service("service_status_after_unlock", phase)
        if status.get("state") != "ready":
            self._record(
                "service_ready_after_restart",
                phase,
                status="fail",
                reason=str(status.get("state_reason") or status.get("state")),
                fatal=True,
            )
        after = self._observe_status("observe_status_after_restart", phase)
        consent_retained = after is not None and after.get("mapping_present") is not None
        self._record(
            "observation_retained",
            phase,
            status="pass" if consent_retained else "fail",
            reason=None if consent_retained else "observe_status_unreadable_after_restart",
        )

    # ---- teardown

    def phase_teardown(self) -> None:
        phase = "teardown"
        if self.launcher is None:
            self._record("dispose", phase, status="skip", reason="never_provisioned")
            return
        logs = self.evidence / "instance-logs"
        rc, out, err, ms = self._run(
            [
                sys.executable,
                str(self.checkout / "scripts" / "provision_test_instance.py"),
                "dispose",
                "--base",
                str(self.base),
                "--tag",
                self.tag,
                "--retain-logs",
                str(logs),
                "--json",
            ],
            timeout=300.0,
        )
        self._record(
            "dispose",
            phase,
            status="pass" if rc == 0 else "fail",
            exit_code=rc,
            duration_ms=ms,
            reason=None if rc == 0 else "dispose_failed",
            stdout=out,
            stderr=err,
        )
        proc = self.service_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self.plugin_dir is not None and self.plugin_dir.exists():
            shutil.rmtree(self.plugin_dir, ignore_errors=True)
        for path in logs.rglob("*"):
            if path.is_file():
                try:
                    path.write_text(self.redact(path.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass

    # ---- report

    def report(self) -> dict[str, Any]:
        failed = [s.name for s in self.steps if s.status == "fail"]
        agent_ok: bool | None = None
        if self.agent.get("ran"):
            agent_ok = self.agent.get("exit_code") == 0 and bool(self.agent.get("mapping_present"))
        agent_failures = {"agent_run"}
        catastrophic = [name for name in failed if name not in agent_failures]
        verdict = {
            "catastrophic": bool(catastrophic),
            "catastrophic_steps": catastrophic,
            "failed_steps": failed,
            "agent_ok": agent_ok,
            "strict_agent": self.strict_agent,
            "green": not catastrophic and (agent_ok is not False or not self.strict_agent),
        }
        body = {
            "schema": "yoetz.dogfood-lane/1",
            "generated_at": _now(),
            "host": self.host,
            "os_cell": self.os_cell,
            "connection_mode": self.connection_mode,
            "semantic_model": self.semantic_model,
            "identity": self.identity,
            "semantic": self.semantic,
            "agent": self.agent,
            "observation": self.observation,
            "ledger": self.ledger,
            "steps": [asdict(step) for step in self.steps],
            "verdict": verdict,
        }
        return cast(dict[str, Any], self._redact_obj(body))

    def write_report(self) -> dict[str, Any]:
        body = self.report()
        (self.evidence / "lane-report.json").write_text(
            json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        lines = [
            f"## Dogfood lane: {self.host} on {self.os_cell} ({self.connection_mode})",
            "",
            f"verdict: {'GREEN' if body['verdict']['green'] else 'RED'}"
            f" · catastrophic={body['verdict']['catastrophic']} · agent_ok={body['verdict']['agent_ok']}",
            "",
            "| phase | step | status | reason |",
            "|---|---|---|---|",
        ]
        lines += [f"| {s.phase} | {s.name} | {s.status} | {s.reason or ''} |" for s in self.steps]
        text = "\n".join(lines) + "\n"
        (self.evidence / "lane-summary.md").write_text(text, encoding="utf-8")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as sink:
                sink.write(text)
        return body

    def run(self) -> int:
        try:
            self.phase_install()
            self.phase_connect()
            self.phase_ledger_probe()
            self.phase_native_agent()
            self.phase_lifecycle()
        except LaneAbort:
            pass
        finally:
            try:
                self.phase_teardown()
            finally:
                body = self.write_report()
        return 0 if body["verdict"]["green"] else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--host", choices=HOSTS, required=True)
    parser.add_argument("--checkout", default=str(_REPO_ROOT))
    parser.add_argument("--base", default=str(Path.home() / ".yz-instances"))
    parser.add_argument("--tag", default="df", help="Instance tag beneath --base (short).")
    parser.add_argument("--evidence", required=True, help="Directory for bounded evidence files.")
    parser.add_argument("--python", default="3.14.6", help="Interpreter for the instance runtime.")
    parser.add_argument("--host-path", default=None, help="Exact host executable.")
    parser.add_argument("--host-config-root", default=None, help="Host configuration root.")
    parser.add_argument("--project", default=None, help="Probe workspace (created if missing).")
    parser.add_argument("--connection-mode", choices=CONNECTION_MODES, default="auto")
    parser.add_argument("--semantic-model", default=SEMANTIC_MODEL_DEFAULT)
    parser.add_argument("--cursor-model", default=CURSOR_MODEL_DEFAULT)
    parser.add_argument("--agent-timeout", type=float, default=420.0)
    parser.add_argument("--strict-agent", action="store_true")
    parser.add_argument("--skip-agent", action="store_true")
    parser.add_argument("--skip-restart", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true", help="Build a modified tree.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    lane = Lane(args)
    return lane.run()


if __name__ == "__main__":
    raise SystemExit(main())
