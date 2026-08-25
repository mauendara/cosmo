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
