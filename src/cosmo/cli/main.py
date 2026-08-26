"""The `cosmo` command."""

from __future__ import annotations

import dataclasses
import sqlite3
import subprocess
import threading
import uuid
from dataclasses import Field
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from cosmo import __version__
from cosmo.bootstrap import (
    NotAGitRepoError,
    OpenSpecInitError,
    TemplatesRootNotFoundError,
    list_templates,
    run_init,
)
from cosmo.checks import CheckResult, CheckStatus
from cosmo.config import DEFAULTS_PATH, CosmoConfig, load_config, user_config_path
from cosmo.doctor import core_checks
from cosmo.events import EventEmitter, EventType, Severity, emit_state_changed
from cosmo.gate.runner import run_validation_gate
from cosmo.gate.types import GateResult
from cosmo.git.worktree import create_worktree
from cosmo.harness import (
    UnknownHarnessError,
    available_harnesses,
    get_adapter,
    resolve_harness_name,
)
from cosmo.harness.base import HarnessCapabilities, HarnessResult
from cosmo.run.dag import DagCycleError, find_cycle, resolve_execution_order
from cosmo.run.loop import run_queue
from cosmo.store import StoreWriter, TaskNotFoundError
from cosmo.store.enums import BlockedReason, RunStatus, StopReason, TaskStatus
from cosmo.store.reader import (
    find_project_by_path,
    get_progress,
    get_task,
    list_events,
    list_projects,
    list_tasks,
)
from cosmo.task import TaskContext, run_task

app = typer.Typer(
    name="cosmo",
    help="Autonomous spec-driven software development agent.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(name="config", help="Inspect configuration.", no_args_is_help=True)
harness_app = typer.Typer(name="harness", help="Inspect harness adapters.", no_args_is_help=True)
queue_app = typer.Typer(name="queue", help="Manage the task queue.", no_args_is_help=True)
events_app = typer.Typer(name="events", help="Inspect the event log.", no_args_is_help=True)
project_app = typer.Typer(name="project", help="Manage registered projects.", no_args_is_help=True)
templates_app = typer.Typer(
    name="templates", help="Inspect available templates.", no_args_is_help=True
)
app.add_typer(config_app)
app.add_typer(harness_app)
app.add_typer(queue_app)
app.add_typer(events_app)
app.add_typer(project_app)
app.add_typer(templates_app)

console = Console()
err_console = Console(stderr=True)

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="Config file to layer over the shipped defaults."),
]
HarnessOption = Annotated[
    str | None,
    typer.Option("--harness", help="Override the harness for this invocation."),
]
ProjectPathOption = Annotated[
    Path | None,
    typer.Option(
        "--project-path",
        help="A registered target repo -- supplies the project tier of harness resolution.",
    ),
]

_STATUS_STYLE = {
    CheckStatus.OK: ("[green]ok[/green]", "green"),
    CheckStatus.WARN: ("[yellow]warn[/yellow]", "yellow"),
    CheckStatus.FAIL: ("[red]FAIL[/red]", "red"),
}


def _load(config_path: Path | None) -> CosmoConfig:
    # `load_config` itself treats a missing path as "no override, use
    # defaults" regardless of whether that path came from the user's
    # environment (XDG/COSMO_CONFIG -- legitimately absent on a fresh
    # install) or from an explicit `--config` flag (a typo, which should be
    # loud, not silently ignored). Only the CLI layer knows which case it is.
    if config_path is not None and not config_path.is_file():
        err_console.print(f"[red]--config file not found:[/red] {config_path}")
        raise typer.Exit(code=2)
    try:
        return load_config(config_path)
    except ValidationError as exc:
        err_console.print("[red]Invalid configuration:[/red]")
        for error in exc.errors():
            loc = ".".join(str(p) for p in error["loc"]) or "(root)"
            err_console.print(f"  {loc}: {error['msg']}")
        raise typer.Exit(code=2) from None
    except (OSError, ValueError) as exc:
        err_console.print(f"[red]Cannot load configuration:[/red] {exc}")
        raise typer.Exit(code=2) from None


