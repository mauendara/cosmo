"""Pure gate mechanics (spec 1.1, 1.2, 6.1, 6.4): serial build -> unit ->
e2e, the diff gate ahead of them, the gitleaks backstop, and flaky
confirm-by-rerun. No `StoreWriter`/`EventEmitter` here -- mirrors
`git.merge.attempt_merge_ladder` (mechanics) vs `merge_task` (ties mechanics
to persisted state): `validate.py`'s `validate_task` is this module's
`merge_task`.

Bypasses the LLM harness entirely (spec 2.2) -- this module and everything
else under `cosmo.gate` must never import `cosmo.harness`
(`tests/test_gate_boundary.py` enforces this via `ast`, the same structural
guarantee Phase 5 built for the merge ladder).
"""

from __future__ import annotations

import time
from pathlib import Path

from cosmo.config.model import GateConfig
from cosmo.gate import docker_runner, parsers
from cosmo.gate.diffgate import run_diff_gate
from cosmo.gate.error_detail import build_diff_gate_error_detail, build_stage_error_detail
from cosmo.gate.flaky import confirm_by_rerun, maybe_escalate_to_quarantine_candidate
from cosmo.gate.quarantine import (
    is_quarantined,
    load_quarantine,
    quarantine_candidates_path,
    quarantine_file_path,
)
from cosmo.gate.types import FailingTest, GateResult, StageResult, TestCounts
from cosmo.git.secrets import run_gitleaks_scan
from cosmo.store.enums import FailureStage, FailureType

_BACKEND_CONTAINER_DIR = "/work"
_FRONTEND_CONTAINER_DIR = "/work"
_BACKEND_PORT = 8080
_FRONTEND_PORT = 4173
_HEALTH_TIMEOUT = 90.0


def _build_stage(
    *, worktree_path: Path, gate: GateConfig, run_id: str, task_id: str, docker_bin: str
) -> StageResult:
    start = time.monotonic()
    backend_dir = worktree_path / gate.backend_dir
    frontend_dir = worktree_path / gate.frontend_dir

    if backend_dir.is_dir():
        backend_run = docker_runner.run_container(
            image=gate.backend_image,
            workdir_mount=backend_dir,
            container_workdir=_BACKEND_CONTAINER_DIR,
            command=["mvn", "-B", "-q", "-DskipTests", "package"],
            gate=gate,
            run_id=run_id,
            task_id=task_id,
            docker_bin=docker_bin,
        )
        if backend_run.timed_out or backend_run.exit_code != 0:
            return StageResult(
                stage=FailureStage.BUILD,
                passed=False,
                duration_seconds=time.monotonic() - start,
                counts=None,
                error_summary="backend build failed"
                + (" (timed out)" if backend_run.timed_out else ""),
                error_detail=(backend_run.stdout + "\n" + backend_run.stderr)[-4000:],
                timed_out=backend_run.timed_out,
            )

    if frontend_dir.is_dir():
        frontend_run = docker_runner.run_container(
            image=gate.frontend_image,
            workdir_mount=frontend_dir,
            container_workdir=_FRONTEND_CONTAINER_DIR,
            command=["sh", "-c", "npm ci && npm run build"],
            gate=gate,
            run_id=run_id,
            task_id=task_id,
            docker_bin=docker_bin,
        )
        if frontend_run.timed_out or frontend_run.exit_code != 0:
            return StageResult(
                stage=FailureStage.BUILD,
                passed=False,
                duration_seconds=time.monotonic() - start,
                counts=None,
                error_summary="frontend build failed"
                + (" (timed out)" if frontend_run.timed_out else ""),
                error_detail=(frontend_run.stdout + "\n" + frontend_run.stderr)[-4000:],
                timed_out=frontend_run.timed_out,
            )

    return StageResult(
        stage=FailureStage.BUILD,
        passed=True,
        duration_seconds=time.monotonic() - start,
        counts=None,
    )


