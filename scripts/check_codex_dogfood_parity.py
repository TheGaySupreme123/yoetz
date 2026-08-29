"""Validate the retained exact-worktree Codex dogfood parity gate (issues #463/#464).

The input is a bounded structural report assembled from the runbook's named commands. It carries
digests and closed states only: no paths, prompts, transcripts, credentials, or provider payloads.
Run ``--phase preflight`` before launching Codex; a non-zero result forbids the launch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Literal, TypedDict, cast

GateStatus = Literal["pass", "fail", "unsupported", "blocked", "not_run"]

_SCHEMA: Final = "yoetz.codex-dogfood-parity/1"
_MAX_REPORT_BYTES: Final = 131_072
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
_SOURCE_REF = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$", re.ASCII)
_TOKEN = re.compile(r"^[a-z][a-z0-9_]{0,127}$", re.ASCII)
_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,127}$", re.ASCII)

PREFLIGHT_FACETS: Final = (
    "source_identity",
    "package_identity",
    "workspace_binding",
    "observation_consent",
    "plugin_source",
    "plugin_installation",
    "plugin_discovery",
    "plugin_inventory",
    "plugin_enablement",
    "plugin_rendered_bytes",
    "plugin_cache",
    "plugin_activation",
    "normal_target_snapshot",
)

POSTFLIGHT_FACETS: Final = (
    "skill_delivery",
    "mcp_runtime",
    "model_mcp_call",
    "hook_lifecycle",
    "mapping",
    "accepted_envelopes",
    "diagnostics",
    "drain",
    "session_stream",
    "semantic_dispatch",
    "semantic_provenance",
    "receipt",
    "corrective_influence",
    "rollback",
    "normal_target_unchanged",
)

_ALL_FACETS: Final = frozenset((*PREFLIGHT_FACETS, *POSTFLIGHT_FACETS))
_HOOK_FACETS: Final = frozenset(
    {"hook_lifecycle", "mapping", "accepted_envelopes", "diagnostics", "drain"}
)
_NEXT_ACTIONS: Final = frozenset(
    {
        "none",
        "do_not_launch",
        "yoetz_observe_grant_exact_worktree",
        "yoetz_recommend_list_exact_target",
        "manual_activation_review",
        "complete_postflight",
    }
)


class DogfoodGateError(ValueError):
    """One bounded report validation failure."""


class GateRow(TypedDict):
    status: GateStatus
    reason: str | None
    evidence_digest: str | None
    next_action: str


class DogfoodScope(TypedDict):
    hooks_advertised: bool
    session_stream_advertised: bool
    semantic_required: bool
    influence_required: bool


class DogfoodIdentity(TypedDict):
    source_ref: str
    package_digest: str
    codex_executable_digest: str
    codex_version: str
    codex_home_digest: str
    launcher_digest: str
    route_profile: Literal["strict", "policy"]
    worktree_digest: str


class DogfoodObserved(TypedDict):
    activation_state: str
    exact_worktree_consent: str
    primary_checkout_consent: str
    controls_workspace_match: bool
    mapping_present: bool
    accepted_envelope_count: int
    undelivered_count: int
    drain_succeeded: bool
    hook_coverage: bool
    stream_coverage: bool


class DogfoodGateResult(TypedDict):
    schema: str
    preflight_outcome: GateStatus
    launch_allowed: bool
    full_outcome: GateStatus
    failed_facets: list[str]
    blocked_facets: list[str]
    unsupported_facets: list[str]
    not_run_facets: list[str]
    report_digest: str


def _error(reason: str) -> DogfoodGateError:
    return DogfoodGateError(reason)


def _require_digest(value: object, reason: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise _error(reason)
    return value


def _require_bool(value: object, reason: str) -> bool:
    if type(value) is not bool:
        raise _error(reason)
    return value


def _parse_identity(value: object) -> DogfoodIdentity:
    if type(value) is not dict:
        raise _error("identity_invalid")
    row = cast(dict[str, object], value)
    expected = {
        "source_ref",
        "package_digest",
        "codex_executable_digest",
        "codex_version",
        "codex_home_digest",
        "launcher_digest",
        "route_profile",
        "worktree_digest",
    }
    if set(row) != expected:
        raise _error("identity_fields_invalid")
    source_ref = row["source_ref"]
    version = row["codex_version"]
    route = row["route_profile"]
    if type(source_ref) is not str or _SOURCE_REF.fullmatch(source_ref) is None:
        raise _error("source_ref_invalid")
    if type(version) is not str or _VERSION.fullmatch(version) is None:
        raise _error("codex_version_invalid")
    if route not in {"strict", "policy"}:
        raise _error("route_profile_invalid")
    return DogfoodIdentity(
        source_ref=source_ref,
        package_digest=_require_digest(row["package_digest"], "package_digest_invalid"),
        codex_executable_digest=_require_digest(
            row["codex_executable_digest"], "codex_executable_digest_invalid"
        ),
        codex_version=version,
        codex_home_digest=_require_digest(row["codex_home_digest"], "codex_home_digest_invalid"),
        launcher_digest=_require_digest(row["launcher_digest"], "launcher_digest_invalid"),
        route_profile=cast(Literal["strict", "policy"], route),
        worktree_digest=_require_digest(row["worktree_digest"], "worktree_digest_invalid"),
    )


def _parse_scope(value: object) -> DogfoodScope:
    if type(value) is not dict:
        raise _error("scope_invalid")
    row = cast(dict[str, object], value)
    expected = {
        "hooks_advertised",
        "session_stream_advertised",
        "semantic_required",
        "influence_required",
    }
    if set(row) != expected:
        raise _error("scope_fields_invalid")
    return DogfoodScope(
        hooks_advertised=_require_bool(row["hooks_advertised"], "hooks_scope_invalid"),
        session_stream_advertised=_require_bool(
            row["session_stream_advertised"], "session_stream_scope_invalid"
        ),
        semantic_required=_require_bool(row["semantic_required"], "semantic_scope_invalid"),
        influence_required=_require_bool(row["influence_required"], "influence_scope_invalid"),
    )


def _parse_observed(value: object) -> DogfoodObserved:
    if type(value) is not dict:
        raise _error("observed_invalid")
    row = cast(dict[str, object], value)
    expected = {
        "activation_state",
        "exact_worktree_consent",
        "primary_checkout_consent",
        "controls_workspace_match",
        "mapping_present",
        "accepted_envelope_count",
        "undelivered_count",
        "drain_succeeded",
        "hook_coverage",
        "stream_coverage",
    }
    if set(row) != expected:
        raise _error("observed_fields_invalid")
    activation = row["activation_state"]
    exact_consent = row["exact_worktree_consent"]
    primary_consent = row["primary_checkout_consent"]
    if activation not in {
        "active",
        "installed_not_activated",
        "not_installed",
        "foreign",
        "unknown",
    }:
        raise _error("activation_state_invalid")
    consent_states = {"active", "missing", "paused", "revoked", "unknown"}
    if exact_consent not in consent_states or primary_consent not in consent_states:
        raise _error("consent_state_invalid")
    for name in (
        "accepted_envelope_count",
        "undelivered_count",
    ):
        if type(row[name]) is not int or cast(int, row[name]) < 0:
            raise _error(f"{name}_invalid")
    return DogfoodObserved(
        activation_state=cast(str, activation),
        exact_worktree_consent=cast(str, exact_consent),
        primary_checkout_consent=cast(str, primary_consent),
        controls_workspace_match=_require_bool(
            row["controls_workspace_match"], "controls_workspace_match_invalid"
        ),
        mapping_present=_require_bool(row["mapping_present"], "mapping_present_invalid"),
        accepted_envelope_count=cast(int, row["accepted_envelope_count"]),
        undelivered_count=cast(int, row["undelivered_count"]),
        drain_succeeded=_require_bool(row["drain_succeeded"], "drain_succeeded_invalid"),
        hook_coverage=_require_bool(row["hook_coverage"], "hook_coverage_invalid"),
        stream_coverage=_require_bool(row["stream_coverage"], "stream_coverage_invalid"),
    )


def _parse_gate_row(name: str, value: object) -> GateRow:
    if type(value) is not dict:
        raise _error(f"{name}_row_invalid")
    row = cast(dict[str, object], value)
    if set(row) != {"status", "reason", "evidence_digest", "next_action"}:
        raise _error(f"{name}_fields_invalid")
    status = row["status"]
    reason = row["reason"]
    evidence = row["evidence_digest"]
    next_action = row["next_action"]
    if status not in {"pass", "fail", "unsupported", "blocked", "not_run"}:
        raise _error(f"{name}_status_invalid")
    if reason is not None and (type(reason) is not str or _TOKEN.fullmatch(reason) is None):
        raise _error(f"{name}_reason_invalid")
    if next_action not in _NEXT_ACTIONS:
        raise _error(f"{name}_next_action_invalid")
    if status == "pass":
        if reason is not None or next_action != "none":
            raise _error(f"{name}_pass_shape_invalid")
        _require_digest(evidence, f"{name}_evidence_invalid")
    else:
        if reason is None:
            raise _error(f"{name}_reason_required")
        if evidence is not None:
            _require_digest(evidence, f"{name}_evidence_invalid")
    return GateRow(
        status=cast(GateStatus, status),
        reason=reason,
        evidence_digest=cast(str | None, evidence),
        next_action=cast(str, next_action),
    )


def _aggregate(names: tuple[str, ...], facets: Mapping[str, GateRow]) -> GateStatus:
    statuses = {facets[name]["status"] for name in names}
    if "fail" in statuses:
        return "fail"
    if "blocked" in statuses:
        return "blocked"
    if "unsupported" in statuses:
        return "unsupported"
    if "not_run" in statuses:
        return "not_run"
    return "pass"


def _required_postflight(scope: DogfoodScope) -> tuple[str, ...]:
    names = [
        "skill_delivery",
        "mcp_runtime",
        "model_mcp_call",
        "receipt",
        "rollback",
        "normal_target_unchanged",
    ]
    if scope["hooks_advertised"]:
        names.extend(sorted(_HOOK_FACETS))
    if scope["session_stream_advertised"]:
        names.append("session_stream")
    if scope["semantic_required"]:
        names.extend(("semantic_dispatch", "semantic_provenance"))
    if scope["influence_required"]:
        names.append("corrective_influence")
    return tuple(names)


def classify_codex_dogfood_report(document: object) -> DogfoodGateResult:
    """Validate one report and derive the preflight/full outcomes without score collapsing."""

    if type(document) is not dict:
        raise _error("report_invalid")
    report = cast(dict[str, object], document)
    if (
        set(report) != {"schema", "identity", "scope", "observed", "facets"}
        or report["schema"] != _SCHEMA
    ):
        raise _error("report_fields_invalid")
    _parse_identity(report["identity"])
    scope = _parse_scope(report["scope"])
    observed = _parse_observed(report["observed"])
    raw_facets = report["facets"]
    if type(raw_facets) is not dict:
        raise _error("facet_inventory_invalid")
    facet_rows = cast(dict[str, object], raw_facets)
    if frozenset(facet_rows) != _ALL_FACETS:
        raise _error("facet_inventory_invalid")
    facets = {name: _parse_gate_row(name, raw) for name, raw in facet_rows.items()}

    if (facets["workspace_binding"]["status"] == "pass") != observed["controls_workspace_match"]:
        raise _error("workspace_binding_observation_mismatch")
    if (facets["observation_consent"]["status"] == "pass") != (
        observed["exact_worktree_consent"] == "active"
    ):
        raise _error("observation_consent_state_mismatch")
    if (facets["plugin_activation"]["status"] == "pass") != (
        observed["activation_state"] == "active"
    ):
        raise _error("activation_state_mismatch")
    if facets["mapping"]["status"] == "pass" and not observed["mapping_present"]:
        raise _error("mapping_observation_missing")
    if (
        facets["accepted_envelopes"]["status"] == "pass"
        and observed["accepted_envelope_count"] == 0
    ):
        raise _error("accepted_envelope_observation_missing")
    if facets["drain"]["status"] == "pass" and (
        not observed["drain_succeeded"] or observed["undelivered_count"] != 0
    ):
        raise _error("drain_observation_mismatch")
    if facets["hook_lifecycle"]["status"] == "pass" and not observed["hook_coverage"]:
        raise _error("hook_coverage_missing")
    if facets["session_stream"]["status"] == "pass" and not observed["stream_coverage"]:
        raise _error("stream_coverage_missing")

    consent = facets["observation_consent"]
    if consent["status"] != "pass" and consent["next_action"] != (
        "yoetz_observe_grant_exact_worktree"
    ):
        raise _error("observation_consent_continuation_missing")
    activation = facets["plugin_activation"]
    if activation["reason"] == "installed_not_activated" and activation["next_action"] != (
        "yoetz_recommend_list_exact_target"
    ):
        raise _error("activation_recovery_continuation_missing")
    if (
        activation["reason"] in {"foreign", "modified", "ambiguous"}
        and activation["next_action"] != "manual_activation_review"
    ):
        raise _error("activation_manual_review_missing")

    for name in _HOOK_FACETS:
        if not scope["hooks_advertised"] and facets[name]["status"] != "unsupported":
            raise _error("unadvertised_hook_facet_not_unsupported")
    if (
        not scope["session_stream_advertised"]
        and facets["session_stream"]["status"] != "unsupported"
    ):
        raise _error("unadvertised_stream_facet_not_unsupported")

    preflight = _aggregate(PREFLIGHT_FACETS, facets)
    required_postflight = _required_postflight(scope)
    postflight = _aggregate(required_postflight, facets)
    full: GateStatus = preflight if preflight != "pass" else postflight
    encoded = json.dumps(report, separators=(",", ":"), sort_keys=True).encode("utf-8")
    report_digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
    return DogfoodGateResult(
        schema=_SCHEMA,
        preflight_outcome=preflight,
        launch_allowed=preflight == "pass",
        full_outcome=full,
        failed_facets=sorted(name for name, row in facets.items() if row["status"] == "fail"),
        blocked_facets=sorted(name for name, row in facets.items() if row["status"] == "blocked"),
        unsupported_facets=sorted(
            name for name, row in facets.items() if row["status"] == "unsupported"
        ),
        not_run_facets=sorted(name for name, row in facets.items() if row["status"] == "not_run"),
        report_digest=report_digest,
    )


def _load(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise _error("report_path_invalid")
    raw = path.read_bytes()
    if not raw or len(raw) > _MAX_REPORT_BYTES:
        raise _error("report_size_invalid")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("report_json_invalid") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--phase", choices=("preflight", "full"), default="full")
    args = parser.parse_args(argv)
    try:
        result = classify_codex_dogfood_report(_load(args.report))
    except DogfoodGateError as exc:
        print(f"codex_dogfood_report_invalid:{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    outcome = result["preflight_outcome"] if args.phase == "preflight" else result["full_outcome"]
    return 0 if outcome == "pass" else 20


if __name__ == "__main__":
    raise SystemExit(main())
