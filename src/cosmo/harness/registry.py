"""Harness registry and name resolution.

Adapters register by name. Core code resolves a name to a class here and never
imports a concrete adapter module directly.
"""

from __future__ import annotations

from cosmo.harness.base import HarnessAdapter
from cosmo.harness.claude import ClaudeCodeAdapter

_REGISTRY: dict[str, type[HarnessAdapter]] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter,
}


class UnknownHarnessError(ValueError):
    """Raised when a requested harness name has no registered adapter."""


def available_harnesses() -> dict[str, type[HarnessAdapter]]:
    return dict(_REGISTRY)


def get_adapter(name: str) -> type[HarnessAdapter]:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise UnknownHarnessError(f"unknown harness {name!r}; registered: {known}") from None


def resolve_harness_name(
    flag: str | None,
    project: str | None,
    configured: str,
) -> tuple[str, str]:
    """Resolve the active harness and say where the answer came from.

    Order (spec 2, plan Phase 0): --harness flag > per-project registration
    (spec 10.4, available from Phase 1) > config default. The source is returned
    alongside the name so every command can print which adapter it chose and why
    -- an audit log should never have to guess.
    """
    if flag:
        return flag, "--harness flag"
    if project:
        return project, "project registration"
    return configured, "config default"
