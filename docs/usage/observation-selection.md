# Observation selection

Observation selection controls how much structural host activity Yoetz keeps for a workspace. It
is a retention and capacity choice. It does not change what the host is allowed to do, what content
may be captured, or what a semantic provider may receive.

Focused with the standard capacity is the default. In Focused mode, Yoetz can account for proven
successful routine reads, searches, and inventory calls in bounded summaries. A summary keeps the
native identities and source positions it represents, but it is not per-call content evidence or a
verification result. Detailed mode keeps eligible routine calls as individual records and allows
more bounded optional detail while pressure remains healthy.

The classifier is deterministic and conservative. Failures, denials, cancellations, interrupted,
partial, and unknown outcomes remain individual observations. Edits and side effects, declared tests and
checks, negative verification, and reads explicitly linked to an obligation, claim, or finding
remain individual observations in both modes. A read-only command can still be important evidence.
Caller prose or an action label cannot make an unknown, ambiguous, or failed operation routine.

Capacity is independent from detail:

| Capacity | Queue-count target | Availability |
| --- | ---: | --- |
| standard | 512 | Default, with Focused or Detailed |
| larger | 2,048 | Explicit owner choice, with Focused or Detailed |
| largest | 8,192 | Explicit owner choice, with Focused or Detailed |

Each profile also has finite byte, state, pending-pair, protected-reserve, fair-share, diagnostic,
and capture budgets. The larger profiles are bounded choices whose performance validation remains
provisional; use the preview and status output to see the selected and effective values and their
current pressure. A larger queue does not promise higher sustained throughput.

## Choose a selection

Inspect the current choice for a host session:

```text
yoetz observe selection-status --workspace /exact/project \
  --session-id <host-session-id> --json
```

Preview a temporary session choice first:

```text
yoetz observe selection-preview --workspace /exact/project \
  --detail detailed --capacity larger --session-id <host-session-id> --json
```

Apply the exact `preview_digest` returned by that preview:

```text
yoetz observe selection-apply --workspace /exact/project \
  --detail detailed --capacity larger --session-id <host-session-id> \
  --accept --preview-digest <preview-digest> --json
```

The default is a temporary session override. It is tied to the selected host session and can have
an optional RFC3339 UTC `--expires-at` deadline. To make an explicit workspace default, preview and
apply with `--persist` and omit `--session-id`:

```text
yoetz observe selection-preview --workspace /exact/project \
  --detail focused --capacity standard --persist --json
yoetz observe selection-apply --workspace /exact/project \
  --detail focused --capacity standard --persist \
  --accept --preview-digest <preview-digest> --json
```

Revoke the setting at the same scope:

```text
yoetz observe selection-revoke --workspace /exact/project \
  --session-id <host-session-id> --json
yoetz observe selection-revoke --workspace /exact/project \
  --persist --json
```

Expiry, revoke, or a lower capacity changes future admission. Accepted records continue through
the bounded drain and are not rewritten or deleted just to match the new target. Under pressure,
the effective mode can temporarily be Focused even when Detailed remains selected. Status exposes
both values, the setting origin and expiry, pressure, accounting, capture backlog, and historical
loss. At a hard limit, replayable input pauses for retry and non-replayable input receives bounded
loss accounting; the host is not held up to preserve an observation.

After current usage falls below every hard threshold, pressure moves from `hard_limit` to `high`
and structural admission can resume when the next input fits. Optional detail stays reduced until
all dimensions remain at or below 45% for ten seconds. Retained history still consumes the byte
budget: an empty pending queue does not imply healthy pressure or restored historical coverage.
Ending a host session retires its pressure snapshot, while preserving pending work and history.

## Protect evidence and promote a buffered read

When a later claim or finding will depend on a read, protect it before the read occurs:

```text
yoetz observe protect-read --workspace /exact/project \
  --session-id <host-session-id> --reference clm_<existing-id> \
  --count 1 --json
```

The reference must be an existing or planned `obl_`, `clm_`, or `fnd_` identifier. Protection is
limited to 32 outstanding logical reads, is bound to the exact native read identity, and consumes
one slot only when its logical post event is admitted. It expires after ten minutes by default;
an explicit `--expires-at` cannot extend that bound. The command requires active observation
consent and narrows retention only. It grants no content, privacy, provider, credential, or
network authority.

Before a buffered routine read is delivered, its native source identity can be promoted:

```text
yoetz observe promote --workspace /exact/project \
  --source-identity <source-identity> --json
```

Promotion preserves the original identity, route, and observed time as an individual structural
record. It is available only while that identity remains in the bounded buffer. Once the buffer
has been delivered, the result is `promotion_window_closed` and `content_availability` is
`not_retained`. If the historical bytes are needed, rerun or reacquire the current state and label
it as a new observation with its new time and state; promotion cannot recover old bytes or prove
the earlier state retroactively.

## Content, pressure, and deadlines

Selection runs before optional native content extraction and structural admission. Native content
capture is a separate authority decision. Claude Code and Cursor require their exact ordinary
profile to be selected and separately enabled; Codex's supported hook capture arm follows active
observation consent and is profileless. See [Privacy and semantic review](privacy-and-semantic-review.md)
and the [Codex](../runbooks/codex-integration.md), [Claude Code](../runbooks/claude-code-integration.md),
and [Cursor](../runbooks/cursor-integration.md) runbooks for host-specific setup and limits.

The native capture lane has workspace-wide limits independent of the selected queue: at most 512
staging or pending capture tickets and 128 MiB of captured content. A larger structural profile
does not raise either limit. Capture is bounded by each host hook's deadline and drain window. A
timeout, cancellation, missing post event, or incomplete content group remains incomplete, partial,
unknown, or unpaired as appropriate and is surfaced as a content gap when the boundary permits; it
is not silently converted into a successful routine summary. Capture status describes configuration,
not proof that bytes were captured or selected for a check.

Summary coverage, current pressure, capture availability, delivery, and selection for a particular
check are separate facts. A summary or an empty queue cannot support a claim of full per-call
content observation, and selection does not replace a check or receipt.

Self-observation has a narrower boundary. Explicit Yoetz MCP workflow names are recognized as
service-owned reads, so their successful projections stay local. A host shell event such as
`exec_command yoetz observe status`, `yoetz observe selection-status`, `yoetz observe
selection-preview`, or `yoetz closure-prepare` does not carry an authenticated identity for the
launcher that actually ran. Even an absolute path written in the command text is untrusted at this
boundary. These local CLI reads therefore remain ordinary shell observations and can consume the
structural, capture, or materialization work available to that host. Failures, mutations, and
ambiguous shell remain individually retained. This is a current support boundary; use an explicit
MCP status or receipt route when that non-amplifying self-read path is available, and account for
local CLI reads in coverage and capacity.
