"""`ManagedProcess`: spec 2.4 steps 1-3.

On POSIX, signaling only a process's own PID leaves its children -- Maven,
Node/Vite, `docker` clients, Playwright's Chromium -- re-parented to init on
timeout, where they keep running, holding ports and memory. `start_new_session`
puts every harness process in its own process group and session so a later
`killpg` reaches the whole tree, not just the direct child.

The escalation logic below intentionally does not declare victory the moment
`Popen.wait()` returns: that only reaps *our* direct child. A grandchild that
traps `SIGTERM` survives its immediate parent's death and is still very much
alive in the same process group. Reaping is only "done" once `killpg(pgid, 0)`
itself raises `ProcessLookupError` -- proof the whole group is gone -- which is
also step 6's signal that a reap failed if SIGKILL couldn't achieve it either.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import IO

# Not spec-mandated -- SIGKILL cannot be blocked or ignored, so this only
# bounds how long we poll for the kernel to finish tearing the group down.
_POST_SIGKILL_TIMEOUT_S = 5.0
_POLL_INTERVAL_S = 0.05


def _pgid_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A process we can signal-check but not signal-kill still counts as
        # alive -- we simply can't prove it's gone.
        return True
    return True


class ManagedProcess:
    def __init__(
        self,
        argv: list[str],
        *,
        raw_log_path: Path,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        on_stdout_chunk: Callable[[bytes], None] | None = None,
    ) -> None:
        raw_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_lock = threading.Lock()
        self._log_file = raw_log_path.open("wb")
        self._proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # spec 2.4 step 1
        )
        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        self._drain_threads = [
            threading.Thread(
                target=self._drain, args=(self._proc.stdout, on_stdout_chunk), daemon=True
            ),
            threading.Thread(target=self._drain, args=(self._proc.stderr, None), daemon=True),
        ]
        for t in self._drain_threads:
            t.start()

    def _drain(self, pipe: IO[bytes], on_chunk: Callable[[bytes], None] | None) -> None:
        # Runs on its own thread so a slow or silent child never blocks
        # whatever the caller's thread is doing -- the "non-blocking" in
        # the plan's "non-blocking stdout/stderr drain". `os.read` on the
        # raw fd, not `pipe.read(4096)`: a `BufferedReader` blocks until it
        # fills the requested size or hits EOF, so a few bytes followed by
        # silence -- exactly a heartbeat line -- would sit unflushed for
        # however long the harness stays quiet.
        #
        # `on_chunk` (stdout only) is a tee, not a second reader of the fd: a
        # harness adapter's own structured-event reader (Phase 3) needs the
        # same bytes this thread is already pulling off the pipe, and a
        # second consumer of the same fd would race it for data. Called
        # synchronously on this thread, so a caller that joins
        # `_drain_threads` (see `_finalize`) has a genuine happens-before
        # relationship with the last `on_chunk` call -- no separate lock
        # needed for whatever `on_chunk` accumulates.
        fd = pipe.fileno()
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            with self._log_lock:
                self._log_file.write(chunk)
                self._log_file.flush()
            if on_chunk is not None:
                on_chunk(chunk)
        pipe.close()

    @property
    def pid(self) -> int:
        return self._proc.pid

    def poll(self) -> int | None:
        return self._proc.poll()

    def wait(self, timeout: float | None = None) -> int:
        return self._proc.wait(timeout=timeout)

    def cancel(self, *, grace_s: float) -> bool:
        """spec 2.4 steps 2-3-6. SIGTERM the group, wait up to `grace_s` for
        every member to exit, SIGKILL the survivors, wait a bounded time more.
        Returns True once the whole process group is confirmed gone, False if
        it still has a survivor after SIGKILL -- the reap-failure case step 6
        wants surfaced."""
        try:
            pgid = os.getpgid(self._proc.pid)
        except ProcessLookupError:
            self._finalize()
            return True

        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            self._finalize()
            return True

        if self._wait_for_group_empty(pgid, timeout=grace_s):
            self._finalize()
            return True

        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            self._finalize()
            return True

        reaped_clean = self._wait_for_group_empty(pgid, timeout=_POST_SIGKILL_TIMEOUT_S)
        self._finalize()
        return reaped_clean

    def _wait_for_group_empty(self, pgid: int, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            # Reap our own direct child the moment it exits. Without this,
            # a child with no surviving descendants of its own sits as a
            # zombie forever -- only its real parent (us) can reap it, and
            # nothing else ever will, so `_pgid_alive` would see it as
            # "still there" until the heat death of the universe rather
            # than until the deadline.
            self._proc.poll()
            if not _pgid_alive(pgid):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_POLL_INTERVAL_S)

    def _finalize(self) -> None:
        # Reap our own direct child last, once the group is confirmed empty
        # (or we've given up waiting) -- reaping early would let the kernel
        # recycle the pid, which is also the pgid, out from under
        # `_wait_for_group_empty`'s polling.
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._proc.wait(timeout=1.0)
        for t in self._drain_threads:
            t.join(timeout=5.0)
        with self._log_lock:
            self._log_file.close()
