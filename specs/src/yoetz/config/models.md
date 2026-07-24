# src/yoetz/config/models.py — strict configuration models and profiles

**Wave:** C | **ADRs:** ADR-003, ADR-004, ADR-006, ADR-007, ADR-008, ADR-009 | **Imports
(spec-tree):** `config/privacy.md` plus pure Pydantic/stdlib | **Imported by:**
`specs/src/yoetz/config/load.md`, `specs/src/yoetz/config/paths.md`,
`specs/src/yoetz/service/daemon.md`, `specs/src/yoetz/application/service.md`

## Purpose

Defines the one validated configuration object (`YoetzConfig`) each trusted local-service
generation consumes and the four named runtime composition profiles. Ordinary CLI/MCP/UI clients
never receive or parse this object. Runtime profiles select possible service components; they never
authorize disclosure. Privacy config is only a denied first-run seed; after it is atomically
persisted, the service-owned policy store is the only authority for network/local-model/agent-
context disclosure. The durable `PrivacyProfile` governs LLM inference/content only; the durable
global network ceiling and five channel policies are distinct authority dimensions.

## Public surface

- `class YoetzConfig` — frozen strict Pydantic model of the complete configuration
  (`schema_version`, `profile`, `storage`, `verification`, `logging`, `privacy`, `provider`,
  `local_model`).
- `class StorageConfig` — the `[storage]` section.
- `class VerificationConfig` — the `[verification]` section.
- `class LoggingConfig` — the `[logging]` section.
- `class PrivacyBootstrapConfig` — imported from `config/privacy.md`; safe first-run seed only.
- `class ProviderProfileConfig` — nonsecret exact external-provider identity/capability selection.
- imported domain `ProviderDataUseProfile` — installed nonsecret recommendation metadata resolved
  from that exact capability profile; this config module does not define or accept it as
  user-authored configuration.
- `class LocalModelProfileConfig` — nonsecret exact installed AF_UNIX local-model capability
  selection; it contains no socket locator or launch instruction.
- `class ProfileCapabilities` (frozen dataclass) — `network: NetworkPolicy`,
  `semantic: SemanticPolicy` derived per profile.
- `NetworkPolicy` is exactly `denied|candidate_external|explicit_per_probe`.
- `SemanticPolicy` is exactly
  `optional_local_model|optional_external|scripted_fake|no_implicit_model`.
- `PROFILE_CAPABILITIES: Mapping[str, ProfileCapabilities]` — the capability table below.
- `class ConfigError(Exception)` — carries a bounded `reason_code: str`; never free text copied
  from input; registered in `specs/INTERFACES.md`.

All models use `ConfigDict(strict=True, extra="forbid", frozen=True, validate_default=True)`.
Unknown keys anywhere in the document are a hard `ConfigError("unknown_config_key")`.

## Behavior

### Top-level fields

| Field | Type | Default | Rule |
|---|---|---|---|
| `schema_version` | `Literal["1"]` | `"1"` | Any other value → `ConfigError("config_schema_unsupported")` |
| `profile` | `Literal["strict-local", "local-openai", "test-fake", "release-probe"]` | `"strict-local"` | Selects the capability row below |
| `storage` | `StorageConfig` | defaults | See below |
| `verification` | `VerificationConfig` | defaults | See below |
| `logging` | `LoggingConfig` | defaults | See below |
| `privacy` | `PrivacyBootstrapConfig` | safe local-only, global-network-denied first-run seed | Used only if no policy exists |
| `provider` | `ProviderProfileConfig \| None` | `None` | Structural capability identity only; not disclosure authority |
| `local_model` | `LocalModelProfileConfig \| None` | `None` | Exact installed local capability selection only; not disclosure authority |

### Runtime capability table (composition contract, never privacy authority)

| Profile | Network | Semantic | Intended use |
|---|---|---|---|
| `strict-local` | Yoetz network denied | Deterministic plus optional separately approved AF_UNIX local model | Default private Yoetz path; “offline” includes the separate runtime only with exact sandbox evidence |
| `local-openai` | Candidate external transport, constructed only when effective privacy policy permits | Optional | Exact-profile semantic review |
| `test-fake` | Denied | Scripted fake (`adapters/providers/fake.py`) | Deterministic unit/integration/conformance |
| `release-probe` | Explicit per probe | No implicit model call | CI-only installation/capability evidence; not a public v0.1 command |

