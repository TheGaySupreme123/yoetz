# src/yoetz/config/load.py — configuration source merging and safe startup parse

**Wave:** C | **ADRs:** ADR-003, ADR-006, ADR-007, ADR-008, ADR-009 | **Imports (spec-tree):**
`specs/src/yoetz/config/models.md`, `specs/src/yoetz/config/paths.md` |
**Imported by:** `specs/src/yoetz/service/daemon.md` only

## Purpose

Owns the one algorithm that turns trusted `service run` startup flags, non-secret environment
values, the user config file, and built-in defaults into a validated `YoetzConfig`. Ordinary CLI,
MCP, agents, plugins, and future UI never call this loader or receive its result. Without this one
service-owned loader, precedence would be re-invented per adapter, project repositories could
smuggle configuration, and startup would parse untrusted TOML before any safety decision.

## Public surface

- `load_config(service_overrides: Mapping[str, str], env: Mapping[str, str], config_path: Path | None) -> YoetzConfig`
  — full service-start precedence merge and validation. `env` is passed in (never read from
  `os.environ` inside the function) so tests are hermetic. `service_overrides` can originate only
  from the trusted local `service run` command, never a workflow/control/MCP request.
- `parse_minimal_safe_config(env: Mapping[str, str], service_overrides: Mapping[str, str]) -> MinimalConfig`
  — service startup step 1 in `specs/src/yoetz/service/daemon.md`: resolves only `profile`, `storage.data_dir`,
  `logging.level`, and the config-file path, without touching the provider section, keyring, or
  any adapter import.
- `default_config_file_path() -> Path` — `config/paths.config_file_path()` re-export used by
  help text and diagnostics.
- `ENV_PREFIX = "YOETZ_"` — the sole recognized environment namespace.

`MinimalConfig` is the registered small frozen dataclass: `profile`, `data_dir`, `log_level`, and
`config_path_used`.

## Behavior

### Precedence (binding; highest first)

1. explicit trusted `service run` startup flag (`service_overrides`);
2. environment variable (`YOETZ_*`);
3. user config file under the platform-native config directory;
4. safe built-in default (the model defaults in `config/models.md`).

The merge is computed per *leaf key*, not per section: setting `YOETZ_LOG_LEVEL` does not
discard the file's `[logging] payloads` value.

`service_overrides` uses the exact dotted leaf keys corresponding to the table below
(`profile`, `storage.data_dir`, `storage.durability`, `verification.semantic`,
`verification.max_findings`, `logging.level`, `provider.provider_id`,
`provider.endpoint_profile_id`, `provider.endpoint_profile_version`, `provider.model`, and
`provider.timeout_seconds`) plus `config` for the file path. Unknown or secret-shaped override
names fail closed before their values are read. The explicit `config_path` argument, when present,
wins over `service_overrides["config"]`.

### Environment variable naming

`ENV_PREFIX + SECTION + "_" + KEY`, upper-cased, dots/section nesting flattened with `_`:

