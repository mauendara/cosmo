"""Harness abstraction layer (spec 2).

Cosmo never talks to a specific harness directly -- it talks to an adapter
implementing a common interface. Core modules import from here; they must never
import a concrete adapter module or branch on an adapter's name.
"""

from cosmo.harness.base import HarnessAdapter, HarnessCapabilities
from cosmo.harness.registry import (
    UnknownHarnessError,
    available_harnesses,
    get_adapter,
    resolve_harness_name,
)

__all__ = [
    "HarnessAdapter",
    "HarnessCapabilities",
    "UnknownHarnessError",
    "available_harnesses",
    "get_adapter",
    "resolve_harness_name",
]
