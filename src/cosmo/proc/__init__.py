"""Process supervision: process-group kill semantics, orphan sweep, and the
two liveness timers a managed run carries (spec 2.4, 3.3)."""

from __future__ import annotations

from cosmo.proc.managed import ManagedProcess
from cosmo.proc.orphans import SweepResult, find_worktree_holders, sweep, sweep_containers
from cosmo.proc.reap import ReapOutcome, cancel_and_reap
from cosmo.proc.timers import LivenessTimers, StallTimer, WallClockTimer

__all__ = [
    "LivenessTimers",
    "ManagedProcess",
    "ReapOutcome",
    "StallTimer",
    "SweepResult",
    "WallClockTimer",
    "cancel_and_reap",
    "find_worktree_holders",
    "sweep",
    "sweep_containers",
]
