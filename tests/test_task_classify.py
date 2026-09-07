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
    assert classification.terminal is False


def test_provider_budget_exceeded_is_classified_terminal() -> None:
    """G8 (docs/v15-fixes-after-wa-chat-run.md): the real
    `wa-chat-interactive-buttons-cta` shape -- an OpenRouter 403 key-limit
    error is a hard ceiling, not a transient blip, and must be flagged
    `terminal=True` so `task.machine` blocks immediately instead of
    retrying toward `max_attempts`."""
    result = _result(
        success=False, exit_code=1, output_summary="403 Key limit exceeded (total limit)"
    )

    classification = classify_harness_failure(result, stage=FailureStage.IMPLEMENT, timed_out=False)

    assert classification.failure_type is FailureType.ENVIRONMENT_ERROR
    assert classification.terminal is True
    assert classification.error_detail == classification.error_summary


def test_monthly_spend_limit_is_also_classified_terminal() -> None:
    result = _result(
        success=False,
        exit_code=1,
        output_summary="You've hit your monthly spend limit -- upgrade your plan",
    )

    classification = classify_harness_failure(result, stage=FailureStage.IMPLEMENT, timed_out=False)

    assert classification.terminal is True


def test_error_max_turns_is_classified_as_a_signature_but_not_terminal() -> None:
    """G1 (docs/v15-fixes-after-wa-chat-run.md): `error_max_turns` is a
    retryable, adaptive-budget case, not a hard ceiling like G8's
    `provider_budget_exceeded` -- but it still needs `error_detail`
    populated so `store.writer.record_task_failure`'s own signature
    classification (and `task.machine`'s consecutive-failure counting) can
    see it."""
    result = _result(success=False, exit_code=1, output_summary="error_max_turns")

    classification = classify_harness_failure(result, stage=FailureStage.IMPLEMENT, timed_out=False)

    assert classification.terminal is False
    assert classification.error_detail == "error_max_turns"


def test_timeout_is_never_classified_terminal_even_with_budget_wording() -> None:
    # A timeout is classified before `result` is ever consulted -- terminal
    # only ever applies to the environment_error path.
    classification = classify_harness_failure(None, stage=FailureStage.IMPLEMENT, timed_out=True)

    assert classification.terminal is False