def _render_checks(title: str, results: list[CheckResult]) -> None:
    table = Table(title=title, title_justify="left", show_lines=False)
    table.add_column("", width=6)
    table.add_column("check", style="bold")
    table.add_column("detail", overflow="fold")
    for result in results:
        label, _ = _STATUS_STYLE[result.status]
        table.add_row(label, result.name, result.detail)
    console.print(table)


@app.callback(invoke_without_command=True)
def root(
    version: Annotated[bool, typer.Option("--version", help="Show the version and exit.")] = False,
) -> None:
    if version:
        console.print(f"cosmo {__version__}")
        raise typer.Exit()


@config_app.command("show")
def config_show(
    config: ConfigOption = None,
    paths: Annotated[
        bool, typer.Option("--paths", help="Show only where config and state live.")
    ] = False,
) -> None:
    """Print the resolved configuration."""
    cfg = _load(config)

    if paths:
        table = Table(title="paths", title_justify="left")
        table.add_column("what", style="bold")
        table.add_column("where")
        user = user_config_path()
        table.add_row("defaults", str(DEFAULTS_PATH))
        table.add_row("user config", f"{user} {'(present)' if user.is_file() else '(absent)'}")
        table.add_row("data dir", str(cfg.paths.data_dir))
        table.add_row("work dir", str(cfg.paths.work_dir))
        table.add_row("log dir", str(cfg.paths.log_dir))
        table.add_row("database", str(cfg.paths.db_path))
        console.print(table)
        return

    for section, value in cfg.model_dump().items():
        table = Table(title=section, title_justify="left")
        table.add_column("setting", style="bold")
        table.add_column("value")
        for key, val in value.items():
            table.add_row(key, str(val))
        console.print(table)


@harness_app.command("list")
def harness_list() -> None:
    """List registered harness adapters and their declared capabilities."""
    table = Table(title="registered harnesses", title_justify="left")
    table.add_column("name", style="bold")
    caps = [f.name for f in _capability_fields()]
    for cap in caps:
        table.add_column(cap.replace("_", "\n"), justify="center")

    for name, adapter in sorted(available_harnesses().items()):
        row = [name]
        for cap in caps:
            row.append("[green]yes[/green]" if getattr(adapter.capabilities, cap) else "no")
        table.add_row(*row)
    console.print(table)


def _capability_fields() -> tuple[Field[Any], ...]:
    return dataclasses.fields(HarnessCapabilities)


