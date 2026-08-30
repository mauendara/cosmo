"""Real Docker gate runs against the Phase 6 fixture repo
(`tests/fixtures/gate_repo`) -- the plan's own exit criterion: "a fixture
Java+Spring / Vite+React repo produces a full structured result," exercised
for build failure, unit failure, e2e failure, an injected flaky test, and a
deliberately weakened test, plus the gitleaks backstop.

Every one of these scenarios was run by hand against a real `docker` daemon
before this file was written (the same discipline every previous phase's
handoff describes) -- this file encodes those same scenarios as regression
tests, not new ones invented for coverage's sake.

**Opt-in, not on-by-default**: unlike `test_git_secrets.py`'s real-gitleaks
tests (sub-second), a full gate run through real Maven/npm/Playwright
containers takes minutes even on a warm image/dependency cache, and the
first run on a cold cache is much slower (image pulls, Maven Central, npm
registry). Running this on every `./check.sh` would make the fast local
loop unusable. Set `COSMO_GATE_DOCKER_E2E=1` to opt in (CI's own nightly/
manual real-gate job, or a developer verifying gate changes by hand);
skipped otherwise, same as the rest of the suite staying fast.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cosmo.config import load_config
from cosmo.config.model import GateConfig
from cosmo.gate.runner import run_validation_gate

FIXTURE_REPO = Path(__file__).parent / "fixtures" / "gate_repo"
AUTHOR = ("Cosmo Test", "cosmo-test@example.com")

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None or os.environ.get("COSMO_GATE_DOCKER_E2E") != "1",
    reason="real gate run against real docker -- opt in with COSMO_GATE_DOCKER_E2E=1",
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            f"user.name={AUTHOR[0]}",
            "-c",
            f"user.email={AUTHOR[1]}",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def _fixture_repo_on_develop(tmp_path: Path) -> Path:
    repo = tmp_path / "gate-repo"
    shutil.copytree(FIXTURE_REPO, repo)
    _git(repo, "init", "-q", "-b", "develop")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial fixture")
    return repo


def _gate_config() -> GateConfig:
    return load_config(config_path=Path("/nonexistent/config.toml")).gate


def test_green_run_produces_a_full_passing_structured_result(tmp_path: Path) -> None:
    repo = _fixture_repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/green-1")
    controller = repo / "backend/src/main/java/com/cosmo/fixture/HelloController.java"
    controller.write_text(controller.read_text() + "\n// no-op change\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "green task")

    result = run_validation_gate(
        task_id="green-1",
        run_id="test-run",
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/green-1",
        allow_test_edits=False,
        gate=_gate_config(),
    )
    assert result.passed
    assert result.build is not None and result.build.passed
    assert result.unit is not None and result.unit.passed
    assert result.unit.counts is not None and result.unit.counts.failed == 0
    assert result.e2e is not None and result.e2e.passed


def test_compile_failure_is_classified_code_error_build(tmp_path: Path) -> None:
    repo = _fixture_repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/compile-fail")
    controller = repo / "backend/src/main/java/com/cosmo/fixture/HelloController.java"
    controller.write_text(controller.read_text().replace("return Map.of", "BROKEN return Map.of"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "compile failure")

    result = run_validation_gate(
        task_id="compile-fail",
        run_id="test-run",
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/compile-fail",
        allow_test_edits=False,
        gate=_gate_config(),
    )
    assert not result.passed
    assert result.failure_type is not None and result.failure_type.value == "code_error"
    assert result.failure_stage is not None and result.failure_stage.value == "build"
    assert "COMPILATION ERROR" in (result.error_detail or "")


def test_unit_failure_names_the_failing_test_and_assertion(tmp_path: Path) -> None:
    repo = _fixture_repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/unit-fail")
    controller = repo / "backend/src/main/java/com/cosmo/fixture/HelloController.java"
    controller.write_text(
        controller.read_text().replace(
            '"hello from cosmo gate fixture"', '"totally different text"'
        )
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "unit failure")

    result = run_validation_gate(
        task_id="unit-fail",
        run_id="test-run",
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/unit-fail",
        allow_test_edits=False,
        gate=_gate_config(),
    )
    assert not result.passed
    assert result.failure_stage is not None and result.failure_stage.value == "unit_tests"
    assert result.unit is not None
    assert result.unit.counts is not None and result.unit.counts.failed == 1
    assert len(result.unit.failing_tests) == 1
    assert "greetReturnsExpectedMessage" in result.unit.failing_tests[0].test_id


def test_weakened_test_is_caught_by_the_diff_gate_before_any_container_runs(
    tmp_path: Path,
) -> None:
    repo = _fixture_repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/weakened-test")
    test_file = repo / "backend/src/test/java/com/cosmo/fixture/HelloControllerTest.java"
    weakened = test_file.read_text().replace(
        '        assertThat(greeting).startsWith("hello");\n', ""
    )
    test_file.write_text(weakened)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "weaken a test")

    result = run_validation_gate(
        task_id="weakened-test",
        run_id="test-run",
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/weakened-test",
        allow_test_edits=False,
        gate=_gate_config(),
    )
    assert not result.passed
    assert result.failure_stage is not None and result.failure_stage.value == "test_integrity"
    # The diff gate runs before any container -- a real build was never attempted.
    assert result.build is None
    assert any(v.kind == "assertion_count_decreased" for v in result.diff_gate.violations)


def test_a_secret_in_the_diff_is_caught_by_the_gitleaks_backstop(tmp_path: Path) -> None:
    repo = _fixture_repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/secret-leak")
    resources = repo / "backend/src/main/resources"
    resources.mkdir(parents=True)
    (resources / "application.properties").write_text("aws.secret.key=AKIAABCDEFGHIJKLMNOP\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "accidentally commit a secret")

    result = run_validation_gate(
        task_id="secret-leak",
        run_id="test-run",
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/secret-leak",
        allow_test_edits=False,
        gate=_gate_config(),
    )
    assert not result.passed
    assert result.failure_stage is not None and result.failure_stage.value == "secrets"
    assert result.build is None


def test_an_injected_flaky_e2e_test_is_classified_flaky_not_code_error(tmp_path: Path) -> None:
    """A deterministic stand-in for real flakiness: fails on the first
    invocation, passes on every rerun after. The counter file lives in the
    worktree so it survives across the gate's separate `docker run`
    invocations for each confirm-by-rerun attempt (spec 6.4)."""
    repo = _fixture_repo_on_develop(tmp_path)
    _git(repo, "checkout", "-q", "-b", "task/flaky")
    flaky_spec = repo / "frontend/e2e/flaky.spec.ts"
    flaky_spec.write_text(
        """\
import { expect, test } from "@playwright/test";
import { existsSync, readFileSync, writeFileSync } from "fs";

const COUNTER_FILE = "/work/.flaky-attempt-counter";

test("intermittently fails once then passes", async ({ page }) => {
  let attempt = 1;
  if (existsSync(COUNTER_FILE)) {
    attempt = parseInt(readFileSync(COUNTER_FILE, "utf-8"), 10) + 1;
  }
  writeFileSync(COUNTER_FILE, String(attempt));

  await page.goto("/");
  expect(attempt).toBeGreaterThan(1);
});
"""
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add an injected flaky e2e test")

    result = run_validation_gate(
        task_id="flaky",
        run_id="test-run",
        worktree_path=repo,
        base_branch="develop",
        task_branch="task/flaky",
        allow_test_edits=False,
        gate=_gate_config(),
    )
    assert result.passed
    assert result.flaky_detected == ["intermittently fails once then passes"]
    assert result.e2e is not None and result.e2e.failing_tests == []
