# ADR-031 — The service captures the check-time change as one reviewed object

**Status:** Accepted for issue #883. The maintainer asked for this scoped work in the issue's
2026-09-29 acceptance boundary (items 1 and 3) and recorded the design-gate acknowledgement for
the privacy/egress and storage changes it needs.
**Implemented by:** `src/yoetz/ports/change_capture.py`, `src/yoetz/adapters/git_change_capture.py`,
the public seam of `src/yoetz/adapters/git_subject_state.py`, `src/yoetz/application/check_change.py`,
`CheckTimeChange` and `_check_time_change_reservation` in `src/yoetz/application/semantic_case.py`,
the semantic evaluator in `src/yoetz/service/ready_composition.py`, `start`/`check` in
`src/yoetz/application/service.py`, the task-base root in `src/yoetz/adapters/sqlite/repository.py`
and `src/yoetz/adapters/memory/ledger.py`, the shown-file record (`CheckChangeShownFiles` in
`src/yoetz/domain/events.py`) and resolution rule (`src/yoetz/kernel/finding_resolution.py`,
`src/yoetz/kernel/projections.py`), and the suites `tests/unit/adapters/test_git_change_capture.py`,
`tests/unit/application/test_semantic_case_check_change.py`,
`tests/unit/application/test_check_time_change_facade.py`,
`tests/integration/service/test_check_time_change.py` and the ledger conformance suite.
**Relates to:** ADR-006 (AI-powered review packet), ADR-008 (service trust boundary), ADR-009
(egress), ADR-011 (structural subject-state capture), and issues #883, #904, #907, #920.

## Context

The review packet was built only from ledger text and observation captures. Captures cover the
edits a host exposes as visible tool input. They miss edits made by scripts (`sed -i`, Python
rewrites, `git apply <file>`), commits, hooks that did not fire or were not admitted, and every edit
under Cursor's ordinary profile, which does not subscribe `afterFileEdit`. In the DeepSWE v2 run a
repair made with 34 shell rewrites and no `apply_patch` reached the reviewer only as the agent's
digest, and the reviewer judged the pre-repair code.

ADR-006 decision 12 and ADR-011 decision 6 kept every repository read out of the service: a
content-returning read needed its own decision. This is that decision, for exactly one object.

## Decisions