`PROFILE_CAPABILITIES` limits which components may be considered, but never grants network access.
The semantic-provider factory also requires an unlocked service vault, exact provider binding, and
effective ADR-009 policy. `local_only` prevents external LLM-provider construction regardless of
this table; it does not decide whether a separately reviewed bounded non-LLM channel is enabled.

### `StorageConfig`

- `data_dir: Path | None = None` — when `None`, `config/paths.bundle_root()` supplies the
  platform-native default. When set, the path still passes every safety check in
  `config/paths.md` (repo/sync/network/world-readable/symlink rejection); config cannot opt out.
- `durability: Literal["full"] = "full"` — v0.1 accepts only `"full"` (`synchronous=FULL`,
  ADR-003). Any other value → `ConfigError("durability_unsupported")`.

### `VerificationConfig`

- `semantic: Literal["disabled", "optional", "required"] = "optional"` — maps to check modes
  `deterministic_only` / `semantic_if_configured` / `semantic_required` as the default for
  requests that do not specify a mode. Under the fail-safe `strict-local + local_only` seed this
  still performs deterministic work with zero external egress; once a user configures an eligible
  semantic sink and standing policy, ordinary checks actually invoke it without another hidden
  mode switch.
- `max_findings: int = MAX_FINDINGS_DEFAULT` (3) — validated `1 <= v <= MAX_FINDINGS_LIMIT`
  (10); out of range → `ConfigError("max_findings_out_of_range")`.

### `LoggingConfig`

- `level: Literal["debug", "info", "warning", "error"] = "info"`.
- `payloads: bool = False` — v0.1 rejects `payloads = true` with
  `ConfigError("payload_logging_forbidden")`: the observability allowlist
  (`observability/logging.md`) has no payload channel, so accepting `true` would be a lie.

### `ProviderProfileConfig`

- `provider_id: str`, `endpoint_profile_id: str`, `endpoint_profile_version: str`, and `model: str`
  are structural identifiers matched against an installed, release-tested profile. There is still
  **no free `base_url` / host / port / headers field** on the ordinary provider surface.
- Optional nested `owner_declared_endpoint: OwnerDeclaredEndpointConfig | None` is accepted **only**
  when `endpoint_profile_id == "owner-declared-openai-responses"` (ADR-014). That nested object
  carries exactly `https_origin` — HTTPS scheme, host, optional port; no userinfo, query, fragment,
  or path other than empty/`/`. Path on the wire remains the profile-fixed `/v1/responses`.
  Presence on any other endpoint profile → `ConfigError("owner_declared_endpoint_forbidden")`;
  absence on the owner-declared profile → `ConfigError("owner_declared_endpoint_required")`.
  Invalid origins → `ConfigError("https_origin_invalid")`.
- Official OpenAI continues to use `endpoint_profile_id = "openai-responses"` with the bundled host
  `api.openai.com` resolved by the adapter, not from TOML.
- Reviewed setup presets also use the exact endpoint-profile identities
  `anthropic-openai-chat-completions`, `google-gemini-openai-chat-completions`,
  `openrouter-openai-chat-completions`, and `vercel-ai-gateway-openai-responses`. These identities
  preserve the provider and wire-style choice in nonsecret config; they are not live-dispatch or
  release-support claims until the matching capability fixture, data-use record, adapter, and ready
  composition satisfy ADR-006/E-007.
- `timeout_seconds: int = 60`; `max_retries: int = 2` (Yoetz-owned budget, ADR-006) —
  both bounded (`1..300`, `0..2`).
- `capability_profile: str` is required and must match the endpoint/model tuple.
- The installed profile named by that tuple carries a versioned `ProviderDataUseProfile` with
  customer-content-training, retention, provider-human-access, review/expiry, and evidence-digest
  facts. Config does not let a user self-assert those facts. Owner-declared hosts default to
  `unknown` data-use facts and never earn the upstream `assisted` recommendation badge.
- **No secret or confidential locator fields exist.** There is no `api_key`, `token`, `password`,
  credential reference, socket path, header, or generic URL field, and
  `extra="forbid"` rejects any attempt to add one. A cross-field validator additionally scans
  the raw pre-validation key names for `api_key`/`apikey`/`token`/`secret`/`password`
  (case-insensitive) and raises `ConfigError("secret_in_config")` with the offending *key name
  only*, never its value. Provider credentials are provisioned through the local service's
  confidential secret-ingress/vault path and reach the adapter only as opaque scoped handles.

### `LocalModelProfileConfig`

