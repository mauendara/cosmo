"""Telegram Bot API sink (v5 improvements plan part 3). stdlib `urllib`
only, no dependency -- same "not worth a dependency for one HTTP call"
reasoning `watchdog.py`'s `sd_notify` integration already uses for its one
`AF_UNIX` datagram.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from cosmo.events.envelope import Event
from cosmo.events.format import event_detail

_API_BASE = "https://api.telegram.org"

_SEVERITY_EMOJI = {"info": "ℹ️", "warning": "⚠️", "error": "\U0001f534", "critical": "\U0001f6a8"}


def format_event(event: Event) -> str:
    """A short, human-readable message -- no markup, sent as Telegram plain
    text (no `parse_mode`), so nothing here needs escaping. Type-specific
    detail comes from `events.format.event_detail`, the same function
    `cli.main._print_emit` uses for the live terminal; an event type it
    doesn't recognize (or one missing the fields it looks for) falls back
    to the raw payload rather than showing nothing, so a genuinely new
    event type is never silently unreadable."""
    emoji = _SEVERITY_EMOJI.get(event.severity.value, "")
    header = f"{emoji} {event.event_type} ({event.severity.value})".strip()
    lines = [header]
    if event.run_id:
        lines.append(f"run: {event.run_id}")
    if event.task_id:
        lines.append(f"task: {event.task_id}")
    detail = event_detail(event)
    if detail:
        lines.append(detail)
    elif event.payload:
        lines.append(json.dumps(event.payload, indent=2, default=str))
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class TelegramSink:
    bot_token: str
    chat_id: str
    timeout_seconds: float = 10.0

    def send(self, event: Event) -> None:
        url = f"{_API_BASE}/bot{self.bot_token}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": self.chat_id, "text": format_event(event)}
        ).encode()
        request = urllib.request.Request(url, data=data, method="POST")
        try:
            urllib.request.urlopen(request, timeout=self.timeout_seconds)  # noqa: S310
        except (OSError, urllib.error.URLError):
            # Best-effort, same posture as `watchdog.notify`: a flaky
            # network or a bad token must never take the watcher down with
            # it -- there is nothing more important than the watcher itself
            # staying alive to notice the *next* real event.
            return
