"""`watchdog.notify` -- the systemd `sd_notify` datagram (Phase 9, spec 9.5).
A real `AF_UNIX SOCK_DGRAM` socket stands in for systemd's own notification
socket, so this is a real wire-protocol test, not a mock of `socket`."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from cosmo.watchdog import notify


@pytest.fixture
def notify_socket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[socket.socket]:
    sock_path = tmp_path / "notify.sock"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(sock_path))
    sock.settimeout(1.0)
    monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))
    yield sock
    sock.close()


def test_watchdog_ping_sends_the_expected_datagram(notify_socket: socket.socket) -> None:
    sent = notify(watchdog=True)

    assert sent is True
    data, _ = notify_socket.recvfrom(1024)
    assert data == b"WATCHDOG=1"


def test_ready_and_status_combine_into_one_datagram(notify_socket: socket.socket) -> None:
    notify(ready=True, status="running")

    data, _ = notify_socket.recvfrom(1024)
    lines = data.decode().split("\n")
    assert "READY=1" in lines
    assert "STATUS=running" in lines


def test_no_notify_socket_env_is_a_silent_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert notify(watchdog=True) is False


def test_no_flags_set_sends_nothing(notify_socket: socket.socket) -> None:
    assert notify() is False
    with pytest.raises(TimeoutError):
        notify_socket.recvfrom(1024)


def test_a_bad_notify_socket_path_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "does-not-exist.sock"))
    assert notify(watchdog=True) is False