| Variable | Target key |
|---|---|
| `YOETZ_CONFIG` | path of the config file to read (replaces step 3's default path) |
| `YOETZ_PROFILE` | `profile` |
| `YOETZ_STORAGE_DATA_DIR` | `storage.data_dir` |
| `YOETZ_STORAGE_DURABILITY` | `storage.durability` |
| `YOETZ_VERIFICATION_SEMANTIC` | `verification.semantic` |
| `YOETZ_VERIFICATION_MAX_FINDINGS` | `verification.max_findings` |
| `YOETZ_LOG_LEVEL` | `logging.level` |
| `YOETZ_PROVIDER_ID` | `provider.provider_id` |
| `YOETZ_PROVIDER_ENDPOINT_PROFILE_ID` | `provider.endpoint_profile_id` |
| `YOETZ_PROVIDER_ENDPOINT_PROFILE_VERSION` | `provider.endpoint_profile_version` |
| `YOETZ_PROVIDER_MODEL` | `provider.model` |
| `YOETZ_PROVIDER_TIMEOUT_SECONDS` | `provider.timeout_seconds` |

There is intentionally no provider credential, token, secret, raw endpoint URL, privacy expansion,
or approval environment variable. Provider credentials enter only through the running service's
confidential secret-ingress/vault path and become opaque adapter-scoped handles. Environment and
`service run` configuration may identify an installed exact profile only for the service-start process; they
cannot authorize egress or loosen policy. Ordinary client process arguments are not configuration
sources.

An environment variable with the `YOETZ_` prefix that matches no known key is a hard
`ConfigError("unknown_config_env_var")` naming the variable (fail closed beats silent typo).
Environment and service-override values arrive as strings. After name-only validation and per-leaf
precedence selection, this loader explicitly parses the selected value into the owning strict model
type: integer keys accept only a whole base-10 integer string and become `int`; path keys become
`Path`; enum/string keys remain strings. It performs no float, boolean, whitespace, or generic
Pydantic coercion. The strict models then validate those already typed values and their bounds.

Environment validation is two-pass and binding. Before reading any environment *value*, the loader
enumerates the supplied key names and sorts their exact strings by Unicode code point, then
performs these checks in this exact order:

1. reject a prefixed name matching the case-insensitive secret-name denylist
   (`api_key|apikey|token|secret|password`, including historical provider-key spellings) as
   `ConfigError("secret_env_forbidden")`; if several exist, report only the lexicographically first
   exact key name;
2. reject any remaining unknown `YOETZ_` name as
   `ConfigError("unknown_config_env_var")`, again choosing the lexicographically first name;
3. only after every supplied key name passes, read values for the known names, apply the
   empty-string rule, select the winning value per leaf, explicitly parse that selected value as
   above, and pass the already typed merged mapping to strict model validation.

Secret-name rejection therefore wins even when the same environment also contains an unknown
non-secret `YOETZ_` name. No value—including a forbidden value—is fetched, compared, logged, or
echoed unless all name-only validation succeeds; either name failure performs zero value reads.

### TOML parsing

- Parser: stdlib `tomllib.load` on bytes read from the config file. `tomllib` rejects duplicate
  keys and invalid TOML natively; a `TOMLDecodeError` maps to `ConfigError("config_toml_invalid")`
  with the line/column only — never the offending text.
- The loader converts a selected TOML `storage.data_dir` string to `Path` (TOML has no path scalar),
  then the parsed dict goes to `YoetzConfig.model_validate(..., strict=True)`; `extra="forbid"` on
  every model gives strict unknown-key rejection at all nesting levels.
- File size is capped at 64 KiB before parsing (`ConfigError("config_file_too_large")`).
- A missing config file is not an error: defaults apply. An unreadable existing file
  (permissions) is `ConfigError("config_file_unreadable")`.
- `profile = "release-probe"` read from the *file* source is rejected
  (`ConfigError("release_probe_not_a_user_profile")`); it is accepted only from service-start
  environment/overrides.

### Project-config prohibition (binding)

`load_config` reads exactly one config file: the user config under
`config/paths.config_file_path()`, or the explicit `service run --config` /
`YOETZ_CONFIG` service-start override.
It never searches the current working directory, the repository root, `.yoetz*`, `.codex/`, or
any ancestor directory. Consequences:

- a repository checked out by the user cannot redirect `storage.data_dir` or key paths;
- a repository cannot enable network/model behavior. Selecting `local-openai` or an endpoint
  profile merely selects a candidate capability; effective policy authorization still requires the
  service-owned ADR-009 setup/transition use case;
- even an explicit `service run --config` pointing into a repository is honored as local-human
  startup intent, but emits one bounded stderr diagnostic with reason `explicit_project_config`;
  the path is never printed.
  The resulting `data_dir` must still pass every `config/paths.md` safety check (which rejects
  in-repo bundles regardless of consent).

### `parse_minimal_safe_config`

Runs before path validation, SQLite, keys, or logging exist (startup step 1). It applies the
same precedence to only the three minimal keys plus the config path, using a tolerant reader
that *ignores* all other sections without validating them (full validation happens in
`load_config` at step 1½, before any adapter is constructed). It must not raise on unknown keys
in *other* sections — a broken `[provider]` block must not prevent reading `logging.level` for
the error report — but it does fail on unparseable TOML and on an invalid `profile` value.

### Ordering with the rest of startup

`load_config` performs no I/O beyond reading the one config file and, only for an explicit config
path, the bounded ancestor marker check required to emit `explicit_project_config`. It does not resolve or create
`data_dir` (that is `config/paths.md`), does not construct a provider adapter (the ready service
gateway composition does so only when capability, vault, and effective policy all permit), and does not configure logging (caller wires
`observability/logging.md` using the returned config).

Ordinary CLI/MCP clients use deterministic platform-native service endpoint discovery plus the
authenticated control handshake. They do not read `YoetzConfig`, `YOETZ_CONFIG`, storage or
provider profile values, and cannot point the service client at a caller-selected endpoint. Their
own bounded rendering/log-level preferences, if any, are separate client-only values with no
storage, key, provider, privacy, or endpoint authority.

## Errors and edge cases

- All failures are `ConfigError(reason_code)`; `service run` exits `2` before binding the control
  endpoint and writes one structured stderr line (`observability/logging.md`). Ordinary CLI/MCP
  clients never parse this config; they report bounded service absence/locked status instead.
- Conflicting sources are not an error; precedence resolves them silently and
  `MinimalConfig.config_path_used` records which file (if any) was read for diagnostics.
- Any prefixed credential/secret environment key, including historical provider-key spellings, is
  rejected as `ConfigError("secret_env_forbidden")` during the first name-only pass; the value is
  never read, logged, or echoed, and this rejection takes precedence over unknown-name rejection.
- Empty-string environment values are treated as unset (documented; matches common tooling).
- Strict Pydantic validation never performs string-to-integer or string-to-path coercion for
  environment/service overrides; observing an unparsed selected value is a loader defect.

## Invariants

- Exactly one config file is read per service generation; ordinary client processes read none; no
  directory search or repo-config discovery exists.
- Secrets never enter `YoetzConfig`, environment-derived values, TOML, CLI/MCP arguments, logs, or
  error messages.
- Precedence is service-start override > env > file > default, per leaf key, once.
- `load_config` is a pure function of `(service_overrides, env, file bytes)` — deterministic and
  fully testable without a filesystem beyond the one file read.

## Tests

- `specs/tests/unit.md` — `tests/unit/config/test_load_precedence.py`: precedence matrix per key, env
  naming table, unknown/secret env rejection, TOML duplicate key/oversize/invalid, missing vs
  unreadable file, release-probe source rule, empty-string env, and absence of every credential
  ingress path.
- `specs/tests/integration.md` — startup uses `parse_minimal_safe_config` before path checks;
  broken `[provider]` block still yields a readable minimal config.
- `specs/tests/subprocess.md` — `service run` with invalid config binds no endpoint; MCP/ordinary
  CLI ignore the service config source and report unavailability without stdout contamination.

## Open questions

None.

An explicit project-contained service config is honored only as direct local-human
service-start intent and always emits the bounded `explicit_project_config` warning; project
discovery remains forbidden.
