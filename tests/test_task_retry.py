"""`task.retry.build_retry_context` (spec 6.3's informed retries)."""

from __future__ import annotations

from cosmo.store.reader import TaskFailureRow
from cosmo.task.retry import build_retry_context


def _failure(
    attempt_number: int, error_summary: str, error_detail: str | None = None
) -> TaskFailureRow:
    return TaskFailureRow(
        id=attempt_number,
        task_id="t1",
        run_id=None,
        attempt_number=attempt_number,
        failure_type="code_error",
        failure_stage="unit_tests",
        error_summary=error_summary,
        error_detail=error_detail,
        files_touched=[],
        will_retry=True,
        next_action="retry",
        timestamp="2026-01-01T00:00:00.000Z",
    )


def test_no_failures_yields_no_retry_context() -> None:
    assert build_retry_context([]) is None


def test_one_failure_carries_its_error_detail() -> None:
    context = build_retry_context(
        [_failure(0, "unit test failed", "AssertionError: expected 2 got 1")]
    )

    assert context is not None
    assert "unit test failed" in context
    assert "AssertionError: expected 2 got 1" in context


def test_multiple_failures_summarize_earlier_attempts_and_detail_the_latest() -> None:
    failures = [
        _failure(0, "build failed", "missing import"),
        _failure(1, "unit test failed", "assertion mismatch"),
    ]

    context = build_retry_context(failures)

    assert context is not None
    assert "unit test failed" in context
    assert "assertion mismatch" in context
    assert "Previous attempts:" in context
    assert "build failed" in context