@harness_app.command("probe")
def harness_probe(
    prompt: Annotated[str, typer.Option(help="Raw prompt to send to the harness.")],
    harness: HarnessOption = None,
    timeout: Annotated[
        float | None,
        typer.Option(help="Seconds before cancelling. Defaults to timeouts.proposing_wall."),
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Smoke-test the resolved harness with a raw prompt (plan Phase 3 exit criterion).

    Applies an external timeout from here, on a background thread, rather
    than inside the adapter -- `has_internal_timeout=False` means Cosmo's
    orchestration layer owns that decision (spec 3.3), and this command is
    the simplest stand-in for that layer before Phase 7/8 build the real one.
    """
    cfg = _load(config)
    name, source = resolve_harness_name(harness, None, cfg.harness.name)
    console.print(f"harness: [bold]{name}[/bold] (from {source})")
    adapter = get_adapter(name)(cfg)

    timeout_s = timeout if timeout is not None else float(cfg.timeouts.proposing_wall)
    result_box: list[HarnessResult] = []
    error_box: list[BaseException] = []

    def _run() -> None:
        try:
            result_box.append(adapter.probe(prompt))
        except BaseException as exc:  # noqa: BLE001 -- surfaced on the main thread below
            error_box.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    if thread.is_alive():
        console.print(f"[yellow]probe exceeded {timeout_s:.0f}s -- cancelling[/yellow]")
        adapter.cancel("probe")
        thread.join(timeout=cfg.timeouts.kill_grace + 5.0)

    if error_box:
        raise error_box[0]
    if not result_box:
        err_console.print("[red]probe did not complete[/red]")
        raise typer.Exit(code=1)

    result = result_box[0]
    table = Table(title="probe result", title_justify="left", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("success", str(result.success))
    table.add_row("session_id", result.session_id or "-")
    table.add_row("exit_code", str(result.exit_code))
    table.add_row("duration_seconds", f"{result.duration_seconds:.2f}")
    table.add_row("total_cost_usd", str(result.total_cost_usd))
    table.add_row("output_summary", result.output_summary)
    table.add_row("raw_log_path", str(result.raw_log_path))
    console.print(table)

    if not result.success:
        raise typer.Exit(code=1)


def _git_current_branch(worktree: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _render_gate_result(result: GateResult) -> None:
    table = Table(title="validation gate result", title_justify="left", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    table.add_row("passed", str(result.passed))
    table.add_row("duration_seconds", f"{result.duration_seconds:.2f}")
    table.add_row("diff_gate", "passed" if result.diff_gate.passed else "FAILED")
    for stage_name, stage in (("build", result.build), ("unit", result.unit), ("e2e", result.e2e)):
        if stage is None:
            table.add_row(stage_name, "-")
            continue
        counts = (
            f"{stage.counts.passed} passed, {stage.counts.failed} failed, "
            f"{stage.counts.skipped} skipped"
            if stage.counts
            else "-"
        )
        table.add_row(stage_name, f"{'passed' if stage.passed else 'FAILED'} ({counts})")
    table.add_row("flaky_detected", ", ".join(result.flaky_detected) or "-")
    table.add_row("quarantined_skipped", ", ".join(result.quarantined_skipped) or "-")
    if not result.passed:
        table.add_row("failure_type", result.failure_type.value if result.failure_type else "-")
        table.add_row("failure_stage", result.failure_stage.value if result.failure_stage else "-")
        table.add_row("error_summary", result.error_summary or "-")
        table.add_row("error_detail", result.error_detail or "-")
    console.print(table)


@app.command("validate")
def validate_cmd(
    worktree: Annotated[Path, typer.Argument(help="Path to the task's worktree.")],
    task_id: Annotated[str, typer.Option(help="Task identifier, for labels and attribution.")],
    task_branch: Annotated[
        str | None,
        typer.Option(help="Defaults to the worktree's current branch."),
    ] = None,
    base_branch: Annotated[
        str | None, typer.Option(help="Defaults to git.base_branch from config.")
    ] = None,
    allow_test_edits: Annotated[
        bool, typer.Option(help="Skip the diff gate's test-integrity checks (spec 6.1).")
    ] = False,
    run_id: Annotated[str | None, typer.Option(help="Attaches gate container labels only.")] = None,
    config: ConfigOption = None,
) -> None:
    """Run the Docker validation gate standalone (plan Phase 6 exit
    criterion) -- a diagnostic entry point, the same posture `cosmo harness
    probe` takes: it runs the gate directly against `worktree` and never
    touches the store, since a bare worktree need not correspond to a queued
    task. `gate.validate_task` is the store-backed seam for the real
    `VALIDATING` state handler (Phase 7/8)."""
    cfg = _load(config)
    resolved_branch = task_branch if task_branch is not None else _git_current_branch(worktree)
    resolved_base = base_branch if base_branch is not None else cfg.git.base_branch

    result = run_validation_gate(
        task_id=task_id,
        run_id=run_id,
        worktree_path=worktree,
        base_branch=resolved_base,
        task_branch=resolved_branch,
        allow_test_edits=allow_test_edits,
        gate=cfg.gate,
        db_path=cfg.paths.db_path,
    )
    _render_gate_result(result)
    if not result.passed:
        raise typer.Exit(code=1)


@app.command("run")
def run_cmd(
    *,
    repo: Annotated[
        Path, typer.Option(help="Cosmo's own checkout of the target repo, on base_branch.")
    ],
    task_id: Annotated[
        str | None,
        typer.Option(
            "--task",
            help="Drive only this one queued task (plan Phase 7 posture), instead of the "
            "full DAG (plan Phase 8, the default when omitted).",
        ),
    ] = None,
    base_branch: Annotated[
        str | None, typer.Option(help="Defaults to git.base_branch from config.")
    ] = None,
    harness: HarnessOption = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Print the DAG's resolved multi-task execution order without executing "
            "anything. Ignored (and irrelevant) with --task.",
        ),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """`--task <id>`: drive one queued task through the full spec 3.2 state
    machine (plan Phase 7 exit criterion) -- `QUEUED -> PROPOSING -> ... ->
    DONE`, or `BLOCKED` on an unrecoverable failure. No `--task`: drive the
    *whole* queue as a dependency-ordered DAG (plan Phase 8 exit criterion)
    via `run.loop.run_queue` -- strictly serial (spec 5), until the queue
    empties, a circuit breaker trips, a quota/cost ceiling stops it, or the
    run-level wall clock expires. Both paths create each task's worktree
    (spec 3.2/10.5) the same way; deliberately kept as two paths rather than
    routing single-task through the DAG loop too, so Phase 7's already-
    tested single-task behavior (including its `run_id=None`, no-run-
    tracking posture) is untouched -- see `docs/v3-implementation-state.
    md`'s Phase 8 section for the full reasoning."""
    cfg = _load(config)

    if task_id is None:
        _run_queue_cmd(
            repo=repo, base_branch=base_branch, harness=harness, dry_run=dry_run, cfg=cfg
        )
        return

    task = get_task(cfg.paths.db_path, task_id)
    if task is None:
        err_console.print(f"[red]no such task: {task_id!r}[/red]")
        raise typer.Exit(code=1)
    if task.status != "queued":
        err_console.print(f"[red]task {task_id!r} is {task.status!r}, not queued[/red]")
        raise typer.Exit(code=1)

    resolved_base = base_branch if base_branch is not None else cfg.git.base_branch
    name, source = resolve_harness_name(harness, None, cfg.harness.name)
    console.print(f"harness: [bold]{name}[/bold] (from {source})")
    try:
        adapter = get_adapter(name)(cfg)
    except UnknownHarnessError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None

    writer = StoreWriter(cfg.paths.db_path)
    try:
        emitter = EventEmitter(writer)
        run_id = uuid.uuid4().hex
        spec_id = Path(task.spec_path).stem
        info = create_worktree(
            repo_path=repo,
            work_dir=cfg.paths.work_dir,
            run_id=run_id,
            task_id=task_id,
            spec_id=spec_id,
            base_branch=resolved_base,
            harness=name,
            writer=writer,
            emitter=emitter,
        )
        adapter.cwd = info.path

        ctx = TaskContext(
            task_id=task_id,
            spec_path=task.spec_path,
            worktree_path=info.path,
            branch=info.branch,
            base_branch=resolved_base,
            allow_test_edits=task.allow_test_edits,
            max_attempts=task.max_attempts,
        )
        final_status = run_task(
            ctx=ctx, config=cfg, writer=writer, emitter=emitter, adapter=adapter, repo_path=repo
        )
    finally:
        writer.close()

    style = "green" if final_status is TaskStatus.DONE else "yellow"
    console.print(f"[{style}]{final_status.value}[/{style}] {task_id}")
    if final_status is not TaskStatus.DONE:
        raise typer.Exit(code=1)


_RUN_SUCCESSFUL_STOP_REASONS = frozenset({StopReason.COMPLETED, StopReason.QUEUE_EMPTY})


def _run_queue_cmd(
    *,
    repo: Path,
    base_branch: str | None,
    harness: str | None,
    dry_run: bool,
    cfg: CosmoConfig,
) -> None:
    resolved_base = base_branch if base_branch is not None else cfg.git.base_branch
    name, source = resolve_harness_name(harness, None, cfg.harness.name)
    console.print(f"harness: [bold]{name}[/bold] (from {source})")

    if dry_run:
        try:
            order = resolve_execution_order(list_tasks(cfg.paths.db_path))
        except DagCycleError as exc:
            err_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(code=1) from None
        if not order:
            console.print("[yellow]no eligible queued tasks[/yellow]")
            return
        for i, tid in enumerate(order, start=1):
            console.print(f"{i}. {tid}")
        return

    try:
        adapter = get_adapter(name)(cfg)
    except UnknownHarnessError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None

    writer = StoreWriter(cfg.paths.db_path)
    try:
        emitter = EventEmitter(writer)
        outcome = run_queue(
            config=cfg,
            writer=writer,
            emitter=emitter,
            adapter=adapter,
            repo_path=repo,
            base_branch=resolved_base,
            harness_name=name,
        )
    finally:
        writer.close()

    ok = outcome.status is RunStatus.STOPPED and outcome.stop_reason in _RUN_SUCCESSFUL_STOP_REASONS
    style = "green" if ok else "yellow"
    reason = f" ({outcome.stop_reason.value})" if outcome.stop_reason is not None else ""
    console.print(f"[{style}]{outcome.status.value}{reason}[/{style}]")
    console.print(
        f"completed={outcome.summary.completed} blocked={outcome.summary.blocked} "
        f"requeued={outcome.summary.requeued} retried={outcome.summary.retried}"
    )
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def doctor(
    config: ConfigOption = None,
    harness: HarnessOption = None,
    project_path: ProjectPathOption = None,
) -> None:
    """Check that this host can run Cosmo.

    Reports core checks and harness checks separately: core checks are
    harness-agnostic, while harness checks are whatever the resolved adapter
    declares for itself (spec 2).
    """
    cfg = _load(config)

    project_harness = None
    if project_path is not None:
        project = find_project_by_path(cfg.paths.db_path, str(project_path.resolve()))
        if project is not None:
            project_harness = project.harness

    name, source = resolve_harness_name(harness, project_harness, cfg.harness.name)
    console.print(f"harness: [bold]{name}[/bold] (from {source})\n")

    core = core_checks(cfg)
    _render_checks("core checks", core)

    try:
        adapter = get_adapter(name)(cfg)
    except UnknownHarnessError as exc:
        err_console.print(f"\n[red]{exc}[/red]")
        raise typer.Exit(code=1) from None

    harness_results = adapter.preflight()
    _render_checks(f"harness checks ({name})", harness_results)

    all_results = core + harness_results
    failures = [r for r in all_results if r.status is CheckStatus.FAIL]
    warnings = [r for r in all_results if r.status is CheckStatus.WARN]

    if failures:
        console.print(
            f"\n[red]{len(failures)} blocking problem(s)[/red]"
            + (f", {len(warnings)} warning(s)" if warnings else "")
        )
        raise typer.Exit(code=1)
    if warnings:
        console.print(f"\n[yellow]ready, with {len(warnings)} warning(s)[/yellow]")
        return
    console.print("\n[green]ready[/green]")


# ---------------------------------------------------------------------------
# cosmo init / cosmo templates -- spec 10.4, 10.3.
# ---------------------------------------------------------------------------

_SYMLINK_STYLE = {
    "created": "green",
    "refreshed": "green",
    "skipped_conflict": "red",
    "skipped_missing_target": "yellow",
}


@app.command()
def init(
    target_path: Annotated[
        Path, typer.Argument(help="Path to the target repo (must be git-managed).")
    ],
    harness: HarnessOption = None,
    project_template: Annotated[
        str | None,
        typer.Option("--project-template", help="Project docs template. Defaults to '_blank'."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(help="Overwrite docs/ files already present in the target repo (spec 10.4)."),
    ] = False,
    config: ConfigOption = None,
) -> None:
    """Bootstrap a target repo: openspec/, docs/, .agent/<harness>/, root symlinks (spec 10.4)."""
    cfg = _load(config)
    resolved_harness, source = resolve_harness_name(harness, None, cfg.harness.name)
    resolved_template = project_template or "_blank"
    console.print(f"harness: [bold]{resolved_harness}[/bold] (from {source})")
    console.print(f"project template: [bold]{resolved_template}[/bold]")

    if force:
        proceed = typer.confirm(
            f"--force will overwrite any existing docs/ file that the "
            f"{resolved_template!r} template also provides. Continue?"
        )
        if not proceed:
            console.print("[yellow]aborted[/yellow]")
            raise typer.Exit(code=1)

    writer = StoreWriter(cfg.paths.db_path)
    try:
        result = run_init(
            target_path,
            harness=resolved_harness,
            project_template=resolved_template,
            force_docs=force,
            writer=writer,
            db_path=cfg.paths.db_path,
        )
    except NotAGitRepoError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from None
    except (TemplatesRootNotFoundError, OpenSpecInitError) as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    finally:
        writer.close()

    console.print(
        "[green]openspec/[/green] created" if result.openspec.ran else "openspec/ already present"
    )
    console.print(
        f"docs/: created {len(result.docs.created)}, "
        f"skipped (already exists) {len(result.docs.skipped)}"
    )
    for rel in result.docs.skipped:
        console.print(f"  [dim]skipped[/dim] docs/{rel}")
    console.print(
        f".agent/{resolved_harness}/: synced "
        f"(template_version={result.assets.template_version[:12]})"
    )
    for link in result.symlinks:
        style = _SYMLINK_STYLE[link.status]
        console.print(f"  [{style}]{link.status}[/{style}] {link.link_name} -> {link.detail}")
    console.print(
        f"project already registered ({result.project_id})"
        if result.already_registered
        else f"[green]registered[/green] project {result.project_id}"
    )


@templates_app.command("list")
def templates_list() -> None:
    """Names available under templates/harness/ and templates/projects/ (spec 10.3)."""
    try:
        listing = list_templates()
    except TemplatesRootNotFoundError as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None

    harness_table = Table(title="harness templates", title_justify="left")
    harness_table.add_column("name", style="bold")
    for name in listing.harnesses:
        harness_table.add_row(name)
    console.print(harness_table)

    project_table = Table(title="project templates", title_justify="left")
    project_table.add_column("name", style="bold")
    for name in listing.project_templates:
        project_table.add_row(name)
    console.print(project_table)


# ---------------------------------------------------------------------------
# cosmo project -- spec 10.4 step 6. A minimal registration path so the
# project tier of harness resolution has something to resolve against ahead
# of the full `cosmo init` bootstrap flow (plan Phase 4).
# ---------------------------------------------------------------------------


@project_app.command("register")
def project_register(
    target_path: Annotated[Path, typer.Argument(help="Path to the target repo.")],
    harness: HarnessOption = None,
    project_template: Annotated[
        str | None, typer.Option(help="Project template used at bootstrap, if any.")
    ] = None,
    config: ConfigOption = None,
) -> None:
    """Register a target repo so its harness can be resolved by path."""
    cfg = _load(config)
    resolved = target_path.resolve()
    if not resolved.is_dir():
        err_console.print(f"[red]{resolved} is not a directory[/red]")
        raise typer.Exit(code=2)

    # No project row exists yet for this path -- resolution can only fall
    # back to the config default (spec 2, plan Phase 0 resolution order).
    resolved_harness, _ = resolve_harness_name(harness, None, cfg.harness.name)

    writer = StoreWriter(cfg.paths.db_path)
    try:
        project_id = writer.register_project(
            target_path=str(resolved), harness=resolved_harness, project_template=project_template
        )
    except sqlite3.IntegrityError:
        err_console.print(f"[red]{resolved} is already registered[/red]")
        raise typer.Exit(code=1) from None
    finally:
        writer.close()
    console.print(
        f"[green]registered[/green] {resolved} ({project_id}, harness={resolved_harness})"
    )


@project_app.command("list")
def project_list(config: ConfigOption = None) -> None:
    cfg = _load(config)
    table = Table(title="projects", title_justify="left")
    table.add_column("id", style="bold")
    table.add_column("path")
    table.add_column("harness")
    table.add_column("template")
    for p in list_projects(cfg.paths.db_path):
        table.add_row(p.project_id, p.target_path, p.harness, p.project_template or "-")
    console.print(table)


# ---------------------------------------------------------------------------
# cosmo queue -- spec 5.
# ---------------------------------------------------------------------------


@queue_app.command("add")
def queue_add(
    spec_path: Annotated[str, typer.Argument(help="Path to the OpenSpec change.")],
    task_id: Annotated[
        str | None, typer.Option("--task-id", help="Defaults to the spec path's final component.")
    ] = None,
    depends_on: Annotated[
        list[str] | None,
        typer.Option("--depends-on", help="task_id this task depends on; repeatable."),
    ] = None,
    priority: Annotated[int, typer.Option(help="Soft tie-breaker among eligible tasks.")] = 0,
    max_attempts: Annotated[
        int | None, typer.Option(help="Defaults to the configured retries.max_attempts.")
    ] = None,
    allow_test_edits: Annotated[
        bool, typer.Option(help="Bypass the test-path guard for this task (spec 2.5).")
    ] = False,
    config: ConfigOption = None,
) -> None:
    cfg = _load(config)
    resolved_task_id = task_id or Path(spec_path).stem
    resolved_depends_on = list(depends_on) if depends_on else []

    # Spec 5: "cycle detection at enqueue". Checked against every
    # not-yet-`done` task already in the queue plus this not-yet-inserted
    # one -- `run.dag.find_cycle` takes a plain {task_id: depends_on} graph
    # for exactly this reason, so no full `TaskRow` needs constructing for
    # a task that doesn't exist yet.
    existing = {
        t.task_id: t.depends_on for t in list_tasks(cfg.paths.db_path) if t.status != "done"
    }
    existing[resolved_task_id] = resolved_depends_on
    cycle = find_cycle(existing)
    if cycle is not None:
        err_console.print(f"[red]depends_on cycle: {' -> '.join(cycle)}[/red]")
        raise typer.Exit(code=1)

    writer = StoreWriter(cfg.paths.db_path)
    try:
        result = writer.queue_add(
            task_id=resolved_task_id,
            spec_path=spec_path,
            depends_on=resolved_depends_on,
            priority=priority,
            max_attempts=max_attempts if max_attempts is not None else cfg.retries.max_attempts,
            allow_test_edits=allow_test_edits,
        )
        emit_state_changed(EventEmitter(writer), result)
    except sqlite3.IntegrityError:
        err_console.print(f"[red]task {resolved_task_id!r} already queued[/red]")
        raise typer.Exit(code=1) from None
    finally:
        writer.close()
    console.print(f"[green]queued[/green] {resolved_task_id}")


@queue_app.command("ls")
def queue_ls(
    status: Annotated[str | None, typer.Option(help="Filter by status.")] = None,
    config: ConfigOption = None,
) -> None:
    cfg = _load(config)
    table = Table(title="task queue", title_justify="left")
    columns = (
        "task_id",
        "status",
        "attempts",
        "depends_on",
        "priority",
        "blocked_reason",
        "spec_path",
    )
    for col in columns:
        table.add_column(col)
    for t in list_tasks(cfg.paths.db_path, status=status):
        table.add_row(
            t.task_id,
            t.status,
            f"{t.attempt_count}/{t.max_attempts}",
            ",".join(t.depends_on) or "-",
            str(t.priority),
            t.blocked_reason or "-",
            t.spec_path,
        )
    console.print(table)


@queue_app.command("show")
def queue_show(task_id: str, config: ConfigOption = None) -> None:
    cfg = _load(config)
    task = get_task(cfg.paths.db_path, task_id)
    if task is None:
        err_console.print(f"[red]no such task: {task_id!r}[/red]")
        raise typer.Exit(code=1)

    table = Table(title=task_id, title_justify="left", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value")
    for field_name, value in dataclasses.asdict(task).items():
        table.add_row(field_name, str(value))
    console.print(table)

    progress = get_progress(cfg.paths.db_path, task_id)
    if progress is not None:
        label = progress.last_label or "-"
        console.print(f"progress: {progress.completed}/{progress.total} ({label})")


@queue_app.command("retry")
def queue_retry(task_id: str, config: ConfigOption = None) -> None:
    cfg = _load(config)
    writer = StoreWriter(cfg.paths.db_path)
    try:
        result = writer.queue_retry(task_id)
        emit_state_changed(EventEmitter(writer), result)
    except TaskNotFoundError:
        err_console.print(f"[red]no such task: {task_id!r}[/red]")
        raise typer.Exit(code=1) from None
    finally:
        writer.close()
    console.print(f"[green]requeued[/green] {task_id}")


@queue_app.command("block")
def queue_block(
    task_id: str,
    reason: Annotated[str, typer.Option(help="A blocked_reason value (spec 5).")],
    config: ConfigOption = None,
) -> None:
    cfg = _load(config)
    try:
        blocked_reason = BlockedReason(reason)
    except ValueError:
        valid = ", ".join(r.value for r in BlockedReason)
        err_console.print(f"[red]invalid reason {reason!r}; must be one of: {valid}[/red]")
        raise typer.Exit(code=2) from None

    writer = StoreWriter(cfg.paths.db_path)
    try:
        result = writer.queue_block(task_id, blocked_reason)
        emitter = EventEmitter(writer)
        emitter.emit(
            event_type=EventType.TASK_BLOCKED,
            severity=Severity.WARNING,
            task_id=task_id,
            payload={"blocked_reason": blocked_reason.value},
        )
        emit_state_changed(emitter, result)
    except TaskNotFoundError:
        err_console.print(f"[red]no such task: {task_id!r}[/red]")
        raise typer.Exit(code=1) from None
    finally:
        writer.close()
    console.print(f"[yellow]blocked[/yellow] {task_id} ({reason})")


# ---------------------------------------------------------------------------
# cosmo events -- spec 9.1/9.2.
# ---------------------------------------------------------------------------


@events_app.command("tail")
def events_tail(
    run: Annotated[str | None, typer.Option("--run", help="Filter by run_id.")] = None,
    task: Annotated[str | None, typer.Option("--task", help="Filter by task_id.")] = None,
    severity: Annotated[str | None, typer.Option("--severity", help="Filter by severity.")] = None,
    limit: Annotated[int, typer.Option(help="Most recent N events.")] = 50,
    config: ConfigOption = None,
) -> None:
    cfg = _load(config)
    table = Table(title="events", title_justify="left")
    for col in ("seq", "timestamp", "severity", "event_type", "run_id", "task_id"):
        table.add_column(col)
    rows = list_events(cfg.paths.db_path, run_id=run, task_id=task, severity=severity, limit=limit)
    for e in rows:
        table.add_row(
            str(e.sequence),
            e.timestamp,
            e.severity,
            e.event_type,
            e.run_id or "-",
            e.task_id or "-",
        )
    console.print(table)
