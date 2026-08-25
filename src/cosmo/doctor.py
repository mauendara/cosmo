"""Core preflight checks -- harness-agnostic by construction.

Nothing in this module may name a specific harness, its binary, or its
environment variables. Harness-specific preconditions are obtained by calling
`preflight()` on the resolved adapter (spec 2). A test enforces this boundary.

`git`, `docker` and `openspec` are core rather than harness-specific: Cosmo calls
OpenSpec's CLI itself (spec 10.4), and the validation gate invokes Docker directly,
bypassing the harness entirely (spec 2.2).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from cosmo.checks import CheckResult, check_executable, fail, ok, warn
from cosmo.config import CosmoConfig


def check_disk(config: CosmoConfig) -> CheckResult:
    """Spec 9.5: a full disk fails every subsequent task in a way that reads as
    a code error, so the run aborts before it starts rather than during."""
    target = config.paths.data_dir
    probe = target
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent

    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return fail("disk space", f"cannot stat {probe}: {exc}")

    free_gb = usage.free / 1024**3
    floor = config.disk.min_free_gb
    detail = f"{free_gb:.1f} GB free at {probe} (floor {floor:.1f} GB)"
    return ok("disk space", detail) if free_gb >= floor else fail("disk space", detail)


def check_work_dir_filesystem(config: CosmoConfig) -> CheckResult:
    """Spec 1: under WSL2, keep the working repo inside the WSL2 filesystem.

    A worktree on /mnt/c goes through the 9p bridge, where Maven and node_modules
    I/O is slow enough to distort every timeout in section 3.3.
    """
    work = config.paths.work_dir
    if work.is_absolute() and work.parts[:2] == ("/", "mnt"):
        return warn(
            "work dir filesystem",
            f"{work} is on a Windows drive mount; builds there are slow enough to "
            f"distort the section 3.3 timeouts. Prefer a path inside the WSL2 filesystem.",
        )
    return ok("work dir filesystem", str(work))


def check_python() -> CheckResult:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < (3, 12):
        return fail("python", f"{detail} -- 3.12+ required")
    return ok("python", detail)


def check_paths_writable(config: CosmoConfig) -> CheckResult:
    """Cosmo must be able to create its own state directories without root.

    This is why the path defaults are XDG rather than the spec's /var/cosmo: a
    droplet overrides them via config, a dev box needs no sudo.
    """
    failures: list[str] = []
    for label, path in (
        ("data_dir", config.paths.data_dir),
        ("work_dir", config.paths.work_dir),
        ("log_dir", config.paths.log_dir),
    ):
        probe: Path = path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        import os

        if not os.access(probe, os.W_OK):
            failures.append(f"{label}={path} (blocked at {probe})")

    if failures:
        return fail("state dirs writable", "; ".join(failures))
    return ok("state dirs writable", f"{config.paths.data_dir} and siblings")


def core_checks(config: CosmoConfig) -> list[CheckResult]:
    return [
        check_python(),
        check_executable("git", "git", "worktree isolation and merges"),
        check_executable("docker", "docker", "the validation gate"),
        check_executable("openspec", "openspec", "the propose/apply flow"),
        check_disk(config),
        check_paths_writable(config),
        check_work_dir_filesystem(config),
    ]
