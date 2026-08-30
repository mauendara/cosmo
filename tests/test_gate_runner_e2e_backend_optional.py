"""`_e2e_stage`'s backend-optional branch (`src/cosmo/gate/runner.py`): a
missing `backend_dir` must not skip e2e outright -- only a missing
`frontend_dir` does. Regression coverage for the bug found writing the
`vite-react-local` project template: a frontend-only repo has no `backend/`
at all, and the stage used to silently report `passed=True` with zero tests
run for that case, which is indistinguishable from a repo that genuinely has
no e2e suite.

Uses `fake_gate_docker.sh` (same fake-the-external-process posture as
`test_gate_docker_runner.py`) plus a real local `http.server` standing in for
the container the fake docker never actually starts -- mirrors
`test_wait_for_http_succeeds_against_a_real_local_server`'s trick, since
`wait_for_http` makes a real HTTP call regardless of what `docker` binary is
configured.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from cosmo.config import load_config
from cosmo.config.model import GateConfig
from cosmo.gate import runner

FAKE_DOCKER = str(Path(__file__).parent / "fixtures" / "fake_gate_docker.sh")


@pytest.fixture
def gate() -> GateConfig:
    return load_config(config_path=Path("/nonexistent/config.toml")).gate


@pytest.fixture
def local_server() -> Iterator[http.server.HTTPServer]:
    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_e2e_runs_frontend_only_when_backend_dir_is_absent(
    tmp_path: Path,
    gate: GateConfig,
    monkeypatch: pytest.MonkeyPatch,
    local_server: http.server.HTTPServer,
) -> None:
    (tmp_path / "frontend").mkdir()
    # Deliberately no "backend" dir -- the frontend-only scenario.

    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_PORT_OUTPUT", str(local_server.server_address[1]))

    stage, flaky, quarantined = runner._e2e_stage(
        worktree_path=tmp_path,
        gate=gate,
        run_id="run-1",
        task_id="task-1",
        docker_bin=FAKE_DOCKER,
        db_path=None,
    )

    logged = log.read_text()
    assert "spring-boot:run" not in logged
    assert "VITE_BACKEND_URL" not in logged
    assert "npm ci && npm run build" in logged
    assert "-frontend" in logged

    # No playwright report was actually produced by the fake docker script,
    # so the stage itself still fails -- what matters here is that it got
    # far enough to look for one, i.e. it did not skip e2e outright the way
    # the pre-fix code did for any backend-less repo.
    assert stage.error_summary == "playwright produced no report"
    assert flaky == []
    assert quarantined == []


def test_e2e_still_starts_backend_when_backend_dir_is_present(
    tmp_path: Path,
    gate: GateConfig,
    monkeypatch: pytest.MonkeyPatch,
    local_server: http.server.HTTPServer,
) -> None:
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend").mkdir()

    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    monkeypatch.setenv("FAKE_DOCKER_PORT_OUTPUT", str(local_server.server_address[1]))

    stage, _flaky, _quarantined = runner._e2e_stage(
        worktree_path=tmp_path,
        gate=gate,
        run_id="run-1",
        task_id="task-1",
        docker_bin=FAKE_DOCKER,
        db_path=None,
    )

    logged = log.read_text()
    assert "spring-boot:run" in logged
    assert "VITE_BACKEND_URL" in logged
    assert stage.error_summary == "playwright produced no report"


def test_e2e_skips_entirely_when_frontend_dir_is_absent(
    tmp_path: Path,
    gate: GateConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log = tmp_path / "docker.log"
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))

    stage, flaky, quarantined = runner._e2e_stage(
        worktree_path=tmp_path,
        gate=gate,
        run_id="run-1",
        task_id="task-1",
        docker_bin=FAKE_DOCKER,
        db_path=None,
    )

    assert stage.passed
    assert stage.counts is None
    assert not log.exists()
    assert flaky == []
    assert quarantined == []
