"""Raw `docker` subprocess invocations backing every gate stage (spec 1.1,
1.2). Mirrors `proc.orphans`'s posture toward `docker`: shell out directly,
inject `docker_bin` for tests, never assume a Python docker client is
installed.

`LABEL_RUN_ID`/`LABEL_TASK_ID` intentionally match the literal label keys
`proc.orphans.sweep_containers` already filters on -- those labels existed
since Phase 2 with nothing to attach them, and this module is their first
real writer.
"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from cosmo.config.model import GateConfig

LABEL_RUN_ID = "orchestrator.run_id"
LABEL_TASK_ID = "orchestrator.task_id"


def container_labels(run_id: str, task_id: str) -> list[str]:
    return [
        "--label",
        f"{LABEL_RUN_ID}={run_id}",
        "--label",
        f"{LABEL_TASK_ID}={task_id}",
    ]


def container_flags(gate: GateConfig, run_id: str, task_id: str) -> list[str]:
    """Spec 1.1's non-negotiable flags. Applied to every gate-launched
    container uniformly (build/unit/e2e), not only the Chromium-driving e2e
    one -- the memory/shm rationale is specific to Chromium, but a larger
    `/dev/shm` never hurts a Maven or npm build, and one code path beats a
    branch that only some callers remember to take."""
    return ["--ipc=host", f"--shm-size={gate.shm_size}", *container_labels(run_id, task_id)]


@dataclass(frozen=True, slots=True)
class ContainerRun:
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool


def run_container(
    *,
    image: str,
    workdir_mount: Path,
    container_workdir: str,
    command: list[str],
    gate: GateConfig,
    run_id: str,
    task_id: str,
    docker_bin: str = "docker",
    extra_env: dict[str, str] | None = None,
    network: str | None = None,
    extra_flags: list[str] | None = None,
) -> ContainerRun:
    """One foreground, `--rm` container run to completion -- the shape build,
    unit, and the final Playwright-test-execution step all share."""
    argv = [
        docker_bin,
        "run",
        "--rm",
        *container_flags(gate, run_id, task_id),
        "-v",
        f"{workdir_mount}:{container_workdir}",
        "-w",
        container_workdir,
    ]
    for key, value in (extra_env or {}).items():
        argv += ["-e", f"{key}={value}"]
    if network:
        argv += ["--network", network]
    if extra_flags:
        argv += extra_flags
    argv += [image, *command]

    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=gate.stage_timeout_seconds,
            check=False,
        )
        return ContainerRun(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_seconds=time.monotonic() - start,
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        return ContainerRun(
            exit_code=-1,
            stdout=_decode(exc.stdout),
            stderr=_decode(exc.stderr),
            duration_seconds=time.monotonic() - start,
            timed_out=True,
        )


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else value.decode(errors="replace")


def create_network(name: str, *, docker_bin: str = "docker") -> None:
    subprocess.run([docker_bin, "network", "create", name], capture_output=True, check=False)


def remove_network(name: str, *, docker_bin: str = "docker") -> None:
    subprocess.run([docker_bin, "network", "rm", name], capture_output=True, check=False)


def run_detached_service(
    *,
    name: str,
    image: str,
    workdir_mount: Path,
    container_workdir: str,
    command: list[str],
    gate: GateConfig,
    run_id: str,
    task_id: str,
    network: str,
    docker_bin: str = "docker",
    extra_env: dict[str, str] | None = None,
    publish_container_port: int | None = None,
) -> None:
    """Starts a long-lived service container (backend or frontend, during the
    e2e stage) in the background. Caller is responsible for `stop_service`
    in a `finally` -- there is no `--rm` here because a crashed gate process
    must not silently orphan it; that is exactly what `proc.orphans
    .sweep_containers` exists to clean up via these same labels."""
    argv = [
        docker_bin,
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "--network-alias",
        name,
        *container_flags(gate, run_id, task_id),
        "-v",
        f"{workdir_mount}:{container_workdir}",
        "-w",
        container_workdir,
    ]
    if publish_container_port is not None:
        argv += ["-p", f"0:{publish_container_port}"]
    for key, value in (extra_env or {}).items():
        argv += ["-e", f"{key}={value}"]
    argv += [image, *command]
    subprocess.run(argv, capture_output=True, text=True, check=False)


def stop_service(name: str, *, docker_bin: str = "docker") -> None:
    subprocess.run([docker_bin, "rm", "-f", name], capture_output=True, check=False)


def service_logs(name: str, *, docker_bin: str = "docker") -> str:
    proc = subprocess.run([docker_bin, "logs", name], capture_output=True, text=True, check=False)
    return proc.stdout + proc.stderr


def published_port(name: str, container_port: int, *, docker_bin: str = "docker") -> int | None:
    """Resolves the host port Docker assigned to a `-p 0:<container_port>`
    binding -- dynamic allocation avoids port collisions if the gate is ever
    invoked concurrently, since spec 1.2 only guarantees serial ordering
    *within* one gate run, not across tasks."""
    proc = subprocess.run(
        [docker_bin, "port", name, str(container_port)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    # Output looks like "0.0.0.0:54321" (one line per bound address).
    last = proc.stdout.strip().splitlines()[-1]
    try:
        return int(last.rsplit(":", 1)[-1])
    except ValueError:
        return None


def wait_for_http(url: str, *, timeout_seconds: float, poll_interval: float = 1.0) -> bool:
    """Polls `url` until it returns any HTTP response (even a 4xx/5xx counts
    -- this is a liveness check, not a correctness check) or the deadline
    passes. Used to know when the e2e stage's backend/frontend containers
    are actually ready before starting Playwright against them."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)  # noqa: S310 -- gate-internal, not user input
            return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(poll_interval)
    return False