def _unit_stage(
    *, worktree_path: Path, gate: GateConfig, run_id: str, task_id: str, docker_bin: str
) -> StageResult:
    start = time.monotonic()
    total = TestCounts(0, 0, 0)
    failing: list[FailingTest] = []
    error_summary: str | None = None
    error_detail: str | None = None
    timed_out = False

    backend_dir = worktree_path / gate.backend_dir
    if backend_dir.is_dir():
        run = docker_runner.run_container(
            image=gate.backend_image,
            workdir_mount=backend_dir,
            container_workdir=_BACKEND_CONTAINER_DIR,
            command=["mvn", "-B", "-q", "test"],
            gate=gate,
            run_id=run_id,
            task_id=task_id,
            docker_bin=docker_bin,
        )
        reports_dir = backend_dir / "target" / "surefire-reports"
        if reports_dir.is_dir():
            counts, backend_failing = parsers.parse_maven_surefire_reports(reports_dir)
            total = TestCounts(
                total.passed + counts.passed,
                total.failed + counts.failed,
                total.skipped + counts.skipped,
            )
            failing += backend_failing
        elif run.exit_code != 0 or run.timed_out:
            error_summary = "backend unit tests failed to run"
            error_detail = (run.stdout + "\n" + run.stderr)[-4000:]
            timed_out = timed_out or run.timed_out

    frontend_dir = worktree_path / gate.frontend_dir
    if frontend_dir.is_dir():
        vitest_report = frontend_dir / "vitest-report.json"
        vitest_report.unlink(missing_ok=True)
        run = docker_runner.run_container(
            image=gate.frontend_image,
            workdir_mount=frontend_dir,
            container_workdir=_FRONTEND_CONTAINER_DIR,
            # A file, not stdout, for the JSON report -- `npm ci`'s own
            # stdout ("added N packages...") precedes vitest's output on the
            # same stream, which would otherwise break `json.loads` (found
            # by hand: a real `npm ci && vitest run --reporter=json` combined
            # stream is not valid JSON on its own).
            command=[
                "sh",
                "-c",
                "npm ci && npx vitest run --reporter=json --outputFile=vitest-report.json",
            ],
            gate=gate,
            run_id=run_id,
            task_id=task_id,
            docker_bin=docker_bin,
        )
        if vitest_report.is_file():
            counts, frontend_failing = parsers.parse_vitest_json(vitest_report.read_text())
            total = TestCounts(
                total.passed + counts.passed,
                total.failed + counts.failed,
                total.skipped + counts.skipped,
            )
            failing += frontend_failing
        elif run.exit_code != 0 or run.timed_out:
            error_summary = (error_summary or "") + "; frontend unit tests failed to run"
            error_detail = (error_detail or "") + (run.stdout + "\n" + run.stderr)[-4000:]
            timed_out = timed_out or run.timed_out

    passed = total.failed == 0 and error_summary is None
    return StageResult(
        stage=FailureStage.UNIT_TESTS,
        passed=passed,
        duration_seconds=time.monotonic() - start,
        counts=total,
        failing_tests=failing,
        error_summary=error_summary
        or (f"{total.failed} unit test(s) failed" if not passed else None),
        error_detail=error_detail,
        timed_out=timed_out,
    )


