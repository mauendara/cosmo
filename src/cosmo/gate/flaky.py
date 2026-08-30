"""Spec 6.4: confirm-by-rerun and the quarantine-candidate escalation.

Scoped to e2e only, matching the spec's own framing ("When a non-quarantined
e2e test fails..."); unit-test flakiness isn't addressed by this spec and
this module makes no attempt to handle it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cosmo.gate.quarantine import append_quarantine_candidate
from cosmo.store.clock import utcnow_iso
from cosmo.store.reader import list_events

RunSingleTest = Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class FlakyRerunOutcome:
    test_id: str
    attempts: int
    resolved: bool


def confirm_by_rerun(
    test_id: str, run_single_test: RunSingleTest, *, rerun_limit: int
) -> FlakyRerunOutcome:
    """Spec 6.4 steps 1-3: rerun in isolation up to `rerun_limit` times. The
    first pass wins -- `resolved=True` classifies the failure `flaky`, not
    `code_error`, and consumes no retry attempt. Exhausting every rerun
    without a pass means it's a genuine `code_error`."""
    for attempt in range(1, rerun_limit + 1):
        if run_single_test(test_id):
            return FlakyRerunOutcome(test_id=test_id, attempts=attempt, resolved=True)
    return FlakyRerunOutcome(test_id=test_id, attempts=rerun_limit, resolved=False)


def _historical_flaky_run_ids(db_path: Path, test_id: str) -> list[str]:
    """Distinct `run_id`s where a past `task.validation_result` event
    reported `test_id` in `flaky_detected` -- "three flaky classifications
    ... across distinct runs" (spec 6.4 step 4) means distinct runs, not
    distinct events, so a test flagged twice in one run's flaky_detected
    list (e.g. via a retry) still only counts once here."""
    events = list_events(db_path, event_type="task.validation_result", limit=1000)
    run_ids: set[str] = set()
    for event in events:
        flaky = event.payload.get("flaky_detected")
        if isinstance(flaky, list) and test_id in flaky and event.run_id:
            run_ids.add(event.run_id)
    return sorted(run_ids)


def maybe_escalate_to_quarantine_candidate(
    *,
    db_path: Path,
    test_id: str,
    current_run_id: str | None,
    candidates_path: Path,
    threshold: int,
) -> bool:
    """Spec 6.4 step 4. Never writes to `quarantine.yml` -- only ever the
    separate candidates file -- and never auto-quarantines; a human decides.
    Returns whether an escalation was recorded."""
    run_ids = set(_historical_flaky_run_ids(db_path, test_id))
    if current_run_id:
        run_ids.add(current_run_id)
    if len(run_ids) < threshold:
        return False
    append_quarantine_candidate(
        candidates_path, test_id, run_ids=sorted(run_ids), detected_at=utcnow_iso()
    )
    return True
