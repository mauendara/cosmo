"""Spec 5's DAG scheduler: `depends_on` is a hard ordering constraint,
`priority` only a soft tie-breaker among tasks already eligible to run.
Cycle detection at enqueue (plan Phase 8 build item 2) -- `cli/main.py`'s
`queue add` calls `find_cycle` directly before writing a new task, and
`resolve_execution_order` below calls it again defensively, since the run
loop is where correctness matters most and a cycle could in principle reach
`task_queue` some other way (a bulk import, a future second write path).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from cosmo.store.reader import TaskRow


class DagCycleError(RuntimeError):
    """`depends_on` edges form a cycle -- no execution order exists."""


def find_cycle(depends_on: Mapping[str, Sequence[str]]) -> list[str] | None:
    """`depends_on` maps `task_id -> its depends_on list`. Returns one
    cyclical path (task_ids, first repeated last) if a cycle exists among
    the given task_ids, else `None`. A dependency naming a task_id not
    present in `depends_on` at all is not this function's concern -- that
    is an *unmet* dependency, handled by `resolve_execution_order` (the
    task simply never becomes eligible), not a cycle.

    Plain keyword-free function (not a `TaskRow`-shaped API) so `cli/
    main.py`'s `queue add` can call it with a lightweight `{task_id:
    depends_on}` graph -- including the not-yet-inserted task being added --
    without constructing a full `TaskRow` for that check alone.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(depends_on, WHITE)
    path: list[str] = []

    def visit(task_id: str) -> list[str] | None:
        color[task_id] = GRAY
        path.append(task_id)
        for dep in depends_on[task_id]:
            if dep not in depends_on:
                continue
            if color[dep] == GRAY:
                return [*path[path.index(dep) :], dep]
            if color[dep] == WHITE:
                found = visit(dep)
                if found is not None:
                    return found
        path.pop()
        color[task_id] = BLACK
        return None

    for task_id in depends_on:
        if color[task_id] == WHITE:
            found = visit(task_id)
            if found is not None:
                return found
    return None


def resolve_execution_order(tasks: Sequence[TaskRow]) -> list[str]:
    """Kahn's algorithm restricted to `status == "queued"` tasks: a
    `depends_on` edge to any task not yet `done` blocks eligibility --
    including an edge naming a `blocked` or nonexistent task, which
    therefore simply never becomes eligible. That is a deliberate
    consequence, not an error: a downstream task should not run against a
    dependency that never finished, and spec 3.4 already asks that
    repeated conflicts on the same files (a symptom of exactly this
    situation) be surfaced in `run.summary` rather than silently worked
    around here.

    `priority` (higher first, `created_at` breaking further ties) is
    applied at each step among tasks already eligible by the hard
    constraint -- not as one global sort -- so a low-priority task that
    becomes eligible later still runs before a higher-priority task that
    is not yet eligible.

    Raises `DagCycleError` if the *non-done* tasks' own `depends_on` edges
    contain a cycle (a `done` task cannot participate in a live cycle --
    it already ran, breaking any cycle that passed through it).
    """
    live = [t for t in tasks if t.status != "done"]
    cycle = find_cycle({t.task_id: t.depends_on for t in live})
    if cycle is not None:
        raise DagCycleError(f"depends_on cycle: {' -> '.join(cycle)}")

    done_ids = {t.task_id for t in tasks if t.status == "done"}
    remaining = {t.task_id: t for t in tasks if t.status == "queued"}

    order: list[str] = []
    scheduled = set(done_ids)
    while remaining:
        eligible = [t for t in remaining.values() if all(dep in scheduled for dep in t.depends_on)]
        if not eligible:
            break  # unmet deps (blocked/missing dependency), not a cycle -- already checked above
        eligible.sort(key=lambda t: (-t.priority, t.created_at))
        chosen = eligible[0]
        order.append(chosen.task_id)
        scheduled.add(chosen.task_id)
        del remaining[chosen.task_id]
    return order