1. **One object per check, captured by the service.** When a check's AI-powered review recipe
   selects diff excerpts (`targeted_excerpts`, a positive excerpt count and the `diff` kind), the
   service renders the change from the task base to the working tree: committed, staged and
   unstaged changes to tracked files, plus untracked files Git does not ignore (`.gitignore`,
   `.git/info/exclude` and the owner's global Git ignore file). A short header names the base,
   the counts and every changed file with its line counts, and says which files are not shown and
   why. This covers script edits, commits and missed hooks on every host at once. Because the base
   is a commit, uncommitted or untracked work that already existed when the task started is part of
   the change too, and the header says so. A recipe without diff excerpts captures nothing and
   reports nothing, exactly as it declines any other excerpt.
2. **Only the check's own repository.** The capture reads only the locator the check's
   authenticated control connection supplied at handshake. The service keeps that locator in
   memory beside the repository commitment it derived from it, for the life of the connection
   only; it is never stored, logged or returned. The capture runs only when that commitment equals
   the task route's own. Request bodies, environment variables and ambient discovery never select
   a directory.
3. **The task base is the commit HEAD named when the task was created.** A `start` that creates a
   task (including a delegated child) records it before its result returns, so no agent edit or
   commit precedes it. An empty repository records the empty tree. The base is an encrypted
   `change_capture` object; the task bundle keeps its pointer in `bundle_meta` and inventories the
   object as a root. Attach and resume never record a base, because a later base would silently
   hide the commits made in between. A task without one (created before this decision, or whose
   start base could not be recorded) gets it from its first check instead: that check records HEAD
   through the same seam as a `first_check` base, and every later check of the task diffs from it,
   so work committed between checks stays in the change and files keep matching across checks. The
   header names that commit, and every such check still reports
   `check_time_change_base_unavailable`, because work committed before that first check is not in
   the change. Only when no base can be kept or resolved is the change shown against HEAD.
4. **Read-only and bounded.** Git runs through the ADR-011 hardened runner: no shell, no global or
   system config, hooks, fsmonitor, external diff, textconv or credential helper, and every diff
   passes `--no-ext-diff --no-textconv`. Git transports are disabled and partial clones are
   refused, so a capture never fetches. The ADR-011 root and metadata fences apply (a real `.git`
   directory, no alternates). Because a diff against the working tree runs any `clean` filter the
   repository defines, the capture also asks Git for its whole effective configuration
   (`git config --list --name-only --includes --show-scope`) and refuses any `filter.*`,
   `include.*`, `includeIf.*` or partial-clone key, from whichever file or include it came. That
   needs Git 2.26 or later; an older Git cannot answer, and the capture is unavailable rather than
   taken without the check. Untracked files honour `.gitignore`, `.git/info/exclude`, a
   repository-level `core.excludesFile` and the owner's global Git ignore file. They are opened one
   path component at a time beneath the validated root descriptor without following links; only
   regular files owned by the service user with a single link, at most 64 KiB each and 500 in all,
   are read. Files whose names follow common credential conventions (`.env`, `.env.*`, `.netrc`,
   `.npmrc`, `id_rsa`, `*.pem`, `*.key`, `*.tfvars` and similar), tracked or untracked, are listed
   by name only and their content is never shown. That list is a name heuristic, not a detector:
   it catches common conventions, and every shown line still passes redaction and the never-send
   scan. The stored text is at most 256 KiB and leaves an oversized file out whole rather than
   cutting it mid-hunk; the packet then splits that text on line boundaries. An untracked listing
   over 8 MiB keeps the names that fit and says the untracked count is a lower bound. One 20-second
   deadline bounds the whole capture: each Git call, and the read of the global ignore setting, gets
   at most 10 seconds and never more than what is left. A deadline reached while the change is
   being read leaves the named files unshown and says so; one reached before the file list exists
   makes the capture unavailable. The effective-config check and the diffs are separate Git calls,
   so a filter written into the repository config between them would run; only a process of the
   same user can do that, and that user can already run code as itself, so this window is low
   severity.
5. **The same privacy path as every other excerpt.** The rendered text receives the capture-time
   redaction native observation content receives, with the same detector as the egress never-send
   scan, and is stored encrypted. Its parts are `repository_excerpt` case items, because this is
   the service's own repository read and not recorded evidence: the channel categories, the privacy
   gateway and the never-send scan apply to each one. A recipe that selects diff excerpts therefore
   requires `repository_excerpt`; an inference channel that does not allow it never receives a
   part, and the check reports `semantic_review_context_withheld`, as for any other category the
   recipe selects and the channel withholds. How a never-send match affects the review is owned by
   #920.
6. **A reserved share of the packet, first.** The change is admitted before every other excerpt,
   up to half of the recipe's excerpt count (at least one) and half of its excerpt bytes. Room it
   does not use stays with the other excerpts; parts left over after all of them backfill whatever
   the recipe still has free. Each part begins `[Yoetz check-time change, part i of n]`, its item
   ids sort ahead of every other item, and it links to the case's effective claims and obligations
   (else its latest plan). `_check_time_change_reservation` is the single seam for this share.
7. **Frozen with the job.** The object's pointer, or the fact that it was unavailable, is frozen
   into the job's semantic-case object. A recovered or resumed job reloads exactly that object and
   never re-reads a working tree that has since moved, so the case digest is unchanged. The object
   is check-scoped and not a ledger root.
8. **Disclosed limits.** `check_time_change_unavailable` (selected but nothing carried),
   `check_time_change_base_unavailable`, `check_time_change_truncated` (a file or part not shown)
   and `check_time_change_redacted` join the packet and check coverage. They describe AI-powered
   review input only, so they never weaken local absence proof. A check whose connection named no
   workspace has nothing to read and reports no check-time code; its review is the review of the
   time before this decision.
9. **AI-powered finding resolution compares the files each review was shown.** A completed review
   whose packet carried the change records, on its `check_recorded` 1.3.0 event, keyed commitments
   to the changed files it carried: `fully_shown` (the file's whole diff, unredacted and
   untruncated) and `partially_shown` (the rest it carried any of), each with `shown_bytes` (the
   bytes of the file's diff section that reached the packet, redaction markers included),
   `redactions` (the redacted spans among them), `section_admitted` (the whole section reached
   the packet, so only redaction made it partial) and `clean_bytes` (where the first shown
   redaction marker starts; `shown_bytes` without one). Each commitment is the task bundle's object
   commitment key over the change's base commit, the file's `diff --git` line and its change kind
   (binary, deleted), so no path is recorded, the same file under the task's fixed base commits the
   same way in every check of the task, and a file that turned binary or was deleted never stands
   in for the text diff it replaced. Only shown files count toward the 128-file bound; past it the
   record keeps the first 128 in change order and says it is incomplete. Replay folds, onto the
   finding's projection row, the record of every completed review that raised or re-raised an
   AI-powered finding (R): whole files are united, a file any of them saw whole must be seen whole,
   and a file they saw in part keeps the largest n (`shown_bytes`) and the smallest k
   (`redactions`). At most 64 contributing checks and 1024 files are kept on the finding's row
   (one event row still holds at most 128); past either bound R is unknown. A later repair
   review's `check_time_change_truncated`, `_redacted`, `_base_unavailable` and `_unavailable`
   codes are tolerated for that finding exactly when the repair saw at least what R requires.
   Every file R needs whole reached the repair whole. Every file R saw in part (n, k) reached the
   repair whole, or in part with (the repair showed at least n bytes, or its whole current
   section) and (it showed at most k redacted spans, or its first n bytes held no marker). Each arm
   is sound on its own: a repair that admitted its whole section saw the file's entire current
   diff apart from its redacted spans; a repair with at least n bytes saw at least as long a view;
   at most k spans hides no more than the raising review had hidden; and `clean_bytes` at least n
   means the repair saw the first n bytes with nothing hidden. So a view cut at the packet edge
   before a marker is covered by a longer repair whose first n bytes are clean, a whole section
   whose diff the fix shrank is covered while its redaction persists, and a new redaction or a
   shorter cut view still blocks. The repair's record may be incomplete, since each entry it holds
   is still true. An empty R (reviews that carried no change, including every review from before
   this decision) is always tolerated. R is also unknown, which never tolerates, when a raising
   record is incomplete or when a raising review carried parts without a readable record (0.3
   development builds). None of these codes is a capture
   baseline stamped on the finding. Redacting any contributing check makes R unknown and reopens a
   resolution that depended on it; redacting the resolving check reopens it as before. The
   relation is a pure fold over recorded checks, so the memory and SQLite ledgers replay it
   identically. Residual limits: the rule compares lengths and counts, not content. A redacted span
   that moved while the count stayed the same could hide the region the finding was about, and for
   a repair view cut at the packet edge (not the whole section) a hunk that moved past the m bytes
   it saw could too; a repair review that stays silent about either could clear the finding.
   Explicit `fixed` rulings (#905) are the long-term guard.

## Consequences

Every host and supported operating system gets the same capture, because it depends on the
repository, not on how the edit was made. The reviewer sees the actual change and the file list
it came from, within the existing per-item, count and total caps; #907 owns lifting those caps.

The capture is unavailable, and says so, for linked Git worktrees (whose `.git` is a file),
group- or world-writable roots, repositories whose effective config defines a filter or an include
(for example a repository-local Git LFS or git-crypt setup), partial clones, and Git older than
2.26.
Submodule changes appear as commit ids and binary files as a one-line description.

This decision does not add an MCP tool, a repository browser or an `ArtifactInspectionPort`, and
does not change ADR-011's content-withholding structural capture. No content-returning read exists
outside a check's AI-powered review composition.
