"""SQLite connection factory and required pragmas (spec 8, 8.1).

Every connection, reader or writer, gets the same pragmas -- WAL is what lets
a read-only connection run concurrently with the one write connection the
main loop owns.

`connect_writer` is the only function anywhere in this package that opens a
connection capable of writing. `store/writer.py` and `store/migrations.py`
are its only legitimate callers; `tests/test_store_boundary.py` enforces this
so that a future watcher or stream-reader module (Phase 2/3) reaches for
`StoreWriter.submit()` instead of a second write connection (spec 8's
single-writer discipline).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Spec 8.1, applied on every connection, not once at creation.
_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA busy_timeout = 10000",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    conn.row_factory = sqlite3.Row


def connect_writer(db_path: Path) -> sqlite3.Connection:
    """Open the single read-write connection.

    `sqlite3`'s own `check_same_thread` guard is left on: this connection must
    stay on the thread that opened it, which is exactly what single-writer
    discipline requires.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    _apply_pragmas(conn)
    return conn


def connect_reader(db_path: Path) -> sqlite3.Connection:
    """Open a genuinely read-only connection.

    SQLite raises `OperationalError` on any write attempted against a
    `mode=ro` connection, so this is safe to hand to any thread or CLI
    command that only queries -- it cannot become a second writer even by
    accident.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
    _apply_pragmas(conn)
    return conn


def checkpoint_truncate(conn: sqlite3.Connection) -> None:
    """Spec 8.1: run at run boundaries so the WAL file does not grow
    unbounded across a 10-hour session."""
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
