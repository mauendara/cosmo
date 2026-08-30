"""`task.classify.classify_harness_failure` (spec 6.2 for `PROPOSING`/
`IMPLEMENTING`): timeout vs. environment_error, never code_error -- see the
module's own docstring for why a harness-level failure is never classified
`code_error`."""

from __future__ import annotations

from cosmo.harness.base import HarnessResult
from cosmo.store.enums import FailureStage, FailureType
from cosmo.task.classify import classify_harness_failure


def _result(*, success: bool, exit_code: int | None, output_summary: str = "") -> HarnessResult:
    return HarnessResult(
        success=success,
        output_summary=output_summary,
        raw_log_path=None,
        files_changed=[],
        duration_seconds=1.0,
        total_cost_usd=None,
        exit_code=exit_code,
        session_id="s1",
    )


def test_timed_out_classifies_as_timeout_regardless_of_result() -> None:
    classification = classify_harness_failure(None, stage=FailureStage.IMPLEMENT, timed_out=True)

    assert classification.failure_type is FailureType.TIMEOUT
    assert classification.failure_stage is FailureStage.IMPLEMENT


def test_a_failed_result_that_did_not_time_out_is_environment_error_never_code_error() -> None:
    result = _result(success=False, exit_code=1, output_summary="process crashed")

    classification = classify_harness_failure(result, stage=FailureStage.PROPOSE, timed_out=False)

    assert classification.failure_type is FailureType.ENVIRONMENT_ERROR
    assert classification.failure_stage is FailureStage.PROPOSE
    assert "process crashed" in classification.error_summary