def _e2e_stage(
    *,
    worktree_path: Path,
    gate: GateConfig,
    run_id: str,
    task_id: str,
    docker_bin: str,
    db_path: Path | None,
) -> tuple[StageResult, list[str], list[str]]:
    """Spec 1.2's final serial stage. Starts frontend (and, if the repo has
    one, backend) as long-lived containers on a private network (so
    Playwright reaches them by container hostname, matching how the target
    repo will actually be deployed -- no reliance on host networking), runs
    the pinned Playwright image against them, then applies spec 6.4's
    confirm-by-rerun to any non-quarantined failure before calling it a
    genuine `code_error`.

    A missing `backend_dir` does not skip this stage -- only a missing
    `frontend_dir` does. A backend-less repo (e.g. a frontend-only template
    with no server component) still gets real e2e coverage: the backend
    container, its health check, and `VITE_BACKEND_URL` are all skipped, and
    Playwright runs against the frontend alone. Skipping e2e outright
    whenever no backend exists would make it silently always "pass" with no
    tests run for any backend-less project -- indistinguishable from a repo
    that genuinely has no e2e suite, which defeats spec 1.2's own guarantee
    that the gate is the only source of truth about correctness.

    Returns `(stage_result, flaky_detected, quarantined_skipped)` -- the two
    lists spec 9.2 requires at the top level of `task.validation_result`,
    not buried inside the stage detail.
    """
    start = time.monotonic()
    backend_dir = worktree_path / gate.backend_dir
    frontend_dir = worktree_path / gate.frontend_dir
    has_backend = backend_dir.is_dir()
    if not frontend_dir.is_dir():
        return (
            StageResult(
                stage=FailureStage.E2E_TESTS,
                passed=True,
                duration_seconds=time.monotonic() - start,
                counts=None,
            ),
            [],
            [],
        )

    network = f"cosmo-gate-{(run_id or 'norun')[:12]}-{task_id[:12]}"
    backend_name = f"{network}-backend"
    frontend_name = f"{network}-frontend"

    quarantine = load_quarantine(quarantine_file_path(gate.quarantine_file))

    docker_runner.create_network(network, docker_bin=docker_bin)
    try:
        frontend_build_env: dict[str, str] = {}
        if has_backend:
            docker_runner.run_detached_service(
                name=backend_name,
                image=gate.backend_image,
                workdir_mount=backend_dir,
                container_workdir=_BACKEND_CONTAINER_DIR,
                command=["mvn", "-B", "-q", "spring-boot:run"],
                gate=gate,
                run_id=run_id,
                task_id=task_id,
                network=network,
                docker_bin=docker_bin,
                publish_container_port=_BACKEND_PORT,
            )
            backend_host_port = docker_runner.published_port(
                backend_name, _BACKEND_PORT, docker_bin=docker_bin
            )
            backend_ready = backend_host_port is not None and docker_runner.wait_for_http(
                f"http://localhost:{backend_host_port}/api/hello", timeout_seconds=_HEALTH_TIMEOUT
            )
            if not backend_ready:
                return (
                    StageResult(
                        stage=FailureStage.E2E_TESTS,
                        passed=False,
                        duration_seconds=time.monotonic() - start,
                        counts=None,
                        error_summary="backend container never became healthy for e2e",
                        error_detail=docker_runner.service_logs(
                            backend_name, docker_bin=docker_bin
                        )[-4000:],
                        timed_out=True,
                    ),
                    [],
                    [],
                )
            frontend_build_env["VITE_BACKEND_URL"] = f"http://{backend_name}:{_BACKEND_PORT}"

        build_run = docker_runner.run_container(
            image=gate.frontend_image,
            workdir_mount=frontend_dir,
            container_workdir=_FRONTEND_CONTAINER_DIR,
            command=["sh", "-c", "npm ci && npm run build"],
            gate=gate,
            run_id=run_id,
            task_id=task_id,
            docker_bin=docker_bin,
            extra_env=frontend_build_env,
        )
        if build_run.exit_code != 0 or build_run.timed_out:
            return (
                StageResult(
                    stage=FailureStage.E2E_TESTS,
                    passed=False,
                    duration_seconds=time.monotonic() - start,
                    counts=None,
                    error_summary="frontend build for e2e failed",
                    error_detail=(build_run.stdout + "\n" + build_run.stderr)[-4000:],
                    timed_out=build_run.timed_out,
                ),
                [],
                [],
            )

        docker_runner.run_detached_service(
            name=frontend_name,
            image=gate.frontend_image,
            workdir_mount=frontend_dir,
            container_workdir=_FRONTEND_CONTAINER_DIR,
            command=["npm", "run", "preview"],
            gate=gate,
            run_id=run_id,
            task_id=task_id,
            network=network,
            docker_bin=docker_bin,
            publish_container_port=_FRONTEND_PORT,
        )
        frontend_host_port = docker_runner.published_port(
            frontend_name, _FRONTEND_PORT, docker_bin=docker_bin
        )
        frontend_ready = frontend_host_port is not None and docker_runner.wait_for_http(
            f"http://localhost:{frontend_host_port}/", timeout_seconds=_HEALTH_TIMEOUT
        )
        if not frontend_ready:
            return (
                StageResult(
                    stage=FailureStage.E2E_TESTS,
                    passed=False,
                    duration_seconds=time.monotonic() - start,
                    counts=None,
                    error_summary="frontend container never became healthy for e2e",
                    error_detail=docker_runner.service_logs(frontend_name, docker_bin=docker_bin)[
                        -4000:
                    ],
                    timed_out=True,
                ),
                [],
                [],
            )

        base_url = f"http://{frontend_name}:{_FRONTEND_PORT}"

        def run_playwright(grep: str | None) -> docker_runner.ContainerRun:
            command = "npm ci && npx playwright test"
            if grep:
                command += f' --grep "{grep}"'
            return docker_runner.run_container(
                image=gate.playwright_image,
                workdir_mount=frontend_dir,
                container_workdir=_FRONTEND_CONTAINER_DIR,
                command=["sh", "-c", command],
                gate=gate,
                run_id=run_id,
                task_id=task_id,
                network=network,
                docker_bin=docker_bin,
                extra_env={"BASE_URL": base_url},
            )

        run = run_playwright(None)
        report_path = frontend_dir / "playwright-report" / "results.json"
        if not report_path.is_file():
            return (
                StageResult(
                    stage=FailureStage.E2E_TESTS,
                    passed=False,
                    duration_seconds=time.monotonic() - start,
                    counts=None,
                    error_summary="playwright produced no report"
                    + (" (timed out)" if run.timed_out else ""),
                    error_detail=(run.stdout + "\n" + run.stderr)[-4000:],
                    timed_out=run.timed_out,
                ),
                [],
                [],
            )

        counts, failing, artifacts = parsers.parse_playwright_json(report_path.read_text())

        flaky_detected: list[str] = []
        quarantined_skipped: list[str] = []
        genuine_failures: list[FailingTest] = []
        for ft in failing:
            if is_quarantined(ft.test_id, quarantine):
                quarantined_skipped.append(ft.test_id)
                continue

            def rerun_one(test_id: str, _grep: str = ft.test_id) -> bool:
                result = run_playwright(_grep)
                return result.exit_code == 0 and not result.timed_out

            outcome = confirm_by_rerun(ft.test_id, rerun_one, rerun_limit=gate.flaky_rerun_limit)
            if outcome.resolved:
                flaky_detected.append(ft.test_id)
                if db_path is not None:
                    # Escalation needs cross-run history (spec 6.4 step 4: "three
                    # flaky classifications ... across distinct runs"), which only
                    # exists once this gate run has a real event store to query --
                    # `FakeGate`/pure-mechanics callers pass db_path=None and skip it.
                    maybe_escalate_to_quarantine_candidate(
                        db_path=db_path,
                        test_id=ft.test_id,
                        current_run_id=run_id,
                        candidates_path=quarantine_candidates_path(gate.quarantine_candidates_file),
                        threshold=gate.flaky_quarantine_candidate_threshold,
                    )
            else:
                genuine_failures.append(ft)

        adjusted_counts = TestCounts(
            passed=counts.passed + len(flaky_detected) + len(quarantined_skipped),
            failed=len(genuine_failures),
            skipped=counts.skipped,
        )

        return (
            StageResult(
                stage=FailureStage.E2E_TESTS,
                passed=not genuine_failures,
                duration_seconds=time.monotonic() - start,
                counts=adjusted_counts,
                failing_tests=genuine_failures,
                error_summary=(
                    f"{len(genuine_failures)} e2e test(s) failed" if genuine_failures else None
                ),
                artifact_paths=artifacts,
            ),
            flaky_detected,
            quarantined_skipped,
        )
    finally:
        docker_runner.stop_service(backend_name, docker_bin=docker_bin)
        docker_runner.stop_service(frontend_name, docker_bin=docker_bin)
        docker_runner.remove_network(network, docker_bin=docker_bin)


