"""Shared preflight check result type.

Lives outside the harness package because both Cosmo's core checks and each
adapter's `preflight()` produce these (spec 2, extended in the v3 plan).
"""

from __future__ import annotations

import enum
import shutil
from dataclasses import dataclass


class CheckStatus(enum.Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str

    @property
    def blocking(self) -> bool:
        return self.status is CheckStatus.FAIL


def ok(name: str, detail: str) -> CheckResult:
    return CheckResult(name, CheckStatus.OK, detail)


def warn(name: str, detail: str) -> CheckResult:
    return CheckResult(name, CheckStatus.WARN, detail)


def fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name, CheckStatus.FAIL, detail)


def check_executable(name: str, binary: str, purpose: str) -> CheckResult:
    """Common helper: is an executable on PATH?"""
    found = shutil.which(binary)
    if found is None:
        return fail(name, f"{binary!r} not found on PATH -- needed for {purpose}")
    return ok(name, found)
