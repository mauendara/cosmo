"""Spec 6.4: confirm-by-rerun and the quarantine-candidate escalation."""

from __future__ import annotations

from pathlib import Path

from cosmo.events.emitter import EventEmitter
from cosmo.events.envelope import EventType
from cosmo.gate.flaky import confirm_by_rerun, maybe_escalate_to_quarantine_candidate
from cosmo.store.enums import Severity
from cosmo.store.writer import StoreWriter


def test_confirm_by_rerun_resolves_on_first_pass() -> None:
    calls: list[str] = []

    def run_single_test(test_id: str) -> bool:
        calls.append(test_id)
        return True

    outcome = confirm_by_rerun("FooTest#a", run_single_test, rerun_limit=3)
    assert outcome.resolved
    assert outcome.attempts == 1
    assert calls == ["FooTest#a"]


def test_confirm_by_rerun_exhausts_without_a_pass() -> None:
    attempts = {"n": 0}

    def run_single_test(test_id: str) -> bool:
        attempts["n"] += 1
        return False

    outcome = confirm_by_rerun("FooTest#a", run_single_test, rerun_limit=3)
    assert not outcome.resolved
    assert outcome.attempts == 3
    assert attempts["n"] == 3


def test_confirm_by_rerun_stops_at_first_pass_not_last_attempt() -> None:
    sequence = iter([False, True, False])

    def run_single_test(test_id: str) -> bool:
        return next(sequence)

    outcome = confirm_by_rerun("FooTest#a", run_single_test, rerun_limit=3)
    assert outcome.resolved
    assert outcome.attempts == 2


def _writer(tmp_path: Path) -> StoreWriter:
    return StoreWriter(tmp_path / "cosmo.db")


def test_escalation_below_threshold_does_not_write_candidate(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    candidates_path = tmp_path / "quarantine-candidates.yml"

    escalated = maybe_escalate_to_quarantine_candidate(
        db_path=tmp_path / "cosmo.db",
        test_id="FooTest#flaky",
        current_run_id="run-1",
        candidates_path=candidates_path,
        threshold=3,
    )
    assert not escalated
    assert not candidates_path.exists()
    writer.close()


def test_escalation_at_threshold_writes_candidate(tmp_path: Path) -> None:
    db_path = tmp_path / "cosmo.db"
    writer = _writer(tmp_path)
    emitter = EventEmitter(writer)
    candidates_path = tmp_path / "quarantine-candidates.yml"

    # Two prior distinct runs already reported this test as flaky.
    for run_id in ("run-1", "run-2"):
        emitter.emit(
            event_type=EventType.TASK_VALIDATION_RESULT,
            severity=Severity.WARNING,
            run_id=run_id,
            task_id="task-1",
            payload={"flaky_detected": ["FooTest#flaky"]},
        )

    escalated = maybe_escalate_to_quarantine_candidate(
        db_path=db_path,
        test_id="FooTest#flaky",
        current_run_id="run-3",
        candidates_path=candidates_path,
        threshold=3,
    )
    assert escalated
    assert candidates_path.exists()
    text = candidates_path.read_text()
    assert "FooTest#flaky" in text
    assert "run-1" in text and "run-2" in text and "run-3" in text
    writer.close()


def test_escalation_never_touches_quarantine_yml_itself(tmp_path: Path) -> None:
    """Spec 6.4 step 4: escalation only ever writes the candidates file."""
    db_path = tmp_path / "cosmo.db"
    writer = _writer(tmp_path)
    emitter = EventEmitter(writer)
    quarantine_path = tmp_path / "quarantine.yml"
    quarantine_path.write_text("entries: []\n")
    candidates_path = tmp_path / "quarantine-candidates.yml"

    for run_id in ("run-1", "run-2"):
        emitter.emit(
            event_type=EventType.TASK_VALIDATION_RESULT,
            severity=Severity.WARNING,
            run_id=run_id,
            task_id="task-1",
            payload={"flaky_detected": ["FooTest#flaky"]},
        )

    maybe_escalate_to_quarantine_candidate(
        db_path=db_path,
        test_id="FooTest#flaky",
        current_run_id="run-3",
        candidates_path=candidates_path,
        threshold=3,
    )
    assert quarantine_path.read_text() == "entries: []\n"
    writer.close()
