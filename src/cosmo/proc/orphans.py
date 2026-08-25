"""Orphan sweep: spec 2.4 steps 4-5, run after `ManagedProcess.cancel()`.

Two independent sources of leaks after a kill:

- A gate container Docker never got the memo about, found only because step 5
  requires every gate container to carry `orchestrator.run_id`/`.task_id`
  labels -- without them this sweep would have nothing to filter on.
- A process that escaped the process group entirely (e.g. by calling
  `setsid()` itself) and is still holding the worktree path open. `killpg`
  cannot reach it, so it is only detected, never killed, and logged as
  `critical` for a human to look at (spec 2.4 step 4).

`docker_bin` is injectable so tests run against a recording fake script
rather than a live daemon -- the same posture the plan takes toward
`claude -p` (a real gate container is Phase 6's concern, not Phase 2's).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SweepResult:
    removed_containers: list[str] = field(default_factory=list)
    worktree_holder_pids: list[int] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.worktree_holder_pids


def sweep_containers(run_id: str, task_id: str, *, docker_bin: str = "docker") -> list[str]:
    """`docker ps -q --filter label=orchestrator.run_id=... --filter
    label=orchestrator.task_id=...` then `docker rm -f`. Returns the
    container ids force-removed."""
    listed = subprocess.run(
        [
            docker_bin,
            "ps",
            "-q",
            "--filter",
            f"label=orchestrator.run_id={run_id}",
            "--filter",
            f"label=orchestrator.task_id={task_id}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if listed.returncode != 0:
        # A non-zero exit means stdout is not a container-id list -- it may
        # be an error banner (observed: the WSL2 Docker Desktop shim prints
        # its "could not be found" message to stdout, not stderr, on exit
        # 1). Parsing that as ids would try to `docker rm -f` garbage.
        return []
    ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not ids:
        return []
    subprocess.run([docker_bin, "rm", "-f", *ids], capture_output=True, text=True, check=False)
    return ids


def find_worktree_holders(worktree_path: Path) -> list[int]:
    """Scan `/proc` for processes whose cwd or an open fd points inside
    `worktree_path`. POSIX-only, matching spec 2.4's own framing ("On
    POSIX..."). Best-effort: a process we can't introspect (permission
    denied, or it exited mid-scan) is silently skipped rather than crashing
    the sweep -- a sweep that raises on a stray daemon defeats its purpose.
    """
    target = str(worktree_path.resolve())
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return []

    own_pid = os.getpid()
    holders: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == own_pid:
            continue
        if _holds_path(entry, target):
            holders.append(pid)
    return holders


def _holds_path(proc_entry: Path, target: str) -> bool:
    try:
        cwd = os.readlink(proc_entry / "cwd")
    except OSError:
        cwd = None
    if cwd is not None and (cwd == target or cwd.startswith(target + "/")):
        return True

    try:
        fds = list((proc_entry / "fd").iterdir())
    except OSError:
        return False
    for fd in fds:
        try:
            link = os.readlink(fd)
        except OSError:
            continue
        if link == target or link.startswith(target + "/"):
            return True
    return False


def sweep(
    run_id: str, task_id: str, worktree_path: Path, *, docker_bin: str = "docker"
) -> SweepResult:
    removed = sweep_containers(run_id, task_id, docker_bin=docker_bin)
    holders = find_worktree_holders(worktree_path)
    return SweepResult(removed_containers=removed, worktree_holder_pids=holders)