def run_validation_gate(
    *,
    task_id: str,
    run_id: str | None,
    worktree_path: Path,
    base_branch: str,
    task_branch: str,
    allow_test_edits: bool,
    gate: GateConfig,
    docker_bin: str = "docker",
    gitleaks_bin: str = "gitleaks",
    db_path: Path | None = None,
) -> GateResult:
    """The whole spec 1.2 sequence: diff gate -> gitleaks backstop -> build
    -> unit -> e2e, stopping at the first failure (later stages never run
    once an earlier one has failed -- spec 1.2's own rationale: concurrent
    or continued execution after a known failure can't cleanly attribute a
    later problem to a distinct `failure_stage`).
    """
    start = time.monotonic()

    diff_gate = run_diff_gate(
        worktree_path=worktree_path,
        base_branch=base_branch,
        task_branch=task_branch,
        gate=gate,
        allow_test_edits=allow_test_edits,
    )
    if not diff_gate.passed:
        return GateResult(
            task_id=task_id,
            run_id=run_id,
            passed=False,
            duration_seconds=time.monotonic() - start,
            diff_gate=diff_gate,
            build=None,
            unit=None,
            e2e=None,
            failure_type=FailureType.CODE_ERROR,
            failure_stage=FailureStage.TEST_INTEGRITY,
            error_summary=f"diff gate rejected {len(diff_gate.violations)} violation(s)",
            error_detail=build_diff_gate_error_detail(
                diff_gate, max_chars=gate.error_detail_max_chars
            ),
            files_touched=[v.file for v in diff_gate.violations if v.file],
        )

    gitleaks_result = run_gitleaks_scan(worktree_path, gitleaks_bin=gitleaks_bin)
    if not gitleaks_result.clean:
        summary = (
            "gitleaks binary unavailable -- refusing to validate without a secret scan"
            if not gitleaks_result.ran
            else f"gitleaks found {len(gitleaks_result.findings)} potential secret(s)"
        )
        detail = (
            "\n".join(f"{f.file}:{f.line} [{f.rule_id}]" for f in gitleaks_result.findings)
            or summary
        )
        return GateResult(
            task_id=task_id,
            run_id=run_id,
            passed=False,
            duration_seconds=time.monotonic() - start,
            diff_gate=diff_gate,
            build=None,
            unit=None,
            e2e=None,
            failure_type=(
                FailureType.ENVIRONMENT_ERROR if not gitleaks_result.ran else FailureType.CODE_ERROR
            ),
            failure_stage=FailureStage.SECRETS,
            error_summary=summary,
            error_detail=detail[: gate.error_detail_max_chars],
            files_touched=[f.file for f in gitleaks_result.findings],
        )

    build = _build_stage(
        worktree_path=worktree_path,
        gate=gate,
        run_id=run_id or "norun",
        task_id=task_id,
        docker_bin=docker_bin,
    )
    if not build.passed:
        return GateResult(
            task_id=task_id,
            run_id=run_id,
            passed=False,
            duration_seconds=time.monotonic() - start,
            diff_gate=diff_gate,
            build=build,
            unit=None,
            e2e=None,
            failure_type=FailureType.ENVIRONMENT_ERROR
            if build.timed_out
            else FailureType.CODE_ERROR,
            failure_stage=FailureStage.BUILD,
            error_summary=build.error_summary,
            error_detail=build_stage_error_detail(build, max_chars=gate.error_detail_max_chars),
        )

    unit = _unit_stage(
        worktree_path=worktree_path,
        gate=gate,
        run_id=run_id or "norun",
        task_id=task_id,
        docker_bin=docker_bin,
    )
    if not unit.passed:
        return GateResult(
            task_id=task_id,
            run_id=run_id,
            passed=False,
            duration_seconds=time.monotonic() - start,
            diff_gate=diff_gate,
            build=build,
            unit=unit,
            e2e=None,
            failure_type=FailureType.ENVIRONMENT_ERROR
            if unit.timed_out
            else FailureType.CODE_ERROR,
            failure_stage=FailureStage.UNIT_TESTS,
            error_summary=unit.error_summary,
            error_detail=build_stage_error_detail(unit, max_chars=gate.error_detail_max_chars),
        )

    e2e, flaky_detected, quarantined_skipped = _e2e_stage(
        worktree_path=worktree_path,
        gate=gate,
        run_id=run_id or "norun",
        task_id=task_id,
        docker_bin=docker_bin,
        db_path=db_path,
    )
    if not e2e.passed:
        return GateResult(
            task_id=task_id,
            run_id=run_id,
            passed=False,
            duration_seconds=time.monotonic() - start,
            diff_gate=diff_gate,
            build=build,
            unit=unit,
            e2e=e2e,
            flaky_detected=flaky_detected,
            quarantined_skipped=quarantined_skipped,
            failure_type=FailureType.ENVIRONMENT_ERROR if e2e.timed_out else FailureType.CODE_ERROR,
            failure_stage=FailureStage.E2E_TESTS,
            error_summary=e2e.error_summary,
            error_detail=build_stage_error_detail(e2e, max_chars=gate.error_detail_max_chars),
        )

    return GateResult(
        task_id=task_id,
        run_id=run_id,
        passed=True,
        duration_seconds=time.monotonic() - start,
        diff_gate=diff_gate,
        build=build,
        unit=unit,
        e2e=e2e,
        flaky_detected=flaky_detected,
        quarantined_skipped=quarantined_skipped,
    )
