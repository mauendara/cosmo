"""Two independent timers per managed run (spec 3.3, plan Phase 2 build item 4).

Wall-clock and stall are deliberately separate objects rather than one with
two fields: the wall clock never resets once a state is entered, while the
stall timer is poked by *either* a checkbox transition or a stream event
(spec 3.3 note) so a long legitimate subtask doesn't trip it. Conflating them
would make "reset the wall clock by accident" an easy bug to write.

`clock` defaults to `time.monotonic` -- durations, not wall-clock timestamps,
so an NTP adjustment or DST change mid-run can't perturb a timeout. Tests
inject a fake clock instead of sleeping in real time.
"""

from __future__ import annotations

import time
from collections.abc import Callable

Clock = Callable[[], float]


class WallClockTimer:
    def __init__(self, interval_s: float, *, clock: Clock = time.monotonic) -> None:
        self._interval = interval_s
        self._clock = clock
        self._start = clock()

    def expired(self) -> bool:
        return self._clock() - self._start >= self._interval

    def remaining(self) -> float:
        return max(0.0, self._interval - (self._clock() - self._start))


class StallTimer:
    def __init__(self, interval_s: float, *, clock: Clock = time.monotonic) -> None:
        self._interval = interval_s
        self._clock = clock
        self._last_poke = clock()

    def poke(self) -> None:
        self._last_poke = self._clock()

    def expired(self) -> bool:
        return self._clock() - self._last_poke >= self._interval

    def remaining(self) -> float:
        return max(0.0, self._interval - (self._clock() - self._last_poke))


class LivenessTimers:
    """The pair a managed run actually carries. `poke()` only ever touches
    the stall timer -- the wall clock has no reset by design."""

    def __init__(self, *, wall_s: float, stall_s: float, clock: Clock = time.monotonic) -> None:
        self.wall = WallClockTimer(wall_s, clock=clock)
        self.stall = StallTimer(stall_s, clock=clock)

    def poke(self) -> None:
        self.stall.poke()

    def expired(self) -> bool:
        return self.wall.expired() or self.stall.expired()
