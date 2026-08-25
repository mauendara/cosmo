"""Core preflight checks -- harness-agnostic by construction.

Nothing in this module may name a specific harness, its binary, or its
environment variables. Harness-specific preconditions are obtained by calling
`preflight()` on the resolved adapter (spec 2). A test enforces this boundary.

`git`, `docker`, `openspec` and `gitleaks` are core rather than harness-specific:
Cosmo calls OpenSpec's CLI itself (spec 10.4), the validation gate invokes Docker
directly, bypassing the harness entirely (spec 2.2), and the gitleaks pre-commit
hook (spec 6.1) is installed by Cosmo's own worktree lifecycle, not by an adapter.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from cosmo.checks import CheckResult, check_executable, fail, ok, warn
from cosmo.config import CosmoConfig
from cosmo.store.connection import connect_reader
from cosmo.store.migrations import current_version, latest_version


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


def check_database(config: CosmoConfig) -> CheckResult:
    """Spec 8: the event/state store must be readable and at the schema
    version this build expects. A database that does not exist yet is not a
    failure -- `StoreWriter` creates and migrates it on first write -- but a
    stale or unreadable one would silently misbehave for every later phase.
    """
    db_path = config.paths.db_path
    if not db_path.exists():
        return ok(
            "event/state store", f"not yet created at {db_path} -- initializes on first write"
        )

    try:
        conn = connect_reader(db_path)
    except sqlite3.OperationalError as exc:
        return fail("event/state store", f"cannot open {db_path}: {exc}")
    try:
        version = current_version(conn)
    except sqlite3.OperationalError as exc:
        return fail("event/state store", f"cannot read schema version from {db_path}: {exc}")
    finally:
        conn.close()

    latest = latest_version()
    if version < latest:
        return fail(
            "event/state store",
            f"schema at version {version}, this build expects {latest} "
            f"-- run a command that writes to trigger migration",
        )
    if version > latest:
        return warn(
            "event/state store",
            f"schema at version {version} is newer than this build expects ({latest})",
        )
    return ok("event/state store", f"{db_path} at schema version {version}")


def check_no_leaked_gate_containers(*, docker_bin: str = "docker") -> CheckResult:
    """Spec 2.4 steps 4-5: an orchestrator-labeled container still running
    means a previous run's reap (proc.reap.cancel_and_reap) didn't fully
    clean up, or Cosmo was killed before it could try. A warning, not a
    blocker -- a human may be intentionally inspecting a live container --
    but silently starting a new run on top of one is how a leaked pool
    poisons the next task (spec 6.5).
    """
    if shutil.which(docker_bin) is None:
        return ok("leaked gate containers", "docker not on PATH -- see the docker check above")
    try:
        result = subprocess.run(
            [docker_bin, "ps", "-q", "--filter", "label=orchestrator.run_id"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return warn("leaked gate containers", f"could not query docker: {exc}")

    if result.returncode != 0:
        # A non-zero exit means stdout is not a container-id list -- it may
        # be an error banner (observed: the WSL2 Docker Desktop shim prints
        # its "could not be found" message to stdout, not stderr, on exit 1).
        # Parsing that as ids would misreport an unrelated docker problem as
        # leaked containers.
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        return warn("leaked gate containers", f"docker ps failed: {detail}")

    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if ids:
        return warn(
            "leaked gate containers",
            f"{len(ids)} orchestrator-labeled container(s) still running: {', '.join(ids)}",
        )
    return ok("leaked gate containers", "none found")


def core_checks(config: CosmoConfig) -> list[CheckResult]:
    return [
        check_python(),
        check_executable("git", "git", "worktree isolation and merges"),
        check_executable("docker", "docker", "the validation gate"),
        check_executable("openspec", "openspec", "the propose/apply flow"),
        check_executable("gitleaks", "gitleaks", "the worktree secret-commit guardrail (spec 6.1)"),
        check_disk(config),
        check_paths_writable(config),
        check_work_dir_filesystem(config),
        check_database(config),
        check_no_leaked_gate_containers(),
    ]
