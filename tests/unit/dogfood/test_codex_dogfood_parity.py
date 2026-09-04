"""Executable classification locks for exact-worktree Codex dogfood parity (#464, #518)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import cast

import pytest

_FIXTURE = (
    Path(__file__).parents[2] / "fixtures" / "codex-dogfood" / "worktree-without-exact-consent.json"
)
_SCRIPT = Path(__file__).parents[3] / "scripts" / "check_codex_dogfood_parity.py"
_SPEC = importlib.util.spec_from_file_location("check_codex_dogfood_parity", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
POSTFLIGHT_FACETS = _MODULE.POSTFLIGHT_FACETS
PREFLIGHT_FACETS = _MODULE.PREFLIGHT_FACETS
DogfoodGateError = _MODULE.DogfoodGateError
classify_codex_dogfood_report = _MODULE.classify_codex_dogfood_report
_DIGEST = "sha256:" + ("a" * 64)
_NORMAL_DIGEST = "sha256:" + ("b" * 64)


def _gate(status: str = "pass") -> dict[str, object]:
    if status == "pass":
        return {
            "status": "pass",
            "reason": None,
            "evidence_digest": _DIGEST,
            "next_action": "none",
        }
    return {
        "status": status,
        "reason": "not_exercised",
        "evidence_digest": None,
        "next_action": "complete_postflight",
    }


def _report() -> dict[str, object]:
    return {
        "schema": "yoetz.codex-dogfood-parity/3",
        "identity": {
            "source_ref": "a" * 40,
            "package_digest": _DIGEST,
            "codex_executable_digest": _DIGEST,
            "codex_version": "0.148.0",
            "codex_home_digest": _DIGEST,
            "launcher_digest": _DIGEST,
            "route_profile": "policy",
            "worktree_digest": _DIGEST,
            "yoetz_isolation": {
                "mode": "isolated",
                "normal_mode": "ambient",
                "state_digest": _DIGEST,
                "endpoint_digest": _DIGEST,
                "storage_digest": _DIGEST,
                "config_digest": _DIGEST,
                "executable_digest": _DIGEST,
                "normal_state_digest": _NORMAL_DIGEST,
                "normal_endpoint_digest": _NORMAL_DIGEST,
                "normal_storage_digest": _NORMAL_DIGEST,
                "normal_config_digest": _NORMAL_DIGEST,
                "normal_executable_digest": _NORMAL_DIGEST,
            },
        },
        "scope": {
            "hooks_advertised": True,
            "session_stream_advertised": True,
            "semantic_required": True,
            "influence_required": True,
        },
        "observed": {
            "activation_state": "active",
            "yoetz_isolation_state": "isolated",
            "mcp_registration_state": "yoetz_owned",
            "mcp_isolation_binding": "isolated_exact",
            "mcp_child_state": "ready",
            "exact_worktree_consent": "active",
            "primary_checkout_consent": "active",
            "controls_workspace_match": True,
            "mapping_present": True,
            "accepted_envelope_count": 4,
            "undelivered_count": 0,
            "drain_succeeded": True,
            "hook_coverage": True,
            "stream_coverage": True,
        },
        "facets": {name: _gate() for name in (*PREFLIGHT_FACETS, *POSTFLIGHT_FACETS)},
    }


def _facets(report: dict[str, object]) -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], report["facets"])


def _observed(report: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], report["observed"])


def test_every_required_layer_passes_before_full_gate_can_pass() -> None:
    result = classify_codex_dogfood_report(_report())

    assert result["preflight_outcome"] == "pass"
    assert result["launch_allowed"] is True
    assert result["full_outcome"] == "pass"
    assert result["failed_facets"] == []


def test_installed_not_activated_is_disqualifying_and_actionable() -> None:
    report = _report()
    _observed(report)["activation_state"] = "installed_not_activated"
    _facets(report)["plugin_activation"] = {
        "status": "fail",
        "reason": "installed_not_activated",
        "evidence_digest": _DIGEST,
        "next_action": "yoetz_recommend_list_exact_target",
    }

    result = classify_codex_dogfood_report(report)

    assert result["preflight_outcome"] == "fail"
    assert result["launch_allowed"] is False
    assert result["failed_facets"] == ["plugin_activation"]


def test_primary_checkout_consent_cannot_cover_the_exact_worktree_fixture() -> None:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    report = _report()
    observed = _observed(report)
    observed["primary_checkout_consent"] = fixture["primary_checkout_consent"]
    observed["exact_worktree_consent"] = fixture["exact_worktree_consent"]
    _facets(report)["observation_consent"] = fixture["observation_consent"]

    result = classify_codex_dogfood_report(report)

    assert result["preflight_outcome"] == fixture["expected_preflight_outcome"]
    assert result["launch_allowed"] is fixture["expected_launch_allowed"]
    assert result["blocked_facets"] == ["observation_consent"]


def test_registration_or_tools_without_model_call_cannot_pass_full_gate() -> None:
    report = _report()
    _facets(report)["model_mcp_call"] = {
        "status": "not_run",
        "reason": "model_call_not_observed",
        "evidence_digest": _DIGEST,
        "next_action": "complete_postflight",
    }

    result = classify_codex_dogfood_report(report)

    assert result["preflight_outcome"] == "pass"
    assert result["full_outcome"] == "not_run"
    assert result["not_run_facets"] == ["model_mcp_call"]


def test_advertised_hooks_require_mapping_envelopes_and_a_clean_drain() -> None:
    report = _report()
    _observed(report)["mapping_present"] = False
    with pytest.raises(DogfoodGateError, match="mapping_observation_missing"):
        classify_codex_dogfood_report(report)

    report = _report()
    _observed(report)["undelivered_count"] = 1
    with pytest.raises(DogfoodGateError, match="drain_observation_mismatch"):
        classify_codex_dogfood_report(report)


def test_unadvertised_session_stream_is_explicitly_unsupported_not_green() -> None:
    report = _report()
    scope = cast(dict[str, object], report["scope"])
    scope["session_stream_advertised"] = False
    _observed(report)["stream_coverage"] = False
    _facets(report)["session_stream"] = {
        "status": "unsupported",
        "reason": "capability_not_advertised",
        "evidence_digest": _DIGEST,
        "next_action": "none",
    }

    result = classify_codex_dogfood_report(report)

    assert result["full_outcome"] == "pass"
    assert result["unsupported_facets"] == ["session_stream"]


def test_out_of_scope_failure_cannot_be_ignored_by_full_aggregation() -> None:
    report = _report()
    scope = cast(dict[str, object], report["scope"])
    scope["influence_required"] = False
    _facets(report)["corrective_influence"] = {
        "status": "fail",
        "reason": "influence_failed",
        "evidence_digest": _DIGEST,
        "next_action": "do_not_launch",
    }

    with pytest.raises(DogfoodGateError, match="out_of_scope_facet_not_not_run"):
        classify_codex_dogfood_report(report)


def _identity(report: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], report["identity"])


def _isolation(report: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], _identity(report)["yoetz_isolation"])


def test_shared_yoetz_identity_cannot_pass_the_isolation_facet() -> None:
    report = _report()
    _isolation(report)["state_digest"] = _NORMAL_DIGEST

    with pytest.raises(DogfoodGateError, match="service_isolation_identity_shared"):
        classify_codex_dogfood_report(report)


def test_shared_yoetz_executable_cannot_pass_the_isolation_facet() -> None:
    report = _report()
    _isolation(report)["executable_digest"] = _NORMAL_DIGEST

    with pytest.raises(DogfoodGateError, match="service_isolation_identity_shared"):
        classify_codex_dogfood_report(report)


def test_ambient_or_unknown_isolation_state_contradicts_a_passing_facet() -> None:
    for state in ("ambient", "shared", "unknown"):
        report = _report()
        _observed(report)["yoetz_isolation_state"] = state
        with pytest.raises(DogfoodGateError, match="service_isolation_state_mismatch"):
            classify_codex_dogfood_report(report)


def test_ambient_identity_mode_contradicts_a_passing_isolation_facet() -> None:
    report = _report()
    _isolation(report)["mode"] = "ambient"

    with pytest.raises(DogfoodGateError, match="service_isolation_identity_mismatch"):
        classify_codex_dogfood_report(report)


def test_nonambient_normal_snapshot_contradicts_a_passing_isolation_facet() -> None:
    report = _report()
    _isolation(report)["normal_mode"] = "isolated"

    with pytest.raises(DogfoodGateError, match="service_isolation_identity_mismatch"):
        classify_codex_dogfood_report(report)


def test_failed_isolation_refuses_launch_with_the_provisioning_continuation() -> None:
    report = _report()
    _observed(report)["yoetz_isolation_state"] = "shared"
    _facets(report)["service_isolation"] = {
        "status": "fail",
        "reason": "yoetz_identity_shared",
        "evidence_digest": _DIGEST,
        "next_action": "provision_isolated_yoetz_root",
    }

    result = classify_codex_dogfood_report(report)

    assert result["preflight_outcome"] == "fail"
    assert result["launch_allowed"] is False
    assert result["failed_facets"] == ["service_isolation"]


def test_failed_isolation_without_the_provisioning_continuation_is_invalid() -> None:
    report = _report()
    _observed(report)["yoetz_isolation_state"] = "unknown"
    _facets(report)["service_isolation"] = {
        "status": "blocked",
        "reason": "yoetz_identity_unknown",
        "evidence_digest": None,
        "next_action": "do_not_launch",
    }

    with pytest.raises(DogfoodGateError, match="service_isolation_continuation_missing"):
        classify_codex_dogfood_report(report)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mcp_registration_state", "absent"),
        ("mcp_isolation_binding", "missing"),
        ("mcp_isolation_binding", "different"),
        ("mcp_child_state", "failed"),
        ("mcp_child_state", "unknown"),
    ],
)
def test_mcp_child_isolation_fails_closed_before_launch(field: str, value: str) -> None:
    report = _report()
    _observed(report)[field] = value
    _facets(report)["mcp_child_isolation"] = {
        "status": "fail" if value != "unknown" else "blocked",
        "reason": "mcp_child_isolation_unproven",
        "evidence_digest": _DIGEST if value != "unknown" else None,
        "next_action": "reregister_isolated_mcp",
    }

    result = classify_codex_dogfood_report(report)

    assert result["preflight_outcome"] in {"fail", "blocked"}
    assert result["launch_allowed"] is False
    assert result["failed_facets"] == (["mcp_child_isolation"] if value != "unknown" else [])


def test_mcp_child_isolation_requires_the_reregistration_continuation() -> None:
    report = _report()
    _observed(report)["mcp_isolation_binding"] = "missing"
    _facets(report)["mcp_child_isolation"] = {
        "status": "fail",
        "reason": "mcp_child_isolation_unproven",
        "evidence_digest": _DIGEST,
        "next_action": "do_not_launch",
    }

    with pytest.raises(DogfoodGateError, match="mcp_child_isolation_continuation_missing"):
        classify_codex_dogfood_report(report)


def test_report_inventory_rejects_transcript_or_path_extensions() -> None:
    report = _report()
    report["transcript"] = "must never be admitted"
    with pytest.raises(DogfoodGateError, match="report_fields_invalid"):
        classify_codex_dogfood_report(report)

    report = _report()
    identity = cast(dict[str, object], report["identity"])
    identity["worktree_path"] = "/private/path"
    with pytest.raises(DogfoodGateError, match="identity_fields_invalid"):
        classify_codex_dogfood_report(report)


def test_cli_preflight_refuses_launch_on_a_failed_required_facet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report()
    _observed(report)["activation_state"] = "installed_not_activated"
    _facets(report)["plugin_activation"] = {
        "status": "fail",
        "reason": "installed_not_activated",
        "evidence_digest": _DIGEST,
        "next_action": "yoetz_recommend_list_exact_target",
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    assert _MODULE.main([str(path), "--phase", "preflight"]) == 20
    result = json.loads(capsys.readouterr().out)
    assert result["launch_allowed"] is False
    assert result["preflight_outcome"] == "fail"
