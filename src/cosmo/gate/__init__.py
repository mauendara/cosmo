"""The Docker validation gate (spec 1.1, 1.2, 1.3, 6.1 layer 2, 6.4, 9.3).

Bypasses the LLM harness entirely (spec 2.2) -- nothing under this package
imports `cosmo.harness` (`tests/test_gate_boundary.py` enforces this
structurally, the same guarantee Phase 5 built for `cosmo.git.merge`).
"""

from __future__ import annotations

from cosmo.gate.fake import FakeGate, ScriptedGateResult
from cosmo.gate.runner import run_validation_gate
from cosmo.gate.types import (
    DiffGateResult,
    DiffGateViolation,
    FailingTest,
    GateResult,
    StageResult,
    TestCounts,
)
from cosmo.gate.validate import validate_task

__all__ = [
    "DiffGateResult",
    "DiffGateViolation",
    "FailingTest",
    "FakeGate",
    "GateResult",
    "ScriptedGateResult",
    "StageResult",
    "TestCounts",
    "run_validation_gate",
    "validate_task",
]
