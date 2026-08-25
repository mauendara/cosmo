"""`ManagedProcess`: process-group kill semantics (spec 2.4 steps 1-3, 6)."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from cosmo.proc.managed import ManagedProcess

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "spawn_ignoring_grandchild.sh"


def _read_grandchild_pid(log_path: Path, *, timeout: float = 2.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if log_path.exists():
            content = log_path.read_text().strip()
            if content:
                return int(content.splitlines()[0])
        time.sleep(0.02)
    raise AssertionError(f"grandchild pid never appeared in {log_path}")


def test_grandchild_ignoring_sigterm_is_fully_reaped_via_sigkill(tmp_path: Path) -> None:
    """The reap must not stop at the direct child exiting -- the grandchild,
    reparented once its immediate parent dies, has to be found and killed
    through the process group, not the parent-child relationship."""
    log_path = tmp_path / "raw.log"
    mp = ManagedProcess(["sh", str(FIXTURE)], raw_log_path=log_path)
    grandchild_pid = _read_grandchild_pid(log_path)

    # Confirm it's actually alive before we claim to have reaped it.
    os.kill(grandchild_pid, 0)

    reaped = mp.cancel(grace_s=0.3)

    assert reaped is True
    with pytest.raises(ProcessLookupError):
        os.kill(grandchild_pid, 0)


def test_cancel_on_a_cooperative_process_succeeds_within_grace(tmp_path: Path) -> None:
    """A child that dies on plain SIGTERM should not need the SIGKILL path
    at all -- cancel() must not wait out the full grace period regardless."""
    mp = ManagedProcess(["sleep", "30"], raw_log_path=tmp_path / "raw.log")
    started = time.monotonic()

    reaped = mp.cancel(grace_s=20.0)

    assert reaped is True
    assert time.monotonic() - started < 5.0


def test_cancel_on_an_already_exited_process_returns_true(tmp_path: Path) -> None:
    mp = ManagedProcess(["true"], raw_log_path=tmp_path / "raw.log")
    mp.wait(timeout=2.0)

    assert mp.cancel(grace_s=1.0) is True


def test_stdout_and_stderr_are_drained_to_the_raw_log(tmp_path: Path) -> None:
    log_path = tmp_path / "raw.log"
    mp = ManagedProcess(
        ["sh", "-c", "echo out-line; echo err-line >&2"],
        raw_log_path=log_path,
    )
    mp.wait(timeout=2.0)
    mp.cancel(grace_s=1.0)

    content = log_path.read_text()
    assert "out-line" in content
    assert "err-line" in content
