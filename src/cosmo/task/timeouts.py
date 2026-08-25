"""Per-state timeout wiring (spec 3.3), generalizing the background-thread
+ join + cancel pattern `cli/main.py`'s `harness_probe` command (Phase 3)
already hand-rolled for a single ad hoc case.

`has_internal_timeout` is `False` for every adapter Cosmo ships (spec 2.2's
own note: "Cosmo imposes an external timeout" when it is), so this module
owns the wall-clock/stall race against a blocking `HarnessAdapter` call, the
same way `cli/main.py`'s probe command already did for one call.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from cosmo.harness.base import HarnessResult
from cosmo.proc.timers import LivenessTimers


class TimeoutResult[T]:
    __slots__ = ("value", "timed_out")

    def __init__(self, value: T | None, *, timed_out: bool) -> None:
        self.value = value
        self.timed_out = timed_out


def run_with_wall_clock_timeout(
    fn: Callable[[], HarnessResult],
    *,
    wall_s: float,
    cancel: Callable[[], None],
    kill_grace_s: float,
) -> TimeoutResult[HarnessResult]:
    """`PROPOSING`/`COMMITTING`: a single blocking harness call with no stall
    timer (spec 3.3's table gives neither a stall value). Runs `fn` on a
    background thread, same as `cli/main.py`'s existing probe timeout, so a
    hung subprocess never blocks the calling thread past `wall_s` +
    `kill_grace_s`."""
    return run_with_liveness_timeout(
        fn, timers=None, wall_s=wall_s, cancel=cancel, kill_grace_s=kill_grace_s
    )


def run_with_liveness_timeout(
    fn: Callable[[], HarnessResult],
    *,
    timers: LivenessTimers | None,
    wall_s: float,
    cancel: Callable[[], None],
    kill_grace_s: float,
    on_tick: Callable[[], None] | None = None,
) -> TimeoutResult[HarnessResult]:
    """`IMPLEMENTING`'s shape: `fn` runs on a background thread while
    `timers` (constructed and *poked* by the caller -- typically the
    progress watcher, on a second background thread it owns) is polled here
    for expiry. Passing `timers=None` degrades to a plain wall-clock join
    (`run_with_wall_clock_timeout`'s case): there is nothing to poke, so
    there is nothing to expire early.

    The caller owns constructing/poking `timers` (rather than this function
    building its own) specifically so the progress watcher's poke calls
    land on the same object this loop checks -- a `LivenessTimers` built
    locally here would never see them.

    `on_tick`, when given, runs once per wake-up on *this* (the caller's)
    thread -- the natural place to drain `StoreWriter`'s cross-thread queue
    (Phase 1's `submit()`/`drain()` handoff) and poll progress, since
    nothing else wakes this thread on the right cadence otherwise.
    """
    result_box: list[HarnessResult] = []
    error_box: list[BaseException] = []

    def _run() -> None:
        try:
            result_box.append(fn())
        except BaseException as exc:  # noqa: BLE001 -- re-raised on the caller's thread below
            error_box.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    if timers is None:
        thread.join(timeout=wall_s)
        timed_out = thread.is_alive()
    else:
        check_interval = min(5.0, max(0.5, wall_s / 20))
        timed_out = False
        while thread.is_alive():
            thread.join(timeout=check_interval)
            if on_tick is not None:
                on_tick()
            if not thread.is_alive():
                break
            if timers.expired():
                timed_out = True
                break

    if timed_out:
        cancel()
        thread.join(timeout=kill_grace_s)

    if error_box:
        raise error_box[0]
    if not result_box:
        return TimeoutResult(None, timed_out=True)
    return TimeoutResult(result_box[0], timed_out=timed_out)
