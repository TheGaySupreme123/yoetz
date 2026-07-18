from __future__ import annotations

import ast
import importlib
import importlib.util
import subprocess
import sys
from collections.abc import Iterator, Mapping
from dataclasses import FrozenInstanceError, fields, is_dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TypeAliasType, cast

import pytest

import yoetz.protocol.errors as errors_module
from yoetz.protocol.errors import (
    PROTOCOL_REASON_CODES,
    SAFE_DETAIL_KEYS,
    ProtocolValueError,
    PublicErrorCode,
    PublicOperationError,
    SafeDetailValue,
    normalize_safe_details,
)

_VALID_CORRELATION_ID = "err_00000000-0000-4000-8000-000000000000"
_OTHER_CORRELATION_ID = "err_00000000-0000-4000-8000-000000000001"

_EXPECTED_REASON_CODES = tuple(
    """accepted_record_shape_invalid
actor_id_malformed
actor_id_not_generated
attachment_key_incomplete
byte_order_mark_forbidden
commitment_only_object_kind
dependency_changed
duplicate_object_key
duplicate_set_member
empty_check_types
empty_publication_channels
empty_subject_state
engine_family_wrong_author
entry_digest_mismatch
event_integer_out_of_range
event_text_out_of_bounds
evidence_strength_unsupported
finding_json_shape_invalid
finding_priority_mismatch
float_forbidden
frontier_changed
frontier_digest_mismatch
id_malformed_uuid
id_not_ascii
id_uuid_not_version_4
id_uuid_wrong_variant
id_wrong_length
id_wrong_prefix
id_wrong_type
import_report_invalid
input_not_bytes
integer_out_of_safe_range
integer_out_of_sqlite_range
invalid_chain
invalid_check_types
invalid_commitment
invalid_cost_fields
invalid_coverage_value
invalid_digest
invalid_duration
invalid_event_enum
invalid_event_schema
invalid_event_value_type
invalid_finding_kind
invalid_finding_origin
invalid_finding_policy_identity
invalid_finding_provenance
invalid_finding_subject_refs
invalid_frontier
invalid_json_pointer
invalid_known_gap
invalid_payload_ref
invalid_projection_locator
invalid_publication_channels
invalid_ranked_findings
invalid_receipt_conclusion
invalid_receipt_document
invalid_receipt_gap
invalid_receipt_obligation
invalid_receipt_redaction
invalid_receipt_response
invalid_receipt_section
invalid_receipt_section_order
invalid_receipt_version_slice
invalid_sampling_params
invalid_semantic_dispatch_kind
invalid_semantic_failure_class
invalid_semantic_outcome_type
invalid_semantic_provenance
invalid_semantic_status_reason_pair
invalid_timestamp
invalid_token_usage
invalid_utf8
ledger_assigned_field_in_request_identity
lone_surrogate
malformed_json
missing_payload_field
nesting_too_deep
noncanonical_integer_string
not_an_accepted_envelope
nul_byte_forbidden
object_key_not_string
obligation_change_invalid
obligation_resolution_invalid
payload_redaction_mismatch
plan_version_conflict
privacy_receipt_not_durable
provider_attempt_provenance_is_not_final
public_error_invalid_correlation_id
public_error_invalid_message
public_error_missing_correlation_id
receipt_coverage_mismatch
receipt_gap_not_in_coverage
receipt_json_shape_invalid
redaction_target_required
ref_mirror_mismatch
response_fields_invalid
schema_artifact_role_invalid
schema_artifact_role_mismatch
schema_bytes_invalid
schema_catalog_incomplete
schema_digest_mismatch
schema_draft_unsupported
schema_duplicate_identity
schema_id_mismatch
schema_instance_invalid
schema_kind_mismatch
schema_manifest_duplicate_path
schema_manifest_invalid
schema_manifest_member_mismatch
schema_manifest_missing
schema_name_invalid
schema_not_found
schema_path_unsafe
schema_reference_unresolved
schema_version_mismatch
semantic_provenance_json_shape_invalid
set_member_not_ascii
timestamp_not_utc
timestamp_out_of_range
timestamp_submillisecond_precision
timestamp_timezone_missing
unknown_event_schema
unknown_payload_field
unsorted_set_field
unsupported_json_type
unsupported_payload_type""".splitlines()
)


class _SafeEnum(Enum):
    READY = "ready"


class _UnsafeEnum(Enum):
    UPPER = "NOT_SAFE"


