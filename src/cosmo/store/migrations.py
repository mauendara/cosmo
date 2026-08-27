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

# ============================================================================
# Migration 2 -- Phase 6: `task_failures.failure_stage` gains 'secrets'.
#
# Spec 9.3 enumerates `failure_stage` without a value for the gate-side
# `gitleaks` backstop (spec 6.1) -- a secret reaching the diff is not a
# test-integrity violation, so it needs its own attribution rather than
# overloading `test_integrity` (see `store.enums.FailureStage.SECRETS`'s
# docstring, and deviation #12 in `docs/v3-implementation-state.md`).
# SQLite has no `ALTER TABLE ... DROP CONSTRAINT`, so a CHECK-constraint
# change means recreate-copy-swap, same recipe SQLite's own docs recommend;
# safe here because `task_failures` has never had a real writer until this
# phase, but written as a genuine copy (not a blind DROP+CREATE) so this
# migration is correct even after real rows exist.
# ============================================================================
_SCHEMA_V2 = """
CREATE TABLE task_failures_v2 (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        TEXT NOT NULL REFERENCES task_queue(task_id),
    run_id         TEXT REFERENCES run_state(run_id),
    attempt_number INTEGER NOT NULL,
    failure_type   TEXT NOT NULL CHECK (failure_type IN (
                       'code_error', 'environment_error', 'timeout', 'flaky'
                   )),
    failure_stage  TEXT NOT NULL CHECK (failure_stage IN (
                       'propose', 'implement', 'build', 'unit_tests', 'e2e_tests',
                       'test_integrity', 'secrets', 'commit', 'merge'
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
INSERT INTO task_failures_v2 SELECT * FROM task_failures;
DROP TABLE task_failures;
ALTER TABLE task_failures_v2 RENAME TO task_failures;
CREATE INDEX idx_task_failures_task ON task_failures(task_id);
"""

# ============================================================================
# Migration 3 -- Phase 9: `run_state.stop_reason` gains 'disk_low'.
#
# Spec 9.5's pre-run disk check (`run.loop.run_queue`, wired this phase)
# aborts a run before its first task rather than let a full disk fail every
# task in a way that reads as a code error. That abort is a real
# `stop_reason`, not an overload of an existing value (`manual` is already
# reused for the DAG-cycle-at-startup case; `disk_low` is distinct so a
# later query can tell the two apart) -- same recreate-copy-swap recipe as
# migration 2, since SQLite has no `ALTER TABLE ... DROP CONSTRAINT`. Safe
# to drop `run_state` mid-transaction despite `run_cost`/`task_cost`/
# `task_transitions`/`task_failures` all holding FKs to it: SQLite only
# checks a foreign key against the *current* schema when a child row is
# inserted/updated, never at the parent's DDL time, and no child rows are
# touched here.
# ============================================================================
_SCHEMA_V3 = """
CREATE TABLE run_state_v2 (
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
                        'manual', 'quota_exhausted_weekly', 'disk_low'
                    )),
    started_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    stopped_at      TEXT
);
INSERT INTO run_state_v2 SELECT * FROM run_state;
DROP TABLE run_state;
ALTER TABLE run_state_v2 RENAME TO run_state;
"""

# ============================================================================
# Migration 4 -- v4 workflow changes: `task_queue.status` gains 'reviewing'
# and 'finishing'.
#
# `docs/v4-changes-to-workflow-plan.md` inserts two new task-machine states
# (`REVIEWING` between `VALIDATING`/`COMMITTING`, `FINISHING` between
# `MERGING`/`DONE`) but its own migration section only named the additive
# `spec_batch_id` column -- it did not account for `task_queue.status`'s own
# CHECK constraint, which would otherwise reject a genuine `queue_transition`
# call for either new state before this migration ever ran. Found while
# implementing the plan, not in the plan document itself; recorded as a
# deviation in `docs/v3-implementation-state.md`. Same recreate-copy-swap
# recipe as migrations 2/3 -- SQLite has no `ALTER TABLE ... DROP
# CONSTRAINT`. Safe despite `task_progress`/`task_heartbeat`/`task_cost`/
# `task_transitions`/`task_failures` all holding FKs to `task_queue`: SQLite
# only checks a foreign key against the *current* schema when a child row is
# inserted/updated, never at the parent's DDL time (same reasoning migration
# 3's own comment already gives for `run_state`).
# ============================================================================
_SCHEMA_V4 = """
CREATE TABLE task_queue_v2 (
    task_id          TEXT PRIMARY KEY,
    spec_path        TEXT NOT NULL,
    depends_on       TEXT NOT NULL DEFAULT '[]',
    priority         INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL CHECK (status IN (
                         'queued', 'proposing', 'proposed', 'implementing',
                         'validating', 'reviewing', 'committing', 'merging',
                         'finishing', 'done', 'failed_retry', 'blocked'
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
INSERT INTO task_queue_v2 SELECT * FROM task_queue;
DROP TABLE task_queue;
ALTER TABLE task_queue_v2 RENAME TO task_queue;
"""

