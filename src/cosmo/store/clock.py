"""One clock for every table that stamps a timestamp.

All timestamps in the store are UTC ISO 8601 with millisecond precision, so
`events.timestamp`, `task_queue.updated_at`, and everything else sort and
compare as plain strings without a parse step.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
