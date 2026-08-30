"""Spec 6.4: the version-controlled quarantine list and its candidates file.

Both files ship inside Cosmo's own package (`gate/data/`), the same
"computed default, overridable" posture `config/loader.py` takes toward
`defaults.toml` -- `GateConfig.quarantine_file`/`quarantine_candidates_file`
default to `None`, meaning "use the bundled copy," and tests point them at a
tmp_path copy instead of touching the real files.

An expired entry fails validation of the file itself (raises, does not skip
silently) -- spec 6.4's whole point is that an unowned, unexpiring
quarantine is how a suite quietly stops testing anything, so a quarantine
list nobody has looked at since it expired must not keep silently working.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

import yaml

_BUNDLED_QUARANTINE = Path(__file__).with_name("data") / "quarantine.yml"
_BUNDLED_CANDIDATES = Path(__file__).with_name("data") / "quarantine-candidates.yml"


class QuarantineFileError(ValueError):
    """Raised when quarantine.yml itself is malformed or contains an expired,
    still-active entry."""


@dataclass(frozen=True, slots=True)
class QuarantineEntry:
    test_id: str
    owner: str
    expiry: datetime.date
    reason: str | None = None


def quarantine_file_path(configured: Path | None) -> Path:
    return configured if configured is not None else _BUNDLED_QUARANTINE


def quarantine_candidates_path(configured: Path | None) -> Path:
    return configured if configured is not None else _BUNDLED_CANDIDATES


def load_quarantine(path: Path, *, today: datetime.date | None = None) -> list[QuarantineEntry]:
    today = today if today is not None else datetime.date.today()
    raw = yaml.safe_load(path.read_text()) or {}
    entries_raw = raw.get("entries") or []

    entries: list[QuarantineEntry] = []
    for i, item in enumerate(entries_raw):
        for required in ("test_id", "owner", "expiry"):
            if required not in item:
                raise QuarantineFileError(
                    f"{path}: entry {i} is missing required field {required!r}"
                )
        expiry = item["expiry"]
        expiry_date = (
            expiry
            if isinstance(expiry, datetime.date)
            else datetime.date.fromisoformat(str(expiry))
        )
        if expiry_date < today:
            raise QuarantineFileError(
                f"{path}: entry {item['test_id']!r} (owner {item['owner']!r}) "
                f"expired on {expiry_date.isoformat()} -- renew or remove it, "
                f"an expired quarantine entry must not keep silently protecting a test"
            )
        entries.append(
            QuarantineEntry(
                test_id=str(item["test_id"]),
                owner=str(item["owner"]),
                expiry=expiry_date,
                reason=item.get("reason"),
            )
        )
    return entries


def is_quarantined(test_id: str, entries: list[QuarantineEntry]) -> bool:
    return any(e.test_id == test_id for e in entries)


def append_quarantine_candidate(
    path: Path, test_id: str, *, run_ids: list[str], detected_at: str
) -> None:
    """Spec 6.4 step 4. Never touches `quarantine.yml` itself -- only ever
    this separate candidates file, for a human to review and, if warranted,
    promote by hand. Idempotent: re-appending the same test_id updates its
    `run_ids`/`detected_at` rather than duplicating the entry."""
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    candidates = raw.get("candidates") or []

    existing = next((c for c in candidates if c.get("test_id") == test_id), None)
    if existing is not None:
        existing["run_ids"] = run_ids
        existing["detected_at"] = detected_at
    else:
        candidates.append({"test_id": test_id, "run_ids": run_ids, "detected_at": detected_at})

    raw["candidates"] = candidates
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
