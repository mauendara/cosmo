"""Phase 1 exit criterion: a watcher thread and the main loop both write with
zero SQLITE_BUSY.

This stresses the pragmas themselves (WAL + a 10s busy_timeout, spec 8.1),
opening two independent write connections on purpose -- the scenario
single-writer discipline (test_store_boundary.py) is designed to avoid in
Cosmo's own code, but the guarantee needs to hold even if it didn't, since
SQLite itself tolerates multiple writers under WAL (spec 8.1's own reasoning
for why the pragmas, not just the discipline, matter).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from cosmo.store.connection import connect_writer
from cosmo.store.migrations import migrate


def test_concurrent_writers_never_hit_sqlite_busy(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    bootstrap = connect_writer(db_path)
    migrate(bootstrap)
    bootstrap.close()

    errors: list[BaseException] = []
    stop = threading.Event()

    def hammer(prefix: str, counter: list[int]) -> None:
        # Each thread opens its own connection -- sqlite3 connections cannot
        # cross threads, and this is exactly the "second writer" shape the
        # pragmas (not the single-writer discipline) must survive.
        conn = connect_writer(db_path)
        try:
            while not stop.is_set():
                counter[0] += 1
                try:
                    with conn:
                        conn.execute(
                            "INSERT INTO event_sequence (scope, next_value) VALUES (?, 1) "
                            "ON CONFLICT(scope) DO UPDATE SET next_value = next_value + 1",
                            (f"{prefix}-{counter[0]}",),
                        )
                except sqlite3.OperationalError as exc:
                    errors.append(exc)
        finally:
            conn.close()

    watcher_counter = [0]
    watcher = threading.Thread(target=hammer, args=("watcher", watcher_counter))
    watcher.start()

    main_counter = [0]
    try:
        deadline = time.monotonic() + 1.0
        main_conn = connect_writer(db_path)
        while time.monotonic() < deadline:
            main_counter[0] += 1
            try:
                with main_conn:
                    main_conn.execute(
                        "INSERT INTO event_sequence (scope, next_value) VALUES (?, 1) "
                        "ON CONFLICT(scope) DO UPDATE SET next_value = next_value + 1",
                        (f"main-{main_counter[0]}",),
                    )
            except sqlite3.OperationalError as exc:
                errors.append(exc)
    finally:
        stop.set()
        watcher.join(timeout=5)
        main_conn.close()

    assert watcher_counter[0] > 0 and main_counter[0] > 0, "both threads must have actually raced"
    assert not errors, f"SQLITE_BUSY (or similar) under concurrent writes: {errors}"
