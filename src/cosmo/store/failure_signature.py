"""Structural sub-classification of `task_failures.error_detail` (v5
improvements plan part 5, Class 1 -- the one part of that section that was
actually decided, not left as open research).

`error_summary` alone can't tell two build failures with completely
different root causes apart (real evidence: 3 of 5 rows in one task's real
`task_failures` history were the identical missing-`package-lock.json`
`npm ci` error, indistinguishable from `error_summary` = "frontend build
failed" without reading `error_detail` by hand). This is deterministic
substring matching against `error_detail` -- no model call, no prose
interpretation beyond recognizing a known tool's own verbatim output shape
-- matching spec 4's rule against parsing free text for a *classification*
decision. Deliberately a small, non-exhaustive taxonomy: anything unmatched
stays `None` rather than forcing a guess.

Lives under `cosmo.store`, not `cosmo.task`, purely to avoid a real import
cycle: `store.writer` (the one caller, at its `record_task_failure`
chokepoint) sits underneath `cosmo.task` in the dependency graph, and
`cosmo.task.__init__` imports `task.machine`, which itself imports
`store.writer` -- importing this classifier through `cosmo.task` would
import a partially-initialized `store.writer` module.
"""

from __future__ import annotations

_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # npm ci refuses to run at all without a committed lockfile (deviation
    # 42/48 in docs/v3-implementation-state.md -- recurred 3 times).
    ("missing_lockfile", ("npm ci", "package-lock.json")),
    # npm's own engine-mismatch warning code (deviation 41's shape).
    ("node_engine_mismatch", ("EBADENGINE",)),
    # A build/test step running before (or without) `npm install`.
    ("enoent_node_modules", ("ENOENT", "node_modules")),
)


def classify_failure_signature(error_detail: str | None) -> str | None:
    """Returns the first matching signature name, or `None` if `error_detail`
    is empty or matches nothing in the (deliberately small) taxonomy above."""
    if not error_detail:
        return None
    for signature, needles in _SIGNATURES:
        if all(needle in error_detail for needle in needles):
            return signature
    return None
