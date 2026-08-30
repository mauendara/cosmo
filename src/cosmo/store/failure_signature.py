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

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from cosmo.store.reader import TaskFailureRow

_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # npm ci refuses to run at all without a committed lockfile (deviation
    # 42/48 in docs/v3-implementation-state.md -- recurred 3 times).
    ("missing_lockfile", ("npm ci", "package-lock.json")),
    # npm's own engine-mismatch warning code (deviation 41's shape).
    ("node_engine_mismatch", ("EBADENGINE",)),
    # A build/test step running before (or without) `npm install`.
    ("enoent_node_modules", ("ENOENT", "node_modules")),
    # gitleaks flagging a stray backup/leftover artifact directory (the
    # unprivileged harness can rename `node_modules` aside when it can't
    # `rm`/`sudo` a gate-container-root-owned one, but can't always remove
    # the renamed copy either -- git.worktree.reset_worktree_to_commit now
    # handles the removal itself, but a task blocked *before* that fix
    # shipped still carries this signature in its history). `generic-api-
    # key` is gitleaks' own catch-all rule id, the one that fires on
    # minified vendor JS almost every time; paired with an `_old/`-named
    # directory rather than used alone, which would be far too broad.
    ("secrets_stray_backup_artifact", ("[generic-api-key]", "_old/")),
    # Playwright's own version-mismatch banner: the npm package version an
    # `IMPLEMENTING` session resolved doesn't match `gate.playwright_image`'s
    # pinned tag. Recurred in both directions on the same real task (a
    # `gate.playwright_image` bump chasing whatever npm happened to resolve
    # that attempt) -- the real fix is pinning an exact version in the
    # target repo's own docs, not chasing this from Cosmo's config.
    ("playwright_image_version_mismatch", ("Please update docker image as well",)),
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


def _block_class_key(failure: TaskFailureRow) -> str:
    """The identity a repeat-block check groups by: the deterministic
    `failure_signature` when one was classified, else the
    `(failure_stage, error_summary)` pair -- itself already deterministic
    (spec 4 forbids prose-parsing for a classification decision; both
    fields come from a fixed enum/format, never free text), just coarser
    than a real signature. Real evidence this fallback matters: `scaffold-
    app`'s own `error_max_turns` blocks (no `error_detail` at all, so
    `classify_failure_signature` can never match) recurred 3 times across
    3 different runs in this project's real acceptance-run history."""
    if failure.failure_signature:
        return failure.failure_signature
    return f"{failure.failure_stage}:{failure.error_summary}"


@dataclass(frozen=True, slots=True)
class RepeatBlock:
    """A task's most recent terminal block shares its class key with
    `len(occurrences)` prior terminal blocks (the most recent one included).
    `is_deterministic` is `True` only when the shared key came from a real
    `failure_signature`, not the coarser stage/summary fallback -- callers
    can use it to phrase a more or less confident report."""

    class_key: str
    is_deterministic: bool
    occurrences: tuple[TaskFailureRow, ...]


def detect_repeat_block(
    failures: Sequence[TaskFailureRow], *, threshold: int
) -> RepeatBlock | None:
    """Has this task's most recent terminal block (`next_action == "block"`)
    already happened, for the same underlying reason, at least `threshold`
    times before across this task's *entire* history -- not just the
    current run's own retry budget, which `queue retry` resets to 0 every
    time regardless of why the task blocked.

    `failures` should be every `task_failures` row for one task, across
    every run it has ever been part of (`store.reader.list_task_failures`
    with no `run_id` filter) -- a `queue retry` cycle otherwise has no
    memory of a task that has blocked the same way 3 times across 3
    separate overnight runs, and will happily hand it 3 more attempts to
    fail the 4th time the same way.

    Returns `None` when there's no block history yet, or when the most
    recent block's class key hasn't recurred `threshold` times among prior
    blocks (i.e. this would still be at or under the budget of repeats
    considered normal retry noise)."""
    blocks = [f for f in failures if f.next_action == "block"]
    if not blocks:
        return None
    latest = blocks[-1]
    key = _block_class_key(latest)
    matches = tuple(f for f in blocks if _block_class_key(f) == key)
    if len(matches) <= threshold:
        return None
    return RepeatBlock(
        class_key=key, is_deterministic=latest.failure_signature is not None, occurrences=matches
    )
