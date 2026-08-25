"""Docker invocation mechanics (spec 1.1) against `fake_gate_docker.sh` --
mirrors the plan's stance on `docker` (Phase 2's `fake_docker.sh`): fake the
external process, test the argv/parsing mechanics, not a real daemon.
"""

from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from cosmo.config import load_config
from cosmo.config.model import GateConfig
from cosmo.gate import docker_runner

FAKE_DOCKER = str(Path(__file__).parent / "fixtures" / "fake_gate_docker.sh")


@pytest.fixture
def gate() -> GateConfig:
    return load_config(config_path=Path("/nonexistent/config.toml")).gate


def test_container_flags_include_the_non_negotiable_labels_and_ipc(gate: GateConfig) -> None:
    flags = docker_runner.container_flags(gate, "run-1", "task-1")
    assert "--ipc=host" in flags
    assert f"--shm-size={gate.shm_size}" in flags
    assert "orchestrator.run_id=run-1" in flags
    assert "orchestrator.task_id=task-1" in flags


def test_run_container_builds_expected_argv(
    tmp_path: Path, gate: GateConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    result = docker_runner.run_container(
        image="maven:test",
        workdir_mount=tmp_path,
        container_workdir="/work",
        command=["mvn", "test"],
        gate=gate,
        run_id="run-1",
        task_id="task-1",
        docker_bin=FAKE_DOCKER,
    )
    assert result.exit_code == 0
    assert not result.timed_out
    logged = log.read_text()
    assert "run" in logged
    assert "--ipc=host" in logged
    assert f"{tmp_path}:/work" in logged
    assert "maven:test mvn test" in logged


def test_run_container_reports_nonzero_exit(
    tmp_path: Path, gate: GateConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
    monkeypatch.setenv("FAKE_DOCKER_EXIT", "1")
    monkeypatch.setenv("FAKE_DOCKER_STDERR", "build failed\n")
    result = docker_runner.run_container(
        image="maven:test",
        workdir_mount=tmp_path,
        container_workdir="/work",
        command=["mvn", "test"],
        gate=gate,
        run_id="run-1",
        task_id="task-1",
        docker_bin=FAKE_DOCKER,
    )
    assert result.exit_code == 1
    assert "build failed" in result.stderr


def test_published_port_parses_docker_port_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
    monkeypatch.setenv("FAKE_DOCKER_PORT_OUTPUT", "0.0.0.0:54321")
    port = docker_runner.published_port("some-container", 8080, docker_bin=FAKE_DOCKER)
    assert port == 54321


def test_published_port_returns_none_when_unbound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp_path / "docker.log"))
    monkeypatch.delenv("FAKE_DOCKER_PORT_OUTPUT", raising=False)
    port = docker_runner.published_port("some-container", 8080, docker_bin=FAKE_DOCKER)
    assert port is None


def test_wait_for_http_succeeds_against_a_real_local_server() -> None:
    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert docker_runner.wait_for_http(
            f"http://127.0.0.1:{port}/", timeout_seconds=5.0, poll_interval=0.1
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_wait_for_http_gives_up_after_timeout() -> None:
    # Nothing listens on this port -- connection refused every poll.
    assert not docker_runner.wait_for_http(
        "http://127.0.0.1:1/", timeout_seconds=0.3, poll_interval=0.1
    )
