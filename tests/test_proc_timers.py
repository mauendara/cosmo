"""Wall-clock and stall timers (spec 3.3, plan Phase 2 build item 4).

A fake clock is injected everywhere so these run in microseconds and are
fully deterministic -- no real `sleep`, matching how the rest of the suite
avoids depending on wall-clock timing (e.g. the fake proxy connection in
test_events.py rather than an actual `kill -9`).
"""

from __future__ import annotations

from cosmo.proc.timers import LivenessTimers, StallTimer, WallClockTimer


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_wall_clock_timer_expires_at_its_interval_and_never_resets() -> None:
    clock = FakeClock()
    timer = WallClockTimer(10.0, clock=clock)

    clock.advance(9.9)
    assert not timer.expired()

    clock.advance(0.2)
    assert timer.expired()


def test_stall_timer_expires_without_a_poke() -> None:
    clock = FakeClock()
    timer = StallTimer(5.0, clock=clock)

    clock.advance(4.9)
    assert not timer.expired()

    clock.advance(0.2)
    assert timer.expired()


def test_stall_timer_poke_resets_the_countdown() -> None:
    """spec 3.3 note: reset by either a checkbox transition or a stream
    event, so a long legitimate subtask does not trip it."""
    clock = FakeClock()
    timer = StallTimer(5.0, clock=clock)

    clock.advance(4.9)
    timer.poke()
    clock.advance(4.9)
    assert not timer.expired()

    clock.advance(0.2)
    assert timer.expired()


def test_liveness_timers_poke_only_touches_stall_not_wall() -> None:
    clock = FakeClock()
    timers = LivenessTimers(wall_s=100.0, stall_s=5.0, clock=clock)

    clock.advance(4.0)
    timers.poke()
    clock.advance(4.0)
    assert not timers.expired()

    # The wall clock, never poked, still expires on its own schedule.
    clock.advance(92.5)
    assert timers.expired()
    assert timers.wall.expired()


def test_liveness_timers_expires_true_if_either_timer_fires() -> None:
    clock = FakeClock()
    timers = LivenessTimers(wall_s=1000.0, stall_s=5.0, clock=clock)

    clock.advance(5.1)
    assert timers.expired()
    assert timers.stall.expired()
    assert not timers.wall.expired()
