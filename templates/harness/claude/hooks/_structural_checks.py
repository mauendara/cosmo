"""Structural test-file heuristics shared between `gate.diffgate` (post-
commit, full diff) and `templates/harness/*/hooks/test_path_guard.py`
(pre-flight, one `Edit`/`Write` call) -- G4, `docs/v15-fixes-after-wa-chat-
run.md`.

Deliberately stdlib-only (`re`/`fnmatch`, nothing from `cosmo` itself) so
this file can be copied byte-for-byte into every harness template's
`hooks/` directory, the same posture `_hooklib.py`'s own docstring already
holds itself to ("these scripts run standalone... with no cosmo package on
the path"). Without a shared, byte-identical copy, the hook's pre-flight
check and the gate's post-commit check would drift into two independently-
maintained assertion-counting regex lists -- exactly the kind of silent
divergence `test_harness_template_parity.py`'s existing byte-parity
discipline for `hooks/` already exists to prevent, extended here to this
one extra file. `tests/test_gate_structural_checks.py` asserts every
template copy stays byte-identical to this canonical one.

Assertion counting is a line-count heuristic per test framework, not a
real parser (see `gate.diffgate`'s own module docstring for why that's a
deliberate, spec-sanctioned tradeoff): it fails safe, only ever
under-counting a removal, never mistaking an unrelated line for one.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Sequence

ASSERTION_PATTERNS = (
    re.compile(r"\bassertThat\("),  # AssertJ (Java)
    re.compile(r"\bassert[A-Z]\w*\("),  # JUnit Assertions.assertEquals(...) etc
    re.compile(r"\bexpect\("),  # Vitest / Playwright
)


def count_assertions(lines: Sequence[str]) -> int:
    return sum(1 for line in lines if any(p.search(line) for p in ASSERTION_PATTERNS))


def is_test_path(path: str, patterns: Sequence[str]) -> str | None:
    """The first pattern `path` matches, or `None`. `fnmatch` has no
    glob-aware "zero or more directories" semantics for a leading `**/`,
    unlike `pathlib`/real shell globs -- `**/src/test/**` would otherwise
    fail to match a bare top-level `src/test/Foo.java` (no directory before
    `src/`), only matching once something precedes it (confirmed by hand:
    `fnmatch.translate('**/src/test/**')` requires a literal `/` before
    `src`). Also trying the pattern with its leading `**/` stripped covers
    exactly that top-level case."""
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return pattern
        if pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]):
            return pattern
    return None


def find_skip_annotation(lines: Sequence[str], annotations: Sequence[str]) -> str | None:
    """The first skip annotation (`@Disabled`, `.skip(`, ...) found as a
    substring of any line, or `None`."""
    for line in lines:
        hit = next((a for a in annotations if a in line), None)
        if hit is not None:
            return hit
    return None