- Required exact structural identifiers: `profile_id`, `profile_version`, `endpoint_profile_id`,
  `endpoint_profile_version`, `model`, `protocol_version`, `judgment_schema_version`, and
  `capability_digest`. Each is a bounded ASCII identifier/digest and the complete tuple must equal
  one entry in the installed `InstalledLocalModelProfileRegistry` owned by
  `adapters/providers/local_model.md`; config validation itself does not access that registry.
- `timeout_seconds: int = 60`, bounded `1..300`.
- The closed model has no socket path, URL, host, port, command, executable, arguments, environment,
  headers, download source, discovery mode, or arbitrary options mapping. Raw pre-validation keys
  matching those locator/launch names or the provider secret-name denylist fail with
  `ConfigError("local_model_locator_forbidden")` or `ConfigError("secret_in_config")`.
- This object selects a *possible* local capability. Durable effective privacy policy must
  independently enable local-model disclosure and bind the same exact profile tuple before the
  adapter is constructed or receives a case.

### Cross-field validation (model validator, in order)

1. `profile == "strict-local"` → external `provider` must be `None`; otherwise
   `ConfigError("strict_local_forbids_provider")`. `verification.semantic` may be optional/required
   with or without `local_model`; absent/uninstalled/policy-disabled local capability resolves to
   unavailable/incomplete according to check mode, never external fallback.
2. `profile == "local-openai"` → `local_model` must be `None`; otherwise
   `ConfigError("external_profile_forbids_local_model")`. v0.1 never races or implicitly chooses
   between local and external semantic sinks.
3. `profile == "test-fake"` → `provider` and `local_model` must both be `None` (the fake is wired by
   tests, not config); violation → `ConfigError("test_fake_forbids_provider")` or
   `ConfigError("test_fake_forbids_local_model")`.
4. `profile == "local-openai"` and `verification.semantic != "disabled"` → `provider` is
   required as a structural capability selection; absence →
   `ConfigError("provider_required_for_semantic")`. This does not authorize egress.
5. `profile == "release-probe"` is accepted from environment/CLI in CI contexts only; when it
   appears in a user config file, `load.md` rejects it (`ConfigError("release_probe_not_a_user_profile")`).
   The model itself stays source-agnostic.

Validation is pure: no filesystem, network, keyring, or clock access occurs inside model
validation. Path *existence/safety* is checked later by `config/paths.md` at startup step 2.

## Errors and edge cases

- Every failure is `ConfigError(reason_code)` with a bounded reviewed reason code. Only the trusted
  `service run` startup path loads this model; it exits before binding either local endpoint and
  emits only the bounded reason (and, where explicitly safe, an offending key name), never a
  configuration value. Ordinary CLI and MCP bridge processes do not parse this config at all.
- Unknown top-level sections, unknown keys inside sections, wrong types (strict mode: no
  string→int coercion), and duplicate TOML keys (rejected by the parser in `load.md`) never
  produce a partially built config.
- A config that validates but names an unusable `data_dir` fails later with `STORAGE_UNSAFE`,
  not here — model validation must stay deterministic and I/O-free.

## Invariants

- One `YoetzConfig` instance per service generation; it is frozen, never exposed to ordinary
  clients, and never mutated after startup.
- Secrets never live in normal TOML, environment, or CLI/MCP values (ADR-004, ADR-008, ADR-009 and
  `specs/src/yoetz/observability/privacy.md`);
  the model
  makes that structurally impossible rather than advisory.
- Effective `local_only` durable privacy policy implies the external semantic-provider adapter is
  never constructed; runtime profile alone can only narrow that result. A zero-network claim also
  requires `network_egress_permitted=false` and every network channel disabled.
- A local-model adapter exists only when configuration, installed exact-profile registry,
  measured socket capability, and durable local-disclosure policy all bind the same tuple.
- Capability is table-driven from `PROFILE_CAPABILITIES`; no adapter branches on the profile
  string directly.
- Model validation performs no I/O and depends on no environment state.

## Tests

- `specs/tests/unit.md` — `tests/unit/config/test_models.py`: defaults, every reason code,
  every profile × global-ceiling × privacy-seed cross-field rule, exact local-model shape/profile combinations,
  secret/URL/socket/launch rejection, unknown-key rejection, frozen-ness.
- Zero-egress conformance asserts the external-provider factory is never invoked under
  `strict-local`; optional local model uses only the exact approved AF_UNIX profile.
- `specs/tests/packaging.md` — `config.schema.json` (see `specs/schemas/README.md`) is
  generated from these models and digest-frozen.

## Open questions

None.
