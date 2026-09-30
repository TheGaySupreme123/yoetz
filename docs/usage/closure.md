# Prepare closure without hand-authoring requests

Read the current inventory using the session and writer IDs returned by Yoetz:

```text
yoetz closure-prepare --session-id <session> --writer-id <writer>
yoetz closure-schema
```

The first command reads all pages at one frontier. The second describes the selection file accepted
by `closure-prepare --input selection.json`.

The inventory can be large. Add `--output <path>` to save the complete result to a file instead of
printing it:

```text
yoetz closure-prepare --session-id <session> --writer-id <writer> --output closure.json
```

The file holds exactly what the command would otherwise print. It is readable only by you and is
replaced whole each time you prepare again, so it never holds a mix of two frontiers. The command
prints a short summary instead: the file's absolute path, size and SHA-256 digest, the frontier, and
the row count of each inventory view. Read fields from the saved file rather than preparing again;
prepare again only after a committed write moves the frontier. If the file cannot be written,
`closure_output_unwritable` says so and nothing is saved. Choose a phase: `attempt`, `respond`, `resolve`,
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

Preparation errors name a bounded reason and its next step. Remove already-resolved obligations
from a resolution selection; account for actual attempts before resolving an open obligation.
Human status shows up to three command attempts per obligation and the remaining count; use
JSON status to read every attempt on the page.