class _LowerSnake64Enum(Enum):
    VALUE = "a" * 64


class _LowerSnake65Enum(Enum):
    VALUE = "a" * 65


class _NonStringValueEnum(Enum):
    VALUE = 1


class _StringSubclass(str):
    pass


class _IntegerSubclass(int):
    pass


class _SubclassValueEnum(Enum):
    VALUE = _StringSubclass("ready")


class _SingleReadEnum(Enum):
    READY = "stored-value"

    @property
    def value(self) -> str:
        reads = getattr(self, "_test_value_reads", 0) + 1
        object.__setattr__(self, "_test_value_reads", reads)
        if reads != 1:
            raise AssertionError("enum value was read more than once")
        return "ready"


class _SpoofedEnum:
    value = "ready"

    @property
    def __class__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
    ) -> type[Enum]:
        return Enum


class _SpoofedMapping:
    @property
    def __class__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
    ) -> type[dict[object, object]]:
        return dict

    def __getitem__(self, key: object) -> object:
        if key == "count":
            return 5
        raise KeyError(key)


class _SpoofedPublicErrorCode:
    value = "UNREGISTERED"

    @property
    def __class__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
    ) -> type[PublicErrorCode]:
        return PublicErrorCode


class _RaisingClass:
    @property
    def __class__(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
    ) -> type[object]:
        raise KeyboardInterrupt("class inspection")


class _ExplosiveValue:
    def __repr__(self) -> str:
        raise AssertionError("unknown value was rendered")

    def __str__(self) -> str:
        raise AssertionError("unknown value was stringified")


class _KnownKeyOnlyMapping(Mapping[object, object]):
    def __init__(self) -> None:
        self.accessed: list[object] = []

    def __getitem__(self, key: object) -> object:
        self.accessed.append(key)
        if key == "count":
            return 3
        if key == "secret":
            return _ExplosiveValue()
        raise KeyError(key)

    def __iter__(self) -> Iterator[object]:
        raise AssertionError("normalizer iterated hostile mapping")

    def __len__(self) -> int:
        raise AssertionError("normalizer measured hostile mapping")


class _RaisingMapping(Mapping[object, object]):
    def __getitem__(self, key: object) -> object:
        raise KeyboardInterrupt(key)

    def __iter__(self) -> Iterator[object]:
        return iter(())

    def __len__(self) -> int:
        return 0


def _assert_reason(exc_info: pytest.ExceptionInfo[ProtocolValueError], reason: str) -> None:
    assert exc_info.value.reason_code == reason
    assert exc_info.value.args == (reason,)
    assert str(exc_info.value) == reason


def test_public_error_code_membership() -> None:
    expected = (
        "INVALID_REQUEST",
        "PROTOCOL_VERSION_UNSUPPORTED",
        "SESSION_NOT_FOUND",
        "SESSION_CONFLICT",
        "IDEMPOTENCY_CONFLICT",
        "OPERATION_PENDING",
        "FRONTIER_CONFLICT",
        "EVENT_INVALID",
        "LIMIT_EXCEEDED",
        "BUNDLE_BUSY",
        "STORAGE_UNSAFE",
        "STORAGE_CORRUPT",
        "MIGRATION_REQUIRED",
        "SERVICE_UNAVAILABLE",
        "VAULT_LOCKED",
        "PRIVACY_AUTHORITY_REQUIRED",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_REFUSED",
        "PROVIDER_TIMEOUT",
        "SEMANTIC_RESULT_INVALID",
        "CANCELLED",
        "INTERNAL_ERROR",
    )
    assert PublicErrorCode.__bases__ == (str, Enum)
    assert tuple(member.name for member in PublicErrorCode) == expected
    assert tuple(member.value for member in PublicErrorCode) == expected
    assert isinstance(SafeDetailValue, TypeAliasType)
    assert SafeDetailValue.__value__ == str | int


