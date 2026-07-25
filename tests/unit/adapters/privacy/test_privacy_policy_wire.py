"""Domain PrivacyPolicy ↔ wire privacy-policy-1.0.0 codec."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from builders.privacy_policies import local_only_policy, minimal_external_policy
from yoetz.adapters.privacy.catalog import (
    decode_privacy_policy_canonical,
    encode_privacy_policy_json,
)
from yoetz.protocol.canonical import JsonValue, canonical_encode
from yoetz.protocol.schemas import validate_schema_instance


def test_encode_local_only_matches_wire_schema() -> None:
    wire = encode_privacy_policy_json(local_only_policy())
    validate_schema_instance("privacy-policy", "1.0.0", wire)
    assert wire["schema_version"] == "1.0.0"
    assert "agent_context_categories" not in wire
    ceilings = wire["local_sink_category_ceilings"]
    assert type(ceilings) is dict
    assert set(cast(dict[str, JsonValue], ceilings)) == {
        "local_model",
        "agent_context",
        "trusted_human_control",
    }
    channels = wire["channel_policies"]
    assert type(channels) is list
    assert [cast(dict[str, JsonValue], item)["channel"] for item in channels] == [
        "capability_testing",
        "crash_diagnostics",
        "llm_inference",
        "product_telemetry",
        "update_checks",
    ]


def test_wire_round_trip_preserves_domain_policy() -> None:
    original = minimal_external_policy()
    wire = encode_privacy_policy_json(original)
    validate_schema_instance("privacy-policy", "1.0.0", wire)
    decoded = decode_privacy_policy_canonical(canonical_encode(wire))
    assert decoded == original


def test_domain_desired_state_shape_still_decodes(tmp_path: Path) -> None:
    from yoetz.config.privacy_desired import (
        load_privacy_desired_canonical,
        write_privacy_desired_toml,
    )

    original = local_only_policy()
    path = write_privacy_desired_toml(original, tmp_path / "desired.toml")
    decoded = decode_privacy_policy_canonical(load_privacy_desired_canonical(path))
    assert decoded == original
