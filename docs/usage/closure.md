# Prepare closure without hand-authoring requests

Read the current inventory using the session and writer IDs returned by Yoetz:

```text
yoetz closure-prepare --session-id <session> --writer-id <writer>
yoetz closure-schema
```

The first command reads all pages at one frontier. The second describes the selection file accepted
by `closure-prepare --input selection.json`. Choose a phase: `attempt`, `respond`, `resolve`,
`claim`, or `receipt`. Select existing IDs from the inventory; fresh request, event, action and claim
IDs are generated for you. An attempt selects exact requested-item indexes on one obligation and
requires a description. A command attempt requires the actual command. A response requires a
finding, explicit disposition and reason. Resolve only after assessing acceptance, selecting actual
evidence or results. A claim requires your statement and explicitly selected obligations/evidence/
results; non-success results are placed in limitations.

Review the emitted request. Submit a publication as a dry run first, then replay that request ID
with `dry_run=false` after a successful preview. Submit responses through `respond` and receipts
through `receipt`; their fields differ. Prepare the next phase after each accepted write so its
frontier is current. Preparation never publishes and is not evidence of completion.

Use the emitted recovery query after a timeout. Preserve the original request identity while its
operation is pending; use the stored result if committed. Do not regenerate a request that might
already have committed.

Discover evidence before copying it: matching native snapshots can be reused, unrelated snapshots
must be excluded, and one clipped or unavailable item does not mean every excerpt is missing.
Command reconciliation distinguishes an observed attempt, a mismatch and unknown observation;
it never proves command success. A substituted command needs a recorded obligation revision with
rationale. An acknowledged finding can remain unresolved until a qualifying check proves absence.
