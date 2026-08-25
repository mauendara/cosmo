"""Forward-only schema migrations (Open Item 5).

There is no down-migration and a shipped migration's SQL is frozen -- a later
change is always a new migration with a higher version, appended to
`MIGRATIONS`. This is what spec 9.1 means by letting the event table "migrate
without a backfill archaeology project"; the same discipline covers the whole
schema, not just `events`.

Each migration's script supplies its own `BEGIN`/`COMMIT` (required by
`sqlite3.Connection.executescript`, which otherwise gives no transactional
guarantee across statements) so that a crash mid-migration never leaves the
schema half-created with no `schema_migrations` row to say so.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    description: str
    sql: str


# ============================================================================
# Migration 1 -- initial schema.
#
# Split along the spec 8 discipline: append-only history tables that are
# never UPDATEd, and current-state tables that are UPSERTed, one row per
# entity rather than one row per tick (spec 8: "avoids flooding the DB from
# high-frequency file-watching").
# ============================================================================
_SCHEMA_V1 = """
-- ---------------------------------------------------------------------
-- Append-only: events (spec 9.1 common envelope).
-- ---------------------------------------------------------------------
CREATE TABLE events (
    event_id       TEXT PRIMARY KEY,
    run_id         TEXT,
    task_id        TEXT,
    timestamp      TEXT NOT NULL,
    sequence       INTEGER NOT NULL,
    event_type     TEXT NOT NULL,
    severity       TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    schema_version INTEGER NOT NULL,
    payload        TEXT NOT NULL
);
CREATE INDEX idx_events_run_sequence ON events(run_id, sequence);
CREATE INDEX idx_events_task ON events(task_id);
CREATE INDEX idx_events_type ON events(event_type);

-- Backing counter for `sequence`: one row per scope (the run_id, or '' for
-- run-less project-level events such as agent_assets.synced). Bumped in the
-- same transaction as the events row it numbers -- spec 9.1: "sequence is
-- written transactionally with the event so ordering survives a crash."
CREATE TABLE event_sequence (
    scope      TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL
);