def test_protocol_reason_registry_is_exact_and_import_order_independent() -> None:
    source_values = cast(tuple[str, ...], getattr(errors_module, "_PROTOCOL_REASON_CODE_VALUES"))
    assert source_values == _EXPECTED_REASON_CODES
    assert len(source_values) == 127
    assert source_values == tuple(sorted(source_values, key=str.encode))
    assert len(source_values) == len(set(source_values))
    assert PROTOCOL_REASON_CODES == frozenset(_EXPECTED_REASON_CODES)
    assert isinstance(PROTOCOL_REASON_CODES, frozenset)
    assert not hasattr(errors_module, "register_protocol_reason")
    before = PROTOCOL_REASON_CODES
    for name in (
        "yoetz.protocol.canonical",
        "yoetz.protocol.ids",
        "yoetz.protocol.coverage",
        "yoetz.protocol.schemas",
    ):
        if importlib.util.find_spec(name) is not None:
            importlib.import_module(name)
            assert errors_module.PROTOCOL_REASON_CODES is before
    with pytest.raises(ValueError, match="^unregistered_protocol_reason_code$"):
        ProtocolValueError("not_registered")
    with pytest.raises(ValueError, match="^unregistered_protocol_reason_code$"):
        ProtocolValueError(_StringSubclass("invalid_digest"))

    consumers = tuple(
        name
        for name in (
            "yoetz.protocol.canonical",
            "yoetz.protocol.ids",
            "yoetz.protocol.coverage",
            "yoetz.protocol.schemas",
        )
        if importlib.util.find_spec(name) is not None
    )
    orders = [("yoetz.protocol.errors", *consumers)]
    orders.extend(
        (consumer, "yoetz.protocol.errors", *(name for name in consumers if name != consumer))
        for consumer in consumers
    )
    for order in dict.fromkeys(orders):
        script = "\n".join(
            (
                "import importlib",
                f"order = {order!r}",
                "for name in order: importlib.import_module(name)",
                "import yoetz.protocol.errors as errors",
                f"expected = {_EXPECTED_REASON_CODES!r}",
                "actual = tuple(sorted(errors.PROTOCOL_REASON_CODES, key=lambda value: value.encode('ascii')))",
                "assert actual == expected",
                "assert not hasattr(errors, 'register_protocol_reason')",
            )
        )
        completed = subprocess.run(  # noqa: S603 - no shell and a fixed interpreter
            [sys.executable, "-I", "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr


def test_dependency_root_has_no_internal_imports() -> None:
    module_file = errors_module.__file__
    assert module_file is not None
    tree = ast.parse(Path(module_file).read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    relative_imports = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            relative_imports += node.level
            if node.module is not None:
                imported_modules.append(node.module)
    assert relative_imports == 0
    assert not any(module == "yoetz" or module.startswith("yoetz.") for module in imported_modules)


def test_operation_error_is_bounded() -> None:
    one_byte = PublicOperationError(PublicErrorCode.INTERNAL_ERROR, "x", False)
    assert one_byte.message == "x"
    valid = PublicOperationError(
        PublicErrorCode.BUNDLE_BUSY,
        "é" * 2048,
        True,
        _VALID_CORRELATION_ID,
        {"count": 1},
    )
    assert len(valid.message.encode()) == 4096
    for invalid in (
        "",
        "é" * 2049,
        "line\nfeed",
        "tab\tvalue",
        "delete\x7f",
        "nul\0",
        _StringSubclass("Safe message"),
    ):
        with pytest.raises(ProtocolValueError) as exc_info:
            PublicOperationError(PublicErrorCode.INTERNAL_ERROR, invalid, False)
        _assert_reason(exc_info, "public_error_invalid_message")
    with pytest.raises(ProtocolValueError) as surrogate_exc:
        PublicOperationError(PublicErrorCode.INTERNAL_ERROR, "\ud800", False)
    _assert_reason(surrogate_exc, "public_error_invalid_message")
    for invalid_id in (
        "bad_00000000-0000-4000-8000-000000000000",
        "err_00000000-0000-4000-8000-00000000000",
        "err_000000000000-4000-8000-000000000000",
        "err_00000000-0000-3000-8000-000000000000",
        "err_00000000-0000-4000-7000-000000000000",
        "err_00000000-0000-4000-8000-00000000000A",
        _StringSubclass(_VALID_CORRELATION_ID),
        1,
    ):
        with pytest.raises(ProtocolValueError) as exc_info:
            PublicOperationError(
                PublicErrorCode.INTERNAL_ERROR,
                "Safe message",
                False,
                cast(str, invalid_id),
            )
        _assert_reason(exc_info, "public_error_invalid_correlation_id")


def test_operation_error_exception_value_contract() -> None:
    original = {"count": 2, "unknown": _ExplosiveValue()}
    error = PublicOperationError(
        PublicErrorCode.LIMIT_EXCEEDED, "Bounded message", False, safe_details=original
    )
    original["count"] = 99
    assert is_dataclass(error)
    assert PublicOperationError.__slots__ == (
        "code",
        "message",
        "retryable",
        "correlation_id",
        "safe_details",
    )
    assert tuple(field.name for field in fields(error)) == PublicOperationError.__slots__
    assert error.args == ("Bounded message",)
    assert str(error) == "Bounded message"
    assert error.correlation_id is None
    assert dict(error.safe_details) == {"count": 2}
    assert isinstance(error.safe_details, MappingProxyType)
    rendered = repr(error)
    assert [rendered.index(name) for name in PublicOperationError.__slots__] == sorted(
        rendered.index(name) for name in PublicOperationError.__slots__
    )
    assert "unknown" not in rendered
    with pytest.raises(FrozenInstanceError):
        setattr(error, "message", "changed")


def test_safe_details_allowlist_and_types_are_exact() -> None:
    expected_keys = (
        "actual_version",
        "component",
        "count",
        "expected_version",
        "field",
        "limit",
        "method",
        "operation",
        "phase",
        "quarantine_code",
        "reason_code",
        "retry_after_ms",
        "schema_name",
        "state",
        "status",
        "view",
    )
    assert SAFE_DETAIL_KEYS == expected_keys
    accepted = normalize_safe_details(
        {
            "actual_version": "1.0.0+local",
            "component": _SafeEnum.READY,
            "count": 0,
            "expected_version": "V2-rc.1",
            "field": "/payload/~0/~1//",
            "limit": 9_007_199_254_740_991,
            "method": _SafeEnum.READY,
            "operation": _SafeEnum.READY,
            "phase": _SafeEnum.READY,
            "quarantine_code": "operation_lease_shape_invalid",
            "reason_code": "invalid_digest",
            "retry_after_ms": 1,
            "schema_name": "accepted-event",
            "state": _SafeEnum.READY,
            "status": _SafeEnum.READY,
            "view": _SafeEnum.READY,
        }
    )
    assert tuple(accepted) == expected_keys
    assert accepted["component"] == "ready"
    assert all(type(value) in {str, int} for value in accepted.values())
    assert isinstance(accepted, MappingProxyType)
    hostile = _KnownKeyOnlyMapping()
    assert dict(normalize_safe_details(hostile)) == {"count": 3}
    assert "secret" not in hostile.accessed
    assert normalize_safe_details(_RaisingMapping()) is normalize_safe_details(None)
    assert normalize_safe_details(_RaisingClass()) is normalize_safe_details(None)
    assert normalize_safe_details(_SpoofedMapping()) is normalize_safe_details(None)
    rejected_values: tuple[object, ...] = (
        True,
        -1,
        9_007_199_254_740_992,
        1.0,
        "1",
        _IntegerSubclass(1),
    )
    for integer_key in ("count", "limit", "retry_after_ms"):
        for rejected in rejected_values:
            assert not normalize_safe_details({integer_key: rejected})
    assert not normalize_safe_details({"component": "ready"})
    assert not normalize_safe_details({"component": _UnsafeEnum.UPPER})
    assert normalize_safe_details({"component": _LowerSnake64Enum.VALUE})
    assert not normalize_safe_details({"component": _LowerSnake65Enum.VALUE})
    assert not normalize_safe_details({"component": _NonStringValueEnum.VALUE})
    assert not normalize_safe_details({"component": _SubclassValueEnum.VALUE})
    assert not normalize_safe_details({"component": _SpoofedEnum()})
    assert normalize_safe_details({"component": _SingleReadEnum.READY}) == {"component": "ready"}
    assert not normalize_safe_details({"reason_code": "unknown"})
    assert not normalize_safe_details({"quarantine_code": "unknown"})
    quarantine_codes = (
        "operation_event_range_mismatch",
        "operation_kind_state_contradiction",
        "operation_lease_shape_invalid",
        "operation_result_digest_mismatch",
        "operation_resume_object_invalid",
    )
    for quarantine_code in quarantine_codes:
        assert normalize_safe_details({"quarantine_code": quarantine_code}) == {
            "quarantine_code": quarantine_code
        }
    assert normalize_safe_details({"field": ""})["field"] == ""
    assert normalize_safe_details({"field": "/"})["field"] == "/"
    assert normalize_safe_details({"field": "/" + "a" * 255})["field"] == "/" + "a" * 255
    for pointer in ("field", "/~", "/~2", "/\n", "/\x7f", "/é", "/" + "a" * 256):
        assert not normalize_safe_details({"field": pointer})
    assert normalize_safe_details({"actual_version": "a" * 64})
    assert not normalize_safe_details({"actual_version": "a" * 65})
    for version_key in ("actual_version", "expected_version"):
        for invalid_version in ("", "-1", "1 0", "é", "1\n"):
            assert not normalize_safe_details({version_key: invalid_version})
    assert normalize_safe_details({"schema_name": "a" * 128})
    assert not normalize_safe_details({"schema_name": "a" * 129})
    for invalid_schema_name in ("", "A", "a--b", "a-", "a_b", "é"):
        assert not normalize_safe_details({"schema_name": invalid_schema_name})
    subclass_details = {
        "actual_version": _StringSubclass("1.0.0"),
        "expected_version": _StringSubclass("1.0.0"),
        "field": _StringSubclass("/payload"),
        "quarantine_code": _StringSubclass("operation_lease_shape_invalid"),
        "reason_code": _StringSubclass("invalid_digest"),
        "schema_name": _StringSubclass("accepted-event"),
    }
    for key, value in subclass_details.items():
        assert not normalize_safe_details({key: value})


def test_constructor_validation_order_is_exact() -> None:
    with pytest.raises(TypeError, match="^public_error_code_wrong_type$"):
        PublicOperationError(cast(PublicErrorCode, "INTERNAL_ERROR"), "", cast(bool, 1))
    with pytest.raises(TypeError, match="^public_error_code_wrong_type$"):
        PublicOperationError(cast(PublicErrorCode, _SpoofedPublicErrorCode()), "Safe", False)
    with pytest.raises(TypeError, match="^public_error_code_wrong_type$"):
        PublicOperationError(cast(PublicErrorCode, _RaisingClass()), "Safe", False)
    with pytest.raises(ProtocolValueError) as message_exc:
        PublicOperationError(PublicErrorCode.INTERNAL_ERROR, "", cast(bool, 1))
    _assert_reason(message_exc, "public_error_invalid_message")
    with pytest.raises(TypeError, match="^public_error_retryable_wrong_type$"):
        PublicOperationError(
            PublicErrorCode.INTERNAL_ERROR,
            "Safe",
            cast(bool, 1),
            "not-an-id",
        )
    with pytest.raises(ProtocolValueError) as correlation_exc:
        PublicOperationError(
            PublicErrorCode.INTERNAL_ERROR,
            "Safe",
            False,
            "not-an-id",
            _RaisingMapping(),
        )
    _assert_reason(correlation_exc, "public_error_invalid_correlation_id")


def test_correlation_binding_lifecycle() -> None:
    unbound = PublicOperationError(PublicErrorCode.OPERATION_PENDING, "Still running", True)
    with pytest.raises(ProtocolValueError) as missing_exc:
        unbound.as_public_dict()
    _assert_reason(missing_exc, "public_error_missing_correlation_id")
    bound = unbound.bind_correlation_id(_VALID_CORRELATION_ID)
    assert bound is not unbound
    assert bound.correlation_id == _VALID_CORRELATION_ID
    assert bound.bind_correlation_id(_VALID_CORRELATION_ID) is bound
    with pytest.raises(ProtocolValueError) as different_exc:
        bound.bind_correlation_id(_OTHER_CORRELATION_ID)
    _assert_reason(different_exc, "public_error_invalid_correlation_id")
    with pytest.raises(ProtocolValueError) as type_exc:
        unbound.bind_correlation_id(cast(str, 1))
    _assert_reason(type_exc, "public_error_invalid_correlation_id")


def test_public_dict_shape_and_copy_are_exact() -> None:
    error = PublicOperationError(
        PublicErrorCode.INVALID_REQUEST,
        "Invalid request",
        False,
        _VALID_CORRELATION_ID,
        {"state": _SafeEnum.READY, "count": 2},
    )
    first = error.as_public_dict()
    assert tuple(first) == ("code", "message", "retryable", "correlation_id", "safe_details")
    assert first["code"] == "INVALID_REQUEST"
    details = cast(dict[str, SafeDetailValue], first["safe_details"])
    assert type(details) is dict
    assert tuple(details) == ("count", "state")
    first["message"] = "changed"
    details["count"] = 99
    second = error.as_public_dict()
    assert second["message"] == "Invalid request"
    assert second["safe_details"] == {"count": 2, "state": "ready"}
    empty = PublicOperationError(
        PublicErrorCode.INTERNAL_ERROR,
        "Internal error",
        False,
        _VALID_CORRELATION_ID,
    ).as_public_dict()
    assert tuple(empty) == ("code", "message", "retryable", "correlation_id")
