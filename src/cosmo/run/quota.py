"""Spec 7.1/7.2: quota-window exhaustion detection and the pause/resume/stop
decision. Detection order, applied in `observe_harness_result`/
`HeuristicTracker` below:

1. **Primary** -- a harness's own structured signal (`HarnessResult.
   quota_window`/`quota_resets_at`; the Claude adapter fills these from
   `harness.claude.stream.extract_quota_signal`).
2. **Secondary** -- the terminal result's error subtype, matched against
   `QuotaConfig.result_error_subtypes` (unverified against a real capture --
   see that config's own docstring).
3. **Tertiary** -- `HeuristicTracker`'s wall-clock heuristic: repeated
   immediate, tool-call-free failures across distinct tasks. Never
   confirmed (`QuotaSignal.confirmed=False`); the run loop must not let it
   pick `weekly` (there is no way to guess that from a heuristic alone) or
   report a `STOPPED` outcome as confidently as a confirmed signal would --
   `decide()` below always treats an unconfirmed signal as the shorter,
   safer `five_hour` window for exactly that reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from cosmo.config.model import QuotaConfig
from cosmo.harness.base import HarnessResult
from cosmo.store.enums import PauseReason, RunStatus, StopReason


@dataclass(frozen=True, slots=True)
class QuotaSignal:
    window: str
    """`"five_hour"` or `"weekly"` (spec 7.1's two windows)."""
    resets_at: str | None
    """UTC ISO 8601, when known."""
    confirmed: bool
    """`False` only for `HeuristicTracker`'s tertiary signal."""


def observe_harness_result(result: HarnessResult, config: QuotaConfig) -> QuotaSignal | None:
    """Primary + secondary detection for one raw `HarnessResult`.
    Deliberately only actionable on a *failed* call: a rate-limit signal
    seen mid-stream does not mean the call ultimately failed -- a real
    capture (`test_api_retry_is_the_primary_quota_signal_in_both_observed_
    shapes`) shows the CLI's own internal retry absorbing one and the call
    still succeeding."""
    if result.success:
        return None
    if result.quota_window is not None:
        return QuotaSignal(
            window=result.quota_window, resets_at=result.quota_resets_at, confirmed=True
        )
    if result.output_summary in config.result_error_subtypes:
        return QuotaSignal(window="five_hour", resets_at=None, confirmed=True)
    return None


@dataclass(slots=True)
class HeuristicTracker:
    """The tertiary, last-resort signal. Call `observe()` **at most once
    per distinct task** (spec 7.2: "across distinct tasks", not across
    retries of one task) -- the run loop feeds it the *last* raw
    `HarnessResult` seen for a task once that task's run is over, not every
    intermediate attempt."""

    config: QuotaConfig
    _consecutive: int = field(default=0, init=False)

    def observe(self, result: HarnessResult) -> QuotaSignal | None:
        immediate_and_empty = (
            not result.success
            and result.tool_call_count == 0
            and result.duration_seconds <= self.config.heuristic_max_duration_seconds
        )
        self._consecutive = self._consecutive + 1 if immediate_and_empty else 0
        if self._consecutive >= self.config.heuristic_consecutive_threshold:
            return QuotaSignal(window="five_hour", resets_at=None, confirmed=False)
        return None


@dataclass(frozen=True, slots=True)
class QuotaDecision:
    status: RunStatus
    """`PAUSED` or `STOPPED` -- never anything else."""
    pause_reason: PauseReason | None
    stop_reason: StopReason | None
    resume_delay_seconds: float
    """Only meaningful when `status is RunStatus.PAUSED`."""


def decide(
    signal: QuotaSignal,
    *,
    config: QuotaConfig,
    run_wall_remaining_seconds: float,
    now: datetime,
) -> QuotaDecision:
    """Spec 7.1's branch, the one that "actually matters":
    - `five_hour` (confirmed or heuristic) always `PAUSED` with a scheduled
      auto-resume -- a rolling window, short enough to just wait out.
    - `weekly` only `PAUSED` if the computed reset lies within the run's
      remaining wall-clock budget; otherwise `STOPPED` with
      `quota_exhausted_weekly` rather than holding the process idle for
      days. An unknown weekly reset (no ETA at all) is treated the same as
      "beyond budget" -- there is nothing to schedule a resume against, and
      guessing an ETA would be worse than admitting there isn't one.
    A signal with no `resets_at` falls back to `config.
    default_5h_resume_delay_seconds` (the `system/api_retry` wire shape
    never carries one -- see `harness.claude.stream.extract_quota_signal`).
    """
    if signal.resets_at is not None:
        resets = datetime.fromisoformat(signal.resets_at)
        delay = max(0.0, (resets - now).total_seconds())
        eta_known = True
    else:
        delay = float(config.default_5h_resume_delay_seconds)
        eta_known = False

    if signal.window == "five_hour":
        return QuotaDecision(
            status=RunStatus.PAUSED,
            pause_reason=PauseReason.QUOTA_EXHAUSTED_5H,
            stop_reason=None,
            resume_delay_seconds=delay,
        )

    if not eta_known or delay > run_wall_remaining_seconds:
        return QuotaDecision(
            status=RunStatus.STOPPED,
            pause_reason=None,
            stop_reason=StopReason.QUOTA_EXHAUSTED_WEEKLY,
            resume_delay_seconds=0.0,
        )
    return QuotaDecision(
        status=RunStatus.PAUSED,
        pause_reason=PauseReason.QUOTA_EXHAUSTED_WEEKLY,
        stop_reason=None,
        resume_delay_seconds=delay,
    )
