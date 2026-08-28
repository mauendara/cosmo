"""`notify.setup` -- the Telegram API helpers behind `cosmo notify config`'s
interactive wizard. Unlike `TelegramSink.send`'s best-effort posture, every
function here raises `TelegramApiError` on a real failure, so tested against
a fake `urlopen` covering both the happy path and each failure shape."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from cosmo.notify.setup import TelegramApiError, discover_chat_id, send_test_message


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


@contextmanager
def _urlopen_returning(body: dict[str, Any]):  # type: ignore[no-untyped-def]
    with patch("urllib.request.urlopen", return_value=_FakeResponse(body)) as mock:
        yield mock


def test_discover_chat_id_returns_none_when_no_messages_yet() -> None:
    with _urlopen_returning({"ok": True, "result": []}):
        assert discover_chat_id("token") is None


def test_discover_chat_id_returns_the_most_recent_chat() -> None:
    body = {
        "ok": True,
        "result": [
            {"message": {"chat": {"id": 111}}},
            {"message": {"chat": {"id": 222}}},
        ],
    }
    with _urlopen_returning(body):
        assert discover_chat_id("token") == "222"


def test_discover_chat_id_raises_on_a_rejected_token() -> None:
    with (
        _urlopen_returning({"ok": False, "description": "Unauthorized"}),
        pytest.raises(TelegramApiError, match="Unauthorized"),
    ):
        discover_chat_id("bad-token")


def test_discover_chat_id_raises_on_a_network_failure() -> None:
    with (
        patch("urllib.request.urlopen", side_effect=OSError("connection refused")),
        pytest.raises(TelegramApiError, match="could not reach"),
    ):
        discover_chat_id("token")


def test_send_test_message_succeeds_silently_on_ok() -> None:
    with _urlopen_returning({"ok": True, "result": {}}):
        send_test_message("token", "123")  # must not raise


def test_send_test_message_raises_on_a_bad_chat_id() -> None:
    with (
        _urlopen_returning({"ok": False, "description": "chat not found"}),
        pytest.raises(TelegramApiError, match="chat not found"),
    ):
        send_test_message("token", "wrong-chat-id")