-- ---------------------------------------------------------------------
-- Current-state: projects (spec 10.4 step 6).
--
-- `cosmo init` (Phase 4) is the real bootstrap flow; this table and a
-- minimal `cosmo project register` exist now only so the project tier of
-- harness resolution (spec 2, plan Phase 0) has something to resolve
-- against.
-- ---------------------------------------------------------------------
CREATE TABLE projects (
    project_id       TEXT PRIMARY KEY,
    target_path      TEXT NOT NULL UNIQUE,
    harness          TEXT NOT NULL,
    project_template TEXT,
    initialized_at   TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Current-state: task_queue (spec 5, all listed columns).
-- ---------------------------------------------------------------------
CREATE TABLE task_queue (
    task_id          TEXT PRIMARY KEY,
    spec_path        TEXT NOT NULL,
    depends_on       TEXT NOT NULL DEFAULT '[]',
    priority         INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL CHECK (status IN (
                         'queued', 'proposing', 'proposed', 'implementing',
                         'validating', 'committing', 'merging', 'done',
                         'failed_retry', 'blocked'
                     )),
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL,
    last_error       TEXT,
    blocked_reason   TEXT CHECK (blocked_reason IN (
                         'code_failure', 'cost', 'merge_conflict', 'environment',
                         'timeout', 'flaky_unresolved'
                     )),
    allow_test_edits INTEGER NOT NULL DEFAULT 0 CHECK (allow_test_edits IN (0, 1)),
    worktree_path    TEXT,
    session_id       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Current-state: task_progress, task_heartbeat (spec 4, 9.2 -- UPSERT,
-- one row per task).
-- ---------------------------------------------------------------------
CREATE TABLE task_progress (
    task_id    TEXT PRIMARY KEY REFERENCES task_queue(task_id),
    completed  INTEGER NOT NULL,
    total      INTEGER NOT NULL,
    last_label TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE task_heartbeat (
    task_id           TEXT PRIMARY KEY REFERENCES task_queue(task_id),
    state              TEXT NOT NULL,
    state_entered_at   TEXT NOT NULL,
    last_activity_at   TEXT NOT NULL,
    source             TEXT NOT NULL CHECK (source IN ('stream', 'file', 'mtime')),
    updated_at         TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Current-state: run_state, run_cost, task_cost (spec 3.1, 7.3 -- UPSERT).
-- ---------------------------------------------------------------------
CREATE TABLE run_state (
    run_id          TEXT PRIMARY KEY,
    status          TEXT NOT NULL CHECK (status IN ('idle', 'running', 'paused', 'stopped')),
    harness         TEXT NOT NULL,
    permission_mode TEXT NOT NULL,
    max_turns       INTEGER NOT NULL,
    base_branch     TEXT NOT NULL,
    pause_reason    TEXT CHECK (pause_reason IN (
                        'circuit_breaker', 'quota_exhausted_5h', 'quota_exhausted_weekly'
                    )),
    stop_reason     TEXT CHECK (stop_reason IN (
                        'completed', 'max_time', 'queue_empty', 'cost_limit_reached',
                        'manual', 'quota_exhausted_weekly'
                    )),
    started_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    stopped_at      TEXT
);

CREATE TABLE run_cost (
    run_id         TEXT PRIMARY KEY REFERENCES run_state(run_id),
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    updated_at     TEXT NOT NULL
);

CREATE TABLE task_cost (
    task_id        TEXT PRIMARY KEY REFERENCES task_queue(task_id),
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    updated_at     TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Append-only: task_transitions, task_failures (spec 8 historical trail).
-- ---------------------------------------------------------------------
CREATE TABLE task_transitions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        TEXT NOT NULL REFERENCES task_queue(task_id),
    run_id         TEXT REFERENCES run_state(run_id),
    from_state     TEXT,
    to_state       TEXT NOT NULL,
    attempt_number INTEGER NOT NULL,
    timestamp      TEXT NOT NULL,
    event_id       TEXT REFERENCES events(event_id)
);
CREATE INDEX idx_task_transitions_task ON task_transitions(task_id);

CREATE TABLE task_failures (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        TEXT NOT NULL REFERENCES task_queue(task_id),
    run_id         TEXT REFERENCES run_state(run_id),
    attempt_number INTEGER NOT NULL,
    failure_type   TEXT NOT NULL CHECK (failure_type IN (
                       'code_error', 'environment_error', 'timeout', 'flaky'
                   )),
    failure_stage  TEXT NOT NULL CHECK (failure_stage IN (
                       'propose', 'implement', 'build', 'unit_tests', 'e2e_tests',
                       'test_integrity', 'commit', 'merge'
                   )),
    error_summary  TEXT NOT NULL,
    error_detail   TEXT,
    files_touched  TEXT NOT NULL DEFAULT '[]',
    will_retry     INTEGER NOT NULL CHECK (will_retry IN (0, 1)),
    next_action    TEXT NOT NULL CHECK (next_action IN (
                       'retry', 'block', 'escalate_circuit_breaker'
                   )),
    timestamp      TEXT NOT NULL,
    event_id       TEXT REFERENCES events(event_id)
);
CREATE INDEX idx_task_failures_task ON task_failures(task_id);
"""

MIGRATIONS: list[Migration] = [
    Migration(1, "initial schema: events, queue, progress, run state, cost, history", _SCHEMA_V1),
]


def current_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    version = row[0]
    return int(version) if version is not None else 0


def latest_version() -> int:
    return max(m.version for m in MIGRATIONS)


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply every migration newer than the schema's current version, in
    order, each as its own transaction. Returns the versions applied."""
    applied: list[int] = []
    current = current_version(conn)
    for migration in sorted(MIGRATIONS, key=lambda m: m.version):
        if migration.version <= current:
            continue
        stamp = (
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "    version INTEGER PRIMARY KEY,"
            "    description TEXT NOT NULL,"
            "    applied_at TEXT NOT NULL"
            ");\n"
            f"INSERT INTO schema_migrations(version, description, applied_at) "
            f"VALUES ({migration.version}, '{migration.description}', "
            f"strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));"
        )
        conn.executescript(f"BEGIN;\n{migration.sql}\n{stamp}\nCOMMIT;")
        applied.append(migration.version)
    return applied
