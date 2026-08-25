"""The `cosmo` command."""

from __future__ import annotations

import dataclasses
from dataclasses import Field
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from cosmo import __version__
from cosmo.checks import CheckResult, CheckStatus
from cosmo.config import CosmoConfig, load_config, user_config_path
from cosmo.doctor import core_checks
from cosmo.harness import (
    UnknownHarnessError,
    available_harnesses,
    get_adapter,
    resolve_harness_name,
)
from cosmo.harness.base import HarnessCapabilities

app = typer.Typer(
    name="cosmo",
    help="Autonomous spec-driven software development agent.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(name="config", help="Inspect configuration.", no_args_is_help=True)
harness_app = typer.Typer(name="harness", help="Inspect harness adapters.", no_args_is_help=True)
app.add_typer(config_app)
app.add_typer(harness_app)

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

_STATUS_STYLE = {
    CheckStatus.OK: ("[green]ok[/green]", "green"),
    CheckStatus.WARN: ("[yellow]warn[/yellow]", "yellow"),
    CheckStatus.FAIL: ("[red]FAIL[/red]", "red"),
}


def _load(config_path: Path | None) -> CosmoConfig:
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
        shipped = "shipped defaults"
        user = user_config_path()
        table.add_row("defaults", shipped)
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


@app.command()
def doctor(config: ConfigOption = None, harness: HarnessOption = None) -> None:
    """Check that this host can run Cosmo.

    Reports core checks and harness checks separately: core checks are
    harness-agnostic, while harness checks are whatever the resolved adapter
    declares for itself (spec 2).
    """
    cfg = _load(config)

    # Project registration is the middle precedence tier; it arrives with the
    # project store in Phase 1, so it is None here.
    name, source = resolve_harness_name(harness, None, cfg.harness.name)
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
