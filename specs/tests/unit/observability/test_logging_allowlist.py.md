# tests/unit/observability/test_logging_allowlist.py — structured logging allowlist and redaction

**Wave:** F | **ADRs:** ADR-005, ADR-007 | **Imports (spec-tree):**
`src/yoetz/observability/logging.md`
**Imported by:** the observability unit suite

## Purpose

Lock the logging allowlist so public logs stay structured, bounded, and free of payload leaks.

## Public surface

- `test_allowlisted_fields_are_preserved` — the approved fields survive structured logging.
- `test_payload_and_secret_fields_are_redacted` — user payloads and secrets never reach logs.
- `test_non_yoetz_logger_names_are_suppressed_or_formed` — third-party loggers do not expand the
  allowlist.
- `test_log_record_shapes_are_bounded` — logged values stay small and predictable.
- `test_exception_objects_are_never_formatted_or_captured` — hostile exception/traceback hooks are
  never invoked and no raw diagnostic artifact is created.
- `test_service_and_confidential_helper_filters_are_exact` — all-level dependency records are
  structurally replaced and YZH1/YZS1 values never become logger arguments or sink bytes.

## Behavior

The suite proves:

- logging uses the approved field list only;
- payload text is absent or redacted;
- third-party logger noise does not bypass the allowlist;
- structured records stay bounded in size and shape;
- unexpected/fatal helpers emit only correlation identity and fixed outcome, never exception
  message, args, chain, locals, source, path, `exc_info`, stack, or traceback;
- all four `LogMode` values have no raw traceback file/debug sink. A recursive scan of the isolated
  process roots after each exception proves no diagnostic artifact was created;
- service and confidential-helper install stderr-only structured sinks plus all-level root/handler/
  `lastResort` filters; the helper creates no stdout, file, or `/dev/tty` log sink;
- synthetic YZH1 preview/decision and YZS1 binding/secret canaries supplied through message, args,
  fields, and `exc_info` are absent from every captured surface.

## Errors and edge cases

- A log line that includes a payload blob fails.
- A logger name outside the allowlist fails unless explicitly permitted.
- Any exception stringification/traceback-format call or new raw diagnostic file fails, even when
  a test marks the file owner-only or selects release-probe configuration.

## Invariants

1. Logs are structured and bounded.
2. Payloads do not leak through logs.
3. Allowlists are explicit.
4. v0.1 diagnostics are structural identity only; raw traceback capture is absent.

## Tests

- `tests/unit/observability/test_logging_allowlist.py`

## Open questions

None.
