"""Spec 6.4: the quarantine list and its candidates file."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from cosmo.gate.quarantine import (
    QuarantineFileError,
    append_quarantine_candidate,
    is_quarantined,
    load_quarantine,
)


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


def test_load_quarantine_parses_valid_entries(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "quarantine.yml",
        """
        entries:
          - test_id: "FooTest#flaky"
            owner: "alice@example.com"
            expiry: "2999-01-01"
            reason: "known flaky under load"
        """,
    )
    entries = load_quarantine(path, today=datetime.date(2026, 1, 1))
    assert len(entries) == 1
    assert entries[0].test_id == "FooTest#flaky"
    assert entries[0].owner == "alice@example.com"
    assert is_quarantined("FooTest#flaky", entries)
    assert not is_quarantined("BarTest#other", entries)


def test_load_quarantine_empty_entries_is_fine(tmp_path: Path) -> None:
    path = _write(tmp_path / "quarantine.yml", "entries: []\n")
    assert load_quarantine(path) == []


def test_load_quarantine_missing_field_raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "quarantine.yml",
        """
        entries:
          - test_id: "FooTest#flaky"
            expiry: "2999-01-01"
        """,
    )
    with pytest.raises(QuarantineFileError, match="owner"):
        load_quarantine(path)


def test_load_quarantine_expired_entry_raises(tmp_path: Path) -> None:
    """An expired, still-active entry fails validation of the file itself
    (spec 6.4) -- it must not silently keep protecting a test."""
    path = _write(
        tmp_path / "quarantine.yml",
        """
        entries:
          - test_id: "FooTest#flaky"
            owner: "alice@example.com"
            expiry: "2020-01-01"
        """,
    )
    with pytest.raises(QuarantineFileError, match="expired"):
        load_quarantine(path, today=datetime.date(2026, 1, 1))


def test_append_quarantine_candidate_creates_and_updates(tmp_path: Path) -> None:
    path = tmp_path / "quarantine-candidates.yml"
    append_quarantine_candidate(
        path, "FooTest#flaky", run_ids=["run-1", "run-2"], detected_at="2026-01-01T00:00:00Z"
    )
    text = path.read_text()
    assert "FooTest#flaky" in text
    assert "run-1" in text

    # Re-appending the same test_id updates in place rather than duplicating.
    append_quarantine_candidate(
        path,
        "FooTest#flaky",
        run_ids=["run-1", "run-2", "run-3"],
        detected_at="2026-01-02T00:00:00Z",
    )
    text2 = path.read_text()
    assert text2.count("FooTest#flaky") == 1
    assert "run-3" in text2
