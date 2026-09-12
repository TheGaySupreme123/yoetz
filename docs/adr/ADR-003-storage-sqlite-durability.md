# ADR-003 — Storage, SQLite build, and durability

**Status:** Working decision for spec drafting (2026-07-13). Ratification requires the full
fault/contention matrix on both advertised platforms.
**Implemented by:** `docs/INTERFACES.md`, `src/yoetz/adapters/sqlite/`,
`src/yoetz/ports/ledger.py`, `src/yoetz/ports/runtime.py`, root and packaged
`migrations/`, and the storage suites under `tests/integration/storage/` and `tests/subprocess/`.

## Decisions

1. **SQLite delivery:** APSW pinned exactly `3.53.3.1` carrying SQLite `3.53.3` amalgamation.
   Startup verifies `apsw.apsw_version()`, `apsw.sqlite_lib_version()`, `using_amalgamation`,
   exact `sqlite_source_id()`
   (`2026-06-26 20:14:12 d4c0e51e4aeb96955b99185ab9cde75c339e2c29c3f3f12428d364a10d782c62`),
   and support-manifest compile options. Unknown builds: read-only inspection allowed, writes
   fail closed (`STORAGE_UNSAFE`). Multi-connection WAL floor: 3.51.3+ (WAL-reset fix). The
   stdlib `sqlite3` adapter is test/reference only.
