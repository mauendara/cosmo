"""Configuration loading and layering.

Layers, lowest precedence first:
  1. defaults.toml shipped in this package (spec defaults)
  2. a user config file (XDG, or $COSMO_CONFIG)
  3. explicit overrides passed by the CLI

Path defaults are computed rather than shipped, because they depend on the host.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from cosmo.config.model import CosmoConfig

DEFAULTS_PATH = Path(__file__).with_name("defaults.toml")


def _xdg(var: str, fallback: str) -> Path:
    raw = os.environ.get(var)
    base = Path(raw) if raw else Path.home() / fallback
    return base / "cosmo"


def user_config_path() -> Path:
    """The per-host config file, which is where a droplet overrides paths."""
    override = os.environ.get("COSMO_CONFIG")
    if override:
        return Path(override)
    return _xdg("XDG_CONFIG_HOME", ".config") / "config.toml"


def default_paths() -> dict[str, Path]:
    data = _xdg("XDG_DATA_HOME", ".local/share")
    return {"data_dir": data, "work_dir": data / "work", "log_dir": data / "logs"}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = _deep_merge(current, value)
        else:
            out[key] = value
    return out


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_config(
    config_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> CosmoConfig:
    """Load, layer, and validate configuration.

    Raises pydantic.ValidationError on a bad value -- deliberately at startup,
    so a malformed timeout fails now rather than mid-run.
    """
    raw = _read_toml(DEFAULTS_PATH)
    raw.setdefault("paths", {})

    path = config_path if config_path is not None else user_config_path()
    if path.is_file():
        raw = _deep_merge(raw, _read_toml(path))

    if overrides:
        raw = _deep_merge(raw, overrides)

    for key, value in default_paths().items():
        raw["paths"].setdefault(key, value)

    return CosmoConfig.model_validate(raw)
