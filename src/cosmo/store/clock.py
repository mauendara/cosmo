"""One clock for every table that stamps a timestamp.

All timestamps in the store are UTC ISO 8601 with millisecond precision, so
`events.timestamp`, `task_queue.updated_at`, and everything else sort and
compare as plain strings without a parse step. `to_local`/`format_local`
below are the read-side counterpart: every *display* of a stored timestamp
converts to the host's local timezone (`datetime.astimezone()` with no
argument), since a human reading `cosmo`'s console output thinks in their
own clock, not UTC. Storage itself never changes -- only presentation.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def to_local(ts: str) -> datetime:
    """Parse a stored UTC ISO timestamp and convert to the host's local
    timezone. Raises `ValueError` on a malformed timestamp -- callers that
    display arbitrary/legacy values should go through `format_local`
    instead, which falls back to the raw string rather than raising."""
    return datetime.fromisoformat(ts).astimezone()


def format_local(ts: str, fmt: str = "%Y-%m-%d %H:%M:%S %Z") -> str:
    """`ts` formatted in the host's local timezone for display. Falls back
    to `ts` unchanged if it isn't a parseable ISO timestamp, so a bad or
    missing value degrades to "show something" rather than a crash."""
    try:
        return to_local(ts).strftime(fmt)
    except ValueError:
        return ts