2. **PRAGMAs / connection contract:** WAL (verified, not assumed),
   `synchronous=FULL` (verified), `foreign_keys=ON`, `trusted_schema=OFF`, `temp_store=MEMORY`,
   `busy_timeout=5000`, `wal_autocheckpoint=0` (owner-run bounded PASSIVE checkpoints),
   `mmap_size=0`, extension loading disabled. `PRAGMA application_id = 0x594F4554` ("YOET"),
   current `user_version` from the ordered catalog and bundle migration registries.
   Runtime authorizers permit the read-only `table_info(observation_consent)` schema probe on
   writer and inspection connections so structural-only and native-content consent remain
   distinguishable. Other table arguments and configuration-changing PRAGMAs remain denied
   unless independently listed in the connection policy. Observation tests must exercise the
   production authorizer, including supported older consent schemas (issue #616).
3. **Transactions:** `BEGIN IMMEDIATE` for every write path; the append transaction contains only
   bounded indexed reads/writes. All hashing, validation, encryption, object
   fsync, and network work happens outside. Acknowledge only after COMMIT returns.
4. **Layout:** platform app-data `…/yoetz/` with `catalog.sqlite3` + `tasks/<task-id>/` bundles.
   One task per bundle database. Owner-only permissions; symlink/hardlink/
   traversal rejection; repo, cloud-synced, network, and world-readable paths unsupported and
   detected where practical (`STORAGE_UNSAFE`).
5. **Schema:** migration `0001` is the canonical initial schema for both
   catalog and bundle (STRICT tables, WITHOUT ROWID where keyed by text, CHECK-enforced state
   machines). Structural columns never contain user plaintext.
6. **Object publication protocol:** encrypted temp file → flush → fsync(file) → atomic rename
   into `objects/<2-hex-prefix>/` → fsync(dir) → only then referenced inside the append
   transaction. Native Claude Code and Cursor content, and source-qualified Codex hook content,
   use the same service-side publication protocol before their structural observation enters the
   FIFO ledger: the service reserves a metadata-only capture ticket, writes the encrypted object
   and manifest, and marks the ticket pending only after the expected set is complete. The Codex
   hook arm is profileless: it is selected by the exact `codex_hook` source and active observation
   consent authority, not by a Claude/Cursor content profile. Codex session-stream content remains
   on its separate path. Orphans are
   collectable after a 24 h safety window, never while referenced by a maintenance pin or an
   outstanding capture ticket.
7. **Backup/restore/migration:** online Backup API only (APSW destination-side `backup`);
   frontier-pinned manifests; restore into a quarantined new bundle then atomic catalog switch;
   canonical event bytes never rewritten by migration; newer unknown write-schema fails closed.
8. **Corruption response:** integrity failure quarantines the bundle (writes disabled,
   `STORAGE_CORRUPT`), preserves originals under `quarantine/`, and directs to
   backup/restore. Projection-only corruption is repaired by generation replay. A deterministic
   failure to rehydrate one pending operation's resume pointer is not bundle integrity failure:
   recovery quarantines that operation as `operation_resume_object_invalid` and keeps the ledger
   live (issue #443). Chain, writer, and object-inventory integrity failures remain
   bundle-terminal. Recovery of an
   existing bundle must remain cooperative with the trusted local service: decoding yields in
   bounded batches and the pure CPU replay reducer runs outside the service event loop, so one
   large or corrupt task cannot monopolize ordinary control. An observation route that encounters
   `STORAGE_CORRUPT` does not reinterpret the bundle as healthy or retry indefinitely; ADR-010's
   terminal observation quarantine contains that delivery lane while preserving the original
   storage recovery contract.

## Amendment — task lineage and project metadata (2026-09-05, issue #494 / ADR-027)

Decision 4 is unchanged: the on-disk layout remains `catalog.sqlite3` + `tasks/<task-id>/`, and
one task still owns one bundle. Lineage (`parent_task_id`, depth, lineage digest, origin,
acceptance, and work state) and project membership are catalog metadata, not a second ledger
inside the parent bundle. A child is a separate bundle. Clients never open sibling or child
bundles (ADR-008). The #498 ownership inventory decides which workspace-keyed stores are
task-owned provenance and which are shared mutable coordination state. Expected shared-mutable
candidates are migration-0004 workspace-to-session routing and the migration-0003 verification-job
scheduling authority (including the per-workspace running-job uniqueness); only inventory-designated
state may move under ADR-027 / issue #496. Job results, inspection snapshots, and session advice
remain task-owned unless that inventory proves otherwise. This decision introduces no shared
writable ledger.

9. **Native capture handoff:** Claude Code and Cursor ordinary native profiles, and the
   source-qualified profileless Codex hook arm, have a bounded service-side staging lane. A
   capture-only request crosses the authenticated local-control boundary, secret-scans and
   encrypts each eligible chunk, publishes its object and manifest with the object protocol above,
   and records a ticket containing only commitments, encrypted object IDs, source/session/cursor
   identity, the original source and content-authority generations, profile when applicable, and
   expected content groups/parts. For Codex, the ticket is fenced to the exact active consent
   authority/generation, task, workspace commitment, Yoetz and host session, `codex_hook` source
   identity and commitment, source generation, tool-call correlation, expected multipart
   groups/parts, object kinds, and object/content digests. No Codex content profile is inferred or
   accepted. The
   structural observation request may advance the FIFO ledger only after it revalidates the source
   and authority generations and the complete expected group/part set; partial, conflicting, or
   unreadable sets remain unavailable and are never promoted by inference.

   For the Codex hook arm, only explicitly linked tool output, selected changed-file/code bytes,
   and workspace-diff bytes are eligible for captured-content evidence and semantic selection.
   Session-stream records remain outside this native ticket lane and are excluded from semantic
   selection. Tool input and path/locator content are excluded from semantic selection too, though
   the current Codex hook path may still stage consented input/locator chunks in the bounded
   encrypted local capture lane pending a follow-up staging filter. Encrypted staging does not
   authorize disclosure: semantic-case selection still requires the effective repository privacy
   authority and the independently authorized provider/attempt route.

   `staging` and `pending` tickets are bounded to 512 outstanding entries per workspace.
   Revoked tickets do not consume that quota, but their metadata-only tombstones remain so an
   old ticket cannot be replayed after a pause, revoke, disable, or re-enable ABA cycle. A
   successful structural append removes its consumed ticket. A new check/frozen-case acquisition
   sees an outstanding ticket under the same bundle transaction and returns retryable
   `OPERATION_PENDING`; retrying the same request reuses the ticket identity and encrypted
   manifests rather than creating another capture or ledger append. The capture-only lane has its
   own bounded lock and may stage while a heavy structural append is running; object and manifest
   writes remain serialized and bounded.

   When a new CHECK encounters the capture barrier, READY reconciles a bounded listing for the
   exact routed task against current local content authority before one freeze retry. It tombstones
   only tickets whose authority is absent, inactive, revoked, runtime-disabled, profile-unselected,
   or from an old authority generation, so a direct capture-only request with no structural outbox
   row cannot leave a permanent barrier. Matching active tickets remain retryable; a completed
   same-request replay returns without inspecting newer tickets, and encrypted objects and
   captured history are unchanged.

   This handoff is a local durability boundary, not an offline guarantee. It contains no
   plaintext spool. A host kill or service failure before authenticated staging completes may
   leave an honest content gap; once the encrypted ticket is committed, service restart and a
   retry may resume it without re-reading plaintext from a local spool.

## Amendment — compatible bundle upgrade at the READY boundary (2026-09-12)

The package update path has one narrowly bounded automatic schema transition. After the catalog is
current and before the new service generation is composed as READY, the service may invoke
`BundleUpgradeCoordinator.run_before_ready` with catalog-derived `BundleUpgradeTarget` values. It
must acquire the exclusive-holder callback before touching a stale bundle; an ordinary lazy
`open_writer()` remains fail-closed on `migration_required` and never upgrades a task as a side
effect of opening it.

The restart-discoverable marker is the existing catalog `maintenance_operations` row, not a new
marker table. `SqliteBundleUpgradeJournal` reserves `kind = 'migration'` with
`requested_target_version = '13'`, a generated `request_id`, `migration_request_digest()` and
`migration_plan_digest()`. The plan binds the task, route identity/generation, subject frontier,
privacy-root generation/digest, and migration ID `0013`. A pending row owns a lease and advances
only through `reserved → backup_ready → schema_applied → replay_verified`; terminal rows carry the
canonical `MigrationResult`, its backup manifest digest, and either `complete` or `quarantined`
state. A restarted service inspects that row and the bundle schema, then resumes or refuses the
same operation; it never creates a second migration for the same target.

The supported automatic pair is released bundle schema `12` to candidate schema `13`, applying
only migration `0013`. The migration preserves released bundle migrations `0010`, `0011`, and
`0012`, and validates the consent-profile column plus events layout before allowing a legacy v10
path to continue. A v10 layout that cannot be identified as the released shape fails as
`schema_upgrade_path_unknown` before mutation; newer, missing, unsafe, or non-contiguous schemas
fail closed as well.

For every candidate, `BundleUpgradeEffects.ensure_machine_backup` creates or reopens the same
machine-bound, frontier-pinned backup before DDL. The migration writer is the narrow
`_open_bundle_migration_writer()` capability; it is closed before the normal verified writer is
reopened. `capture_sqlite_integrity()` and the injected `verify_replay()` compare task identity,
canonical event history/count, frontier, object inventory, preserved metadata/table digests, and
the deterministic projection replay digest. The resulting `MigrationResult` requires an unchanged
frontier before the journal is terminalized.

A failure before a proved schema commit can retry when the old schema is intact. A post-commit,
reopen, verification, or journal uncertainty preserves the backup and target and quarantines the
maintenance operation as `rollback_required`; READY stays blocked until the supported restore or
rollback procedure establishes a verified route. This path never performs reverse SQL, rewrites
canonical event bytes, or treats package replacement as data-upgrade completion.

The package-update authorization is schema-only. It preserves task bundles, settings, permissions,
host integrations, object roots, observation consent, event bytes, and frontiers; it adds no
observation, content, provider, disclosure, or egress authority. Explicit backup, restore, and
ad-hoc `migrate execute` continue to use their own exact review and plan-digest contract.

## Consequences

Platform wheels (not pure-Python) on macOS arm64 + manylinux_2_28 x86_64; Yoetz owns security
patching for the shipped SQLite; every dependency bump reruns the storage matrix.