# ============================================================================
# Migration 5 -- v4 workflow changes: `task_failures.failure_stage` gains
# 'adversarial_review'.
#
# The new `REVIEWING` state (see migration 4's comment) attributes a
# rejected review, or a review harness call that itself failed, to
# `FailureStage.ADVERSARIAL_REVIEW` (`store.enums`) -- not `TEST_INTEGRITY`,
# which is the gate's own diff-gate finding, a different check entirely.
# Same recreate-copy-swap recipe as migration 2, which already did exactly
# this for `SECRETS`.
# ============================================================================
_SCHEMA_V5 = """
CREATE TABLE task_failures_v3 (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        TEXT NOT NULL REFERENCES task_queue(task_id),
    run_id         TEXT REFERENCES run_state(run_id),
    attempt_number INTEGER NOT NULL,
    failure_type   TEXT NOT NULL CHECK (failure_type IN (
                       'code_error', 'environment_error', 'timeout', 'flaky'
                   )),
    failure_stage  TEXT NOT NULL CHECK (failure_stage IN (
                       'propose', 'implement', 'build', 'unit_tests', 'e2e_tests',
                       'test_integrity', 'secrets', 'adversarial_review', 'commit', 'merge'
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
INSERT INTO task_failures_v3 SELECT * FROM task_failures;
DROP TABLE task_failures;
ALTER TABLE task_failures_v3 RENAME TO task_failures;
CREATE INDEX idx_task_failures_task ON task_failures(task_id);
"""

# ============================================================================
# Migration 6 -- v4 workflow changes: `task_queue` gains `spec_batch_id`.
#
# Plain nullable column, no CHECK constraint -- `ALTER TABLE ... ADD COLUMN`
# is enough, no recreate-copy-swap needed (unlike migrations 4/5 above,
# which had to touch this same table for a CHECK-constraint reason). Lets
# `cosmo report`/a future `cosmo spec status` group every task a `cosmo spec
# queue <name>` call inserted together -- the batch id is just the spec's
# own name (`<name>-spec`), no separate opaque id to invent.
# ============================================================================
_SCHEMA_V6 = """
ALTER TABLE task_queue ADD COLUMN spec_batch_id TEXT;
"""

# ============================================================================
# Migration 7 -- v5 improvements plan part 1: `run_state.stop_reason` gains
# 'crashed'.
#
# The startup reconciliation sweep (`run.recovery.reconcile_interrupted_
# tasks`) transitions any `run_state` row still `running` at process start
# to `stopped`/`crashed` -- under Cosmo's strictly serial, single-process
# design (spec 5), a `running` row that outlives its own process can only
# mean that process died. Same recreate-copy-swap recipe as migrations 3/4,
# since SQLite has no `ALTER TABLE ... DROP CONSTRAINT`.
# ============================================================================
_SCHEMA_V7 = """
CREATE TABLE run_state_v3 (
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
                        'manual', 'quota_exhausted_weekly', 'disk_low', 'crashed'
                    )),
    started_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    stopped_at      TEXT
);
INSERT INTO run_state_v3 SELECT * FROM run_state;
DROP TABLE run_state;
ALTER TABLE run_state_v3 RENAME TO run_state;
"""

# ============================================================================
# Migration 8 -- v5 improvements plan part 5 (Class 1): `task_failures` gains
# `failure_signature`.
#
# Plain nullable column, no CHECK constraint -- populated by a small
# deterministic classifier (`store.failure_signature.
# classify_failure_signature`), not a closed enumeration, and anything
# unmatched is `None` on purpose. `ALTER TABLE ... ADD COLUMN` is enough,
# same as migration 6's `spec_batch_id`.
# ============================================================================
_SCHEMA_V8 = """
ALTER TABLE task_failures ADD COLUMN failure_signature TEXT;
"""

MIGRATIONS: list[Migration] = [
    Migration(1, "initial schema: events, queue, progress, run state, cost, history", _SCHEMA_V1),
    Migration(2, "task_failures.failure_stage gains secrets (gate gitleaks backstop)", _SCHEMA_V2),
    Migration(3, "run_state.stop_reason gains disk_low (pre-run disk check)", _SCHEMA_V3),
    Migration(4, "task_queue.status gains reviewing, finishing (v4 workflow changes)", _SCHEMA_V4),
    Migration(
        5,
        "task_failures.failure_stage gains adversarial_review (v4 workflow changes)",
        _SCHEMA_V5,
    ),
    Migration(
        6, "task_queue gains spec_batch_id (v4 workflow changes, cosmo spec queue)", _SCHEMA_V6
    ),
    Migration(
        7,
        "run_state.stop_reason gains crashed (v5 improvements, startup reconciliation)",
        _SCHEMA_V7,
    ),
    Migration(
        8,
        "task_failures gains failure_signature (v5 improvements, part 5 Class 1)",
        _SCHEMA_V8,
    ),
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
