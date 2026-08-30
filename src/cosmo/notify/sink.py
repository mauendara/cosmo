"""The notification `Sink` protocol (v5 improvements plan part 3) -- shaped
like this codebase's own harness-adapter/gate-stage pattern: one small
protocol, one real implementation (`notify.telegram.TelegramSink`) shipped
alongside it, cheap to keep generic now rather than reshape later if a
second channel (a webhook, Slack) shows up.
"""

from __future__ import annotations

from typing import Protocol

from cosmo.events.envelope import Event


class Sink(Protocol):
    def send(self, event: Event) -> None:
        """Best-effort: a sink must never raise past this call (mirrors
        `watchdog.notify`'s "a stale/misconfigured target must never take
        the run down with it" posture) -- `event` may be a real, persisted
        `events` row, or a synthetic one `notify.watch` constructs itself
        (e.g. a staleness alert, which by definition has no row to read)."""
