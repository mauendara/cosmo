"""Forward-only migrations and pragmas (spec 8, 8.1, Open Item 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cosmo.store.connection import connect_reader, connect_writer
from cosmo.store.migrations import MIGRATIONS, current_version, latest_version, migrate


def test_fresh_database_starts_at_version_zero(tmp_path: Path) -> None:
    conn = connect_writer(tmp_path / "cosmo.db")
    assert current_version(conn) == 0
    conn.close()


def test_migrate_applies_every_migration_once(tmp_path: Path) -> None:
    conn = connect_writer(tmp_path / "cosmo.db")
    applied = migrate(conn)
    assert applied == [m.version for m in MIGRATIONS]
    assert current_version(conn) == latest_version()
    conn.close()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    conn = connect_writer(tmp_path / "cosmo.db")
    migrate(conn)
    second_pass = migrate(conn)
    assert second_pass == []
    conn.close()


def test_all_tables_from_the_spec_exist_after_migration(tmp_path: Path) -> None:
    conn = connect_writer(tmp_path / "cosmo.db")
    migrate(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    expected = {
        "schema_migrations",
        "events",
        "event_sequence",
        "projects",
        "task_queue",
        "task_progress",
        "task_heartbeat",
        "run_state",
        "run_cost",
        "task_cost",
        "task_transitions",
        "task_failures",
    }
    assert expected <= tables
    conn.close()


def test_required_pragmas_are_applied_on_every_connection(tmp_path: Path) -> None:
    """Spec 8.1's exact pragma set, applied on every connection, not once at
    creation time."""
    db_path = tmp_path / "cosmo.db"
    writer = connect_writer(db_path)
    migrate(writer)
    writer.close()

    for conn in (connect_writer(db_path), connect_reader(db_path)):
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.close()


def test_reader_connection_cannot_write(tmp_path: Path) -> None:
    """Spec 8's single-writer discipline, enforced at the SQLite level: a
    `mode=ro` connection physically cannot become a second writer."""
    db_path = tmp_path / "cosmo.db"
    writer = connect_writer(db_path)
    migrate(writer)
    writer.close()

    reader = connect_reader(db_path)
    with pytest.raises(Exception, match="readonly|read-only"):
        reader.execute("INSERT INTO event_sequence (scope, next_value) VALUES ('x', 1)")
    reader.close()


def test_migration_descriptions_are_free_of_single_quotes() -> None:
    """Descriptions are spliced into a literal SQL INSERT (see migrate()); an
    apostrophe would break the statement rather than merely look odd."""
    for m in MIGRATIONS:
        assert "'" not in m.description


def test_migration_3_preserves_existing_run_state_rows_and_accepts_disk_low(
    tmp_path: Path,
) -> None:
    """Migration 3 (Phase 9) recreate-copy-swaps `run_state` to widen its
    `stop_reason` CHECK constraint -- verify a pre-existing row survives the
    swap (not just that the migration runs), and that the new value is
    actually accepted afterward."""
    db_path = tmp_path / "cosmo.db"
    conn = connect_writer(db_path)
    # Apply only migration 1 (stamped, so `migrate()` below treats this as
    # a real database that predates migration 3, not an unmigrated one).
    migration_1 = next(m for m in MIGRATIONS if m.version == 1)
    conn.executescript(
        f"BEGIN;\n{migration_1.sql}\n"
        "CREATE TABLE schema_migrations ("
        "    version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL"
        ");\n"
        "INSERT INTO schema_migrations VALUES (1, 'initial', 't0');\n"
        "COMMIT;"
    )
    conn.execute(
        """
        INSERT INTO run_state (
            run_id, status, harness, permission_mode, max_turns, base_branch,
            started_at, updated_at
        ) VALUES ('run-1', 'stopped', 'claude', 'dontAsk', 80, 'develop', 't0', 't0')
        """
    )
    conn.commit()

    applied = migrate(conn)
    assert 3 in applied

    row = conn.execute("SELECT run_id, status FROM run_state WHERE run_id = 'run-1'").fetchone()
    assert row is not None
    assert row[1] == "stopped"

    conn.execute("UPDATE run_state SET stop_reason = 'disk_low' WHERE run_id = 'run-1'")
    conn.commit()
    reread = conn.execute("SELECT stop_reason FROM run_state WHERE run_id = 'run-1'").fetchone()
    assert reread[0] == "disk_low"
    conn.close()


def test_migration_4_preserves_existing_task_queue_rows_and_accepts_new_states(
    tmp_path: Path,
) -> None:
    """v4 workflow changes: migration 4 recreate-copy-swaps `task_queue` to
    widen its `status` CHECK constraint (a gap the plan document itself
    didn't name -- see `docs/v3-implementation-state.md`'s v4 section) --
    verify a pre-existing row survives the swap, and that `reviewing`/
    `finishing` are actually accepted afterward."""
    db_path = tmp_path / "cosmo.db"
    conn = connect_writer(db_path)
    migration_1 = next(m for m in MIGRATIONS if m.version == 1)
    conn.executescript(
        f"BEGIN;\n{migration_1.sql}\n"
        "CREATE TABLE schema_migrations ("
        "    version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL"
        ");\n"
        "INSERT INTO schema_migrations VALUES (1, 'initial', 't0');\n"
        "COMMIT;"
    )
    conn.execute(
        """
        INSERT INTO task_queue (
            task_id, spec_path, status, attempt_count, max_attempts, created_at, updated_at
        ) VALUES ('t1', 'openspec/changes/t1', 'queued', 0, 2, 't0', 't0')
        """
    )
    conn.commit()

    applied = migrate(conn)
    assert 4 in applied

    row = conn.execute("SELECT task_id, status FROM task_queue WHERE task_id = 't1'").fetchone()
    assert row is not None
    assert row[1] == "queued"

    for status in ("reviewing", "finishing"):
        conn.execute("UPDATE task_queue SET status = ? WHERE task_id = 't1'", (status,))
        conn.commit()
        reread = conn.execute("SELECT status FROM task_queue WHERE task_id = 't1'").fetchone()
        assert reread[0] == status
    conn.close()


def test_migration_5_accepts_adversarial_review_failure_stage(tmp_path: Path) -> None:
    """v4 workflow changes: same recreate-copy-swap recipe as migration 2,
    for `task_failures.failure_stage`."""
    conn = connect_writer(tmp_path / "cosmo.db")
    migrate(conn)
    conn.execute(
        """
        INSERT INTO task_queue (
            task_id, spec_path, status, attempt_count, max_attempts, created_at, updated_at
        ) VALUES ('t1', 'openspec/changes/t1', 'queued', 0, 2, 't0', 't0')
        """
    )
    conn.execute(
        """
        INSERT INTO task_failures (
            task_id, attempt_number, failure_type, failure_stage, error_summary,
            will_retry, next_action, timestamp
        ) VALUES ('t1', 0, 'code_error', 'adversarial_review', 'rejected', 1, 'retry', 't0')
        """
    )
    conn.commit()
    row = conn.execute("SELECT failure_stage FROM task_failures WHERE task_id = 't1'").fetchone()
    assert row[0] == "adversarial_review"
    conn.close()


def test_migration_6_adds_spec_batch_id_defaulting_to_null(tmp_path: Path) -> None:
    conn = connect_writer(tmp_path / "cosmo.db")
    migrate(conn)
    conn.execute(
        """
        INSERT INTO task_queue (
            task_id, spec_path, status, attempt_count, max_attempts, created_at, updated_at
        ) VALUES ('t1', 'docs/specs/demo-spec/tasks/backend-task.md', 'queued', 0, 2, 't0', 't0')
        """
    )
    conn.commit()
    row = conn.execute("SELECT spec_batch_id FROM task_queue WHERE task_id = 't1'").fetchone()
    assert row[0] is None

    conn.execute("UPDATE task_queue SET spec_batch_id = 'demo-spec' WHERE task_id = 't1'")
    conn.commit()
    reread = conn.execute("SELECT spec_batch_id FROM task_queue WHERE task_id = 't1'").fetchone()
    assert reread[0] == "demo-spec"
    conn.close()
