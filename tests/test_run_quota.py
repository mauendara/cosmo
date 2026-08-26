"""`run.quota` (spec 7.1/7.2, plan Phase 8 exit criteria): detection order
(primary structured signal, secondary result-error-subtype, tertiary
wall-clock heuristic) and the pause/resume/stop decision -- 5-hour always
auto-resumes, weekly only if the reset lies within the run's remaining
wall-clock budget, otherwise stops."""

from __future__ import annotations

from datetime import UTC, datetime

from cosmo.config.model import QuotaConfig
from cosmo.harness.base import HarnessResult
from cosmo.run.quota import HeuristicTracker, QuotaSignal, decide, observe_harness_result
from cosmo.store.enums import PauseReason, RunStatus, StopReason


def _config(**overrides: object) -> QuotaConfig:
    base = {
        "result_error_subtypes": ["error_rate_limit"],
        "heuristic_consecutive_threshold": 3,
        "heuristic_max_duration_seconds": 5.0,
        "default_5h_resume_delay_seconds": 18000,
    }
    base.update(overrides)
    return QuotaConfig(**base)  # type: ignore[arg-type]


def _result(
    *,
    success: bool,
    output_summary: str = "",
    quota_window: str | None = None,
    quota_resets_at: str | None = None,
    tool_call_count: int = 0,
    duration_seconds: float = 1.0,
) -> HarnessResult:
    return HarnessResult(
        success=success,
        output_summary=output_summary,
        raw_log_path=None,
        files_changed=[],
        duration_seconds=duration_seconds,
        total_cost_usd=None,
        exit_code=0 if success else 1,
        session_id="s",
        quota_window=quota_window,
        quota_resets_at=quota_resets_at,
        tool_call_count=tool_call_count,
    )


# -- observe_harness_result (primary + secondary) ----------------------------


def test_a_successful_call_is_never_actionable_even_with_a_signal_present() -> None:
    # Spec 4's own capture: the CLI's internal retry can absorb a rate
    # limit and still succeed.
    result = _result(success=True, quota_window="five_hour")

    assert observe_harness_result(result, _config()) is None


def test_primary_signal_wins_and_carries_the_window_and_reset_eta() -> None:
    result = _result(success=False, quota_window="weekly", quota_resets_at="2026-09-01T00:00:00Z")

    signal = observe_harness_result(result, _config())

    assert signal == QuotaSignal(window="weekly", resets_at="2026-09-01T00:00:00Z", confirmed=True)


def test_secondary_signal_fires_on_a_configured_result_error_subtype() -> None:
    result = _result(success=False, output_summary="error_rate_limit")

    signal = observe_harness_result(result, _config())

    assert signal == QuotaSignal(window="five_hour", resets_at=None, confirmed=True)


def test_an_ordinary_failure_with_no_signal_is_not_a_quota_event() -> None:
    result = _result(success=False, output_summary="exit code 1")

    assert observe_harness_result(result, _config()) is None


# -- HeuristicTracker (tertiary) ----------------------------------------------


def test_heuristic_fires_after_n_consecutive_immediate_empty_failures() -> None:
    tracker = HeuristicTracker(_config(heuristic_consecutive_threshold=3))
    fast_empty_failure = _result(success=False, tool_call_count=0, duration_seconds=1.0)

    assert tracker.observe(fast_empty_failure) is None
    assert tracker.observe(fast_empty_failure) is None
    signal = tracker.observe(fast_empty_failure)

    assert signal == QuotaSignal(window="five_hour", resets_at=None, confirmed=False)


def test_heuristic_never_reports_confirmed() -> None:
    tracker = HeuristicTracker(_config(heuristic_consecutive_threshold=1))

    signal = tracker.observe(_result(success=False, tool_call_count=0, duration_seconds=0.1))

    assert signal is not None
    assert signal.confirmed is False


def test_a_tool_call_resets_the_heuristic_streak() -> None:
    tracker = HeuristicTracker(_config(heuristic_consecutive_threshold=2))
    fast_empty_failure = _result(success=False, tool_call_count=0, duration_seconds=1.0)
    with_tool_call = _result(success=False, tool_call_count=1, duration_seconds=1.0)

    tracker.observe(fast_empty_failure)
    tracker.observe(with_tool_call)
    result = tracker.observe(fast_empty_failure)

    assert result is None  # only 1 consecutive since the reset


def test_a_slow_failure_does_not_count_toward_the_heuristic() -> None:
    tracker = HeuristicTracker(
        _config(heuristic_consecutive_threshold=2, heuristic_max_duration_seconds=5.0)
    )
    slow_failure = _result(success=False, tool_call_count=0, duration_seconds=60.0)

    tracker.observe(slow_failure)
    result = tracker.observe(slow_failure)

    assert result is None


# -- decide() ------------------------------------------------------------


def test_five_hour_always_pauses_and_schedules_a_resume() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    signal = QuotaSignal(window="five_hour", resets_at="2026-01-01T01:00:00+00:00", confirmed=True)

    decision = decide(signal, config=_config(), run_wall_remaining_seconds=36000.0, now=now)

    assert decision.status is RunStatus.PAUSED
    assert decision.pause_reason is PauseReason.QUOTA_EXHAUSTED_5H
    assert decision.resume_delay_seconds == 3600.0


def test_five_hour_with_no_eta_falls_back_to_the_configured_default_delay() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    signal = QuotaSignal(window="five_hour", resets_at=None, confirmed=False)

    decision = decide(
        signal,
        config=_config(default_5h_resume_delay_seconds=1234),
        run_wall_remaining_seconds=36000.0,
        now=now,
    )

    assert decision.status is RunStatus.PAUSED
    assert decision.resume_delay_seconds == 1234.0


def test_weekly_within_budget_pauses_with_a_scheduled_resume() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    signal = QuotaSignal(window="weekly", resets_at="2026-01-01T02:00:00+00:00", confirmed=True)

    decision = decide(signal, config=_config(), run_wall_remaining_seconds=36000.0, now=now)

    assert decision.status is RunStatus.PAUSED
    assert decision.pause_reason is PauseReason.QUOTA_EXHAUSTED_WEEKLY
    assert decision.resume_delay_seconds == 7200.0


def test_weekly_beyond_budget_stops_rather_than_idling_for_days() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    # Reset is 3 days away; run only has 10h of wall clock left.
    signal = QuotaSignal(window="weekly", resets_at="2026-01-04T00:00:00+00:00", confirmed=True)

    decision = decide(signal, config=_config(), run_wall_remaining_seconds=36000.0, now=now)

    assert decision.status is RunStatus.STOPPED
    assert decision.stop_reason is StopReason.QUOTA_EXHAUSTED_WEEKLY
    assert decision.pause_reason is None


def test_weekly_with_no_eta_at_all_stops_rather_than_guessing() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    signal = QuotaSignal(window="weekly", resets_at=None, confirmed=True)

    decision = decide(signal, config=_config(), run_wall_remaining_seconds=36000.0, now=now)

    assert decision.status is RunStatus.STOPPED
    assert decision.stop_reason is StopReason.QUOTA_EXHAUSTED_WEEKLY
