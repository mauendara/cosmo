"""Harness registry and name resolution."""

from __future__ import annotations

import pytest

from cosmo.harness import UnknownHarnessError, available_harnesses, get_adapter
from cosmo.harness.base import HarnessAdapter, HarnessCapabilities
from cosmo.harness.registry import resolve_harness_name


def test_every_registered_adapter_declares_all_capabilities() -> None:
    import dataclasses

    fields = {f.name for f in dataclasses.fields(HarnessCapabilities)}
    for name, adapter in available_harnesses().items():
        assert issubclass(adapter, HarnessAdapter), name
        assert adapter.name == name
        for field in fields:
            assert isinstance(getattr(adapter.capabilities, field), bool), (name, field)


def test_unknown_harness_names_the_registered_ones() -> None:
    with pytest.raises(UnknownHarnessError, match="registered:"):
        get_adapter("nonexistent")


@pytest.mark.parametrize(
    ("flag", "project", "configured", "expected", "source"),
    [
        ("flagged", "projected", "configured", "flagged", "--harness flag"),
        (None, "projected", "configured", "projected", "project registration"),
        (None, None, "configured", "configured", "config default"),
    ],
)
def test_resolution_order_and_provenance(
    flag: str | None, project: str | None, configured: str, expected: str, source: str
) -> None:
    """Every command prints where the harness came from; an audit log should
    never have to guess which adapter ran."""
    assert resolve_harness_name(flag, project, configured) == (expected, source)
