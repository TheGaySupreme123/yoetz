PRAGMA application_id = 0x594F4554;

CREATE TABLE observation_advice_history (
    id INTEGER PRIMARY KEY,
    workspace_commitment TEXT NOT NULL,
    subject_frontier TEXT NOT NULL,
    suppression_identity TEXT NOT NULL,
    snapshot_json BLOB NOT NULL,
    recorded_at TEXT NOT NULL
) STRICT;

CREATE INDEX observation_advice_history_by_frontier
    ON observation_advice_history (workspace_commitment, subject_frontier, id);

CREATE TABLE observation_verification_jobs (
    job_id TEXT PRIMARY KEY,
    workspace_commitment TEXT NOT NULL,
    subject_state_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'complete', 'stale', 'rejected')
    ),
    cursor_position INTEGER NOT NULL CHECK (cursor_position >= 0),
    enqueued_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT, WITHOUT ROWID;

CREATE INDEX observation_verification_jobs_by_workspace
    ON observation_verification_jobs (workspace_commitment, status);

CREATE TABLE observation_verification_results (
    id INTEGER PRIMARY KEY,
    job_id TEXT NOT NULL,
    workspace_commitment TEXT NOT NULL,
    check_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('passed', 'failed', 'rejected', 'unavailable', 'stale')
    ),
    subject_state_before TEXT NOT NULL,
    subject_state_after TEXT,
    result_commitment TEXT,
    limitations_json BLOB NOT NULL,
    stale INTEGER NOT NULL CHECK (stale IN (0, 1)),
    recorded_at TEXT NOT NULL
) STRICT;

CREATE INDEX observation_verification_results_by_job
    ON observation_verification_results (job_id, id);

CREATE TABLE observation_logical_identity (
    workspace_commitment TEXT NOT NULL,
    logical_identity TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    mapping_version TEXT NOT NULL,
    materialized_at TEXT NOT NULL,
    PRIMARY KEY (workspace_commitment, logical_identity)
) STRICT, WITHOUT ROWID;

PRAGMA user_version = 3;
