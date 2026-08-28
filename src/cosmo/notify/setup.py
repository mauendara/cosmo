"""Interactive one-time setup helpers for `cosmo notify config` (`cli.main.
notify_config`). Deliberately separate from `notify.telegram.TelegramSink`:
that sink's whole contract is "never raise, a flaky network must not take
the watcher down with it" (best-effort, matching `watchdog.notify`'s own
posture). This module is the opposite -- a human is sitting at the wizard,
so a bad token or an unreachable API should surface as a real, readable
error right away, not get swallowed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

_API_BASE = "https://api.telegram.org"


class TelegramApiError(RuntimeError):
    """A real, surfaced failure talking to the Bot API -- a rejected token,
    an unreachable network, or a malformed response."""


def _call(
    bot_token: str, method: str, *, data: dict[str, str] | None, timeout_seconds: float
) -> object:
    url = f"{_API_BASE}/bot{bot_token}/{method}"
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode() if data is not None else None,
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            body = json.loads(response.read())
    except (OSError, urllib.error.URLError) as exc:
        raise TelegramApiError(f"could not reach the Telegram API: {exc}") from None
    except json.JSONDecodeError as exc:
        raise TelegramApiError(f"malformed response from the Telegram API: {exc}") from None
    if not isinstance(body, dict) or not body.get("ok"):
        description = body.get("description") if isinstance(body, dict) else body
        raise TelegramApiError(f"Telegram API rejected the request: {description}")
    return body.get("result")


def discover_chat_id(bot_token: str, *, timeout_seconds: float = 10.0) -> str | None:
    """The chat id of the most recent message sent to the bot, or `None` if
    nobody has messaged it yet -- Telegram bots cannot message first, so
    this is expected to return `None` on a fresh bot until the wizard tells
    the user to go send it one message. Raises `TelegramApiError` on a real
    API/network failure (almost always a bad token) so the wizard can tell
    that apart from "just hasn't been messaged yet"."""
    result = _call(bot_token, "getUpdates", data=None, timeout_seconds=timeout_seconds)
    updates = result if isinstance(result, list) else []
    if not updates:
        return None
    latest = updates[-1]
    chat = latest.get("message", {}).get("chat", {}) if isinstance(latest, dict) else {}
    chat_id = chat.get("id")
    return str(chat_id) if chat_id is not None else None


def send_test_message(bot_token: str, chat_id: str, *, timeout_seconds: float = 10.0) -> None:
    """A real, verified send -- raises `TelegramApiError` on failure (unlike
    `TelegramSink.send`'s best-effort posture), so the wizard can tell the
    user setup actually worked rather than silently accepting a bad
    chat id."""
    text = "cosmo notify: this chat is now configured to receive alerts."
    _call(
        bot_token,
        "sendMessage",
        data={"chat_id": chat_id, "text": text},
        timeout_seconds=timeout_seconds,
    )
