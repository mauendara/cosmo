"""systemd `sd_notify` integration (spec 9.5): `WatchdogSec` pings and a
`READY=1` on startup, without the `sdnotify`/`systemd-python` dependency --
the wire protocol is one `AF_UNIX SOCK_DGRAM` datagram to the path in
`$NOTIFY_SOCKET`, simple enough that a dependency for it isn't worth it.

Harness-agnostic and core-agnostic by construction, matching `checks.py`'s
own stance: this module knows nothing about `cosmo.run` and is safe to
import from anywhere.
"""

from __future__ import annotations

import os
import socket


def notify(*, ready: bool = False, watchdog: bool = False, status: str | None = None) -> bool:
    """Sends an `sd_notify` datagram if `$NOTIFY_SOCKET` is set (running
    under systemd with `Type=notify`/`WatchdogSec`); a silent no-op
    everywhere else -- a bare CLI invocation, a test, a non-systemd
    deployment. Returns whether a datagram was actually sent, so a caller
    can assert on it in a test without needing a real systemd around.
    """
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False

    fields = []
    if ready:
        fields.append("READY=1")
    if watchdog:
        fields.append("WATCHDOG=1")
    if status is not None:
        fields.append(f"STATUS={status}")
    if not fields:
        return False

    # systemd's abstract-namespace convention: a leading '@' means the
    # first byte of the real address is NUL, not a literal '@'.
    if address.startswith("@"):
        address = "\0" + address[1:]

    message = "\n".join(fields).encode()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.connect(address)
        sock.sendall(message)
    except OSError:
        # A stale/misconfigured $NOTIFY_SOCKET must never take the run
        # down with it -- this is a best-effort liveness signal, not a
        # correctness dependency.
        return False
    finally:
        sock.close()
    return True
