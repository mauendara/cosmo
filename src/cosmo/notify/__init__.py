"""Notification sinks and the always-on watcher (v5 improvements plan part 3)."""

from __future__ import annotations

from cosmo.notify.sink import Sink
from cosmo.notify.telegram import TelegramSink
from cosmo.notify.watch import WatchState, run_watch_loop, watch_once

__all__ = ["Sink", "TelegramSink", "WatchState", "run_watch_loop", "watch_once"]
