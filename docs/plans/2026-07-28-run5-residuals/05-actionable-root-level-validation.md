# 05 — Root-level request rules must name safe corrective fields

**Severity:** medium  
**PR boundary:** JSON-Schema-to-MCP validation location projection

## The defect

The dogfood needed three `start` calls. The first invalid request ID produced an excellent field
pointer and pattern. The second request paired `workspace_ref` without `external_ref` and returned
only a generic invalid-arguments message.

The actor-type change in the successful third request was unnecessary:
`logical_agent` is admitted. The only failing rule was the root-level
`dependentRequired` relationship.

`jsonschema` reports that object rule at an empty instance path. The model validator converts it to
bare `ValueError("schema_instance_invalid")`; Pydantic records `loc=()`;
`safe_validation_locations` drops the empty pointer. This is a machinery gap, not disclosure
filtering: both `workspace_ref` and `external_ref` are already safe location segments.

The same class includes root-level `if/then`, `allOf`, and `anyOf` rules.

## Design

### 1. Project known object-rule failures into safe locations

When `_validate_model_against_schema` catches a root-level validation error, inspect only checked-in
schema metadata and the validator kind. Produce fixed safe locations for:

- `dependentRequired` — the present field and required peer;
- `required` inside a selected `if/then` branch;
- closed discriminated `oneOf|anyOf` failures where the discriminator path is known.

Never derive a field name from an unknown caller-supplied key. Continue to suppress unsafe
`additionalProperties` names.

### 2. Preserve rule meaning

Add closed safe reason tokens such as `paired_field_required` or `conditional_field_required`
instead of collapsing every object rule into `invalid_type_or_value`.

Authoring hints are generated only from the packaged schema: name the required peer and, where
bounded, the condition that activated it. Do not echo the submitted value.

### 3. Keep validation failures total

Failure to derive a safe object-rule location falls back to the current generic invalid request.
Hint generation can never raise or turn `INVALID_REQUEST` into `INTERNAL_ERROR`.

## Files

- `src/yoetz/protocol/models.py`
- `src/yoetz/mcp/errors.py`
- `src/yoetz/mcp/server.py`
- request schemas/fixtures only if reason projection is represented there
- validation and authoring-hint tests

## Tests

- The exact `workspace_ref`-without-`external_ref` request names both safe fields and the pairing
  rule.
- The inverse missing-pair case is equally actionable.
- `logical_agent` without refs remains valid, proving the red herring stays closed.
- Root-level attach `if/then` failure names the safe required alternative.
- Nested enum/pattern pointers retain existing behavior.
- Hostile unknown property names and values never reach message, details, or logs.
- Complex or unrecognized schema rules degrade to a bounded generic error.

## Done

The second start attempt tells an agent exactly which safe relationship is invalid without source
inspection or unnecessary field changes.

## Dogfood observable

One intentionally unpaired start request is corrected on the next attempt using only the public
error and guidance.

## Out of scope

Event-state invariants discovered during reducer replay (plan 06).

