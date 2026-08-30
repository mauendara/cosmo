"""Single-writer discipline, enforced by test rather than by discipline alone
(spec 8: "all writes go through one connection owned by the main loop").

`connect_writer` opens the one write connection. A future watcher or
stream-reader module (Phase 2/3) must push writes through
`StoreWriter.submit()` instead of opening a second one -- this test keeps
`connect_writer` from being reachable anywhere outside the two modules that
legitimately need it.
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "cosmo"

ALLOWED_WRITER_IMPORTERS = {
    SRC / "store" / "writer.py",
    SRC / "store" / "migrations.py",
    SRC / "store" / "connection.py",  # defines it; referencing its own name is not a call
}


def _core_python_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def test_connect_writer_is_not_reachable_outside_the_allowlist() -> None:
    offenders = [
        str(p.relative_to(SRC))
        for p in _core_python_files()
        if p not in ALLOWED_WRITER_IMPORTERS and "connect_writer" in p.read_text()
    ]
    assert not offenders, (
        f"connect_writer referenced outside the single-writer allowlist: {offenders}. "
        f"Go through StoreWriter.submit() instead (spec 8)."
    )


def test_store_writer_is_the_only_public_way_to_get_a_write_connection() -> None:
    """`cosmo.store`'s public surface never re-exports `connect_writer`."""
    import cosmo.store as store_pkg

    assert not hasattr(store_pkg, "connect_writer")
    assert "connect_writer" not in store_pkg.__all__
