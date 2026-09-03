"""Interactive prompts for `cosmo init -i` (`cli.main.init`).

Kept separate from `bootstrap.init.run_init`, which stays a pure,
flag-driven function this module never touches or imports side effects
into -- the same split as `notify.setup` (interactive-only helpers) vs.
`notify.telegram.TelegramSink` (the non-interactive sink it configures).

Every field here is only prompted for when the caller didn't already supply
it as a CLI flag -- `-i` fills gaps, it never re-asks about something
already decided on the command line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import typer
from rich.console import Console

from cosmo.bootstrap import list_templates
from cosmo.config import CosmoConfig


@dataclass(frozen=True, slots=True)
class WizardChoices:
    target_path: Path
    harness: str
    project_template: str
    base_branch: str
    force_docs: bool
    model_overrides: dict[str, str] = field(default_factory=dict)
    """Only the fields the user actually entered a value for -- keys are a
    subset of `model`/`propose_model`/`implement_model`/`review_model`.
    Empty means "customize models" was declined, not "clear every field"."""


def collect(
    *,
    console: Console,
    target_path_flag: Path | None,
    harness_flag: str | None,
    project_template_flag: str | None,
    force_flag: bool,
    cfg: CosmoConfig,
    default_harness: str,
) -> WizardChoices:
    listing = list_templates()

    if target_path_flag is not None:
        target_path = target_path_flag
    else:
        target_path = Path(typer.prompt("Target repo path", default="."))

    if harness_flag is not None:
        harness = harness_flag
    else:
        console.print(f"[dim]available harnesses: {', '.join(listing.harnesses)}[/dim]")
        default = default_harness if default_harness in listing.harnesses else listing.harnesses[0]
        harness = _prompt_from_list(console, "Harness", listing.harnesses, default)

    if project_template_flag is not None:
        project_template = project_template_flag
    else:
        console.print(
            f"[dim]available project templates: {', '.join(listing.project_templates)}[/dim]"
        )
        default_template = (
            "_blank" if "_blank" in listing.project_templates else listing.project_templates[0]
        )
        project_template = _prompt_from_list(
            console, "Project template", listing.project_templates, default_template
        )

    base_branch = typer.prompt("Base branch", default=cfg.git.base_branch)

    force_docs = force_flag or typer.confirm(
        "Overwrite any docs/ file the chosen template also provides, if already present?",
        default=False,
    )

    model_overrides = _collect_model_overrides(harness, cfg, console)

    return WizardChoices(
        target_path=target_path,
        harness=harness,
        project_template=project_template,
        base_branch=base_branch,
        force_docs=force_docs,
        model_overrides=model_overrides,
    )


def _prompt_from_list(console: Console, label: str, choices: list[str], default: str) -> str:
    while True:
        value = str(typer.prompt(f"{label} ({'/'.join(choices)})", default=default))
        if value in choices:
            return value
        console.print(f"[red]{value!r} isn't one of: {', '.join(choices)}[/red]")


_MODEL_FIELDS = (
    ("model", "Default model for this harness"),
    ("propose_model", "Model for PROPOSING (blank = inherit default)"),
    ("implement_model", "Model for IMPLEMENTING (blank = inherit default)"),
    ("review_model", "Model for REVIEWING (blank = inherit default)"),
)


def _collect_model_overrides(harness: str, cfg: CosmoConfig, console: Console) -> dict[str, str]:
    if not typer.confirm(
        f"\nCustomize models for the {harness!r} harness now? This writes to your "
        f"GLOBAL config file and applies to every project using {harness!r}, not "
        f"just this one.",
        default=False,
    ):
        return {}

    existing = cfg.harness.overrides.get(harness)
    values: dict[str, str] = {}
    for attr, label in _MODEL_FIELDS:
        current = getattr(existing, attr, None) if existing is not None else None
        entered = typer.prompt(label, default=current or "", show_default=bool(current))
        if entered.strip():
            values[attr] = entered.strip()
    return values
