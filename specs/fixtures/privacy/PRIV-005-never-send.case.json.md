# fixtures/privacy/PRIV-005-never-send.case.json — non-overridable secret and protected-sink denial

**Wave:** C/E | **ADRs:** ADR-004, ADR-006, ADR-009 | **Imports (spec-tree):**
privacy schemas and data-egress protocol | **Imported by:** privacy property, conformance, packaging
and canary tests

## Purpose

Freeze denial of every never-send class under the most permissive profile and prevent leakage into
MCP responses or agent/LLM context as well as network requests.

## Public surface

Canonical fixture `yoetz.fixture-case/1.0.0`, ID `PRIV-005`, with one synthetic canary per exact
never-send token and mixed-content variants. Policy is `trusted_provider` and otherwise authorizes
all expressible user-content categories, proving the denial is non-overridable.

## Behavior

Encryption/recovery material, password, API key, auth token, cookie, private certificate, keyring
content, credential/hidden-auth file, unrelated env, raw database, unrestricted log/stderr/
transcript, out-of-scope file and ambiguous secret-like item are removed or block the indivisible
item before case construction. A blocking variant freezes a structural
`PreDispatchAuditDecision`/reservation and terminal `blocked_forbidden_data/never_send_detected`
receipt; it never fabricates a `DisclosureProposal`, prepared-case digest, commitment, or encrypted
copy of the denied bytes. That receipt requires `safe_failure_reason=never_send_detected`. No canary
appears in adapter input, preview metadata outside the
local human's explicitly selected excerpt, receipt, logs, errors, MCP result, agent context, LLM
context, traceback, crash diagnostics, or telemetry. Receipts retain only counts/reason/category and
no commitment to denied bytes.

## Errors and edge cases

Encoding splits, mixed case, whitespace insertion, base64 wrapping, nested JSON, file-extension
mislabeling, policy wildcard, and agent instruction to “ignore privacy” do not convert a protected
value into allowed content. Detector uncertainty chooses the stricter action.

## Invariants

The fixture is canonical, synthetic, deterministic, offline and test/sdist-only. Canary absence is
checked across network and local disclosure sinks; no real credential pattern/value is used.
The fixture also includes initial audit-reservation failure: it returns bounded `audit_failed`
without a proposal/receipt ID or any prompt, authorization, dispatch, or retained canary.

## Tests

`tests/property/test_egress_policy_properties.py`,
`tests/conformance/privacy/test_never_send_scope_and_channels.py`, and
`tests/packaging/test_privacy_docs_and_resources.py`.

## Open questions

None.
