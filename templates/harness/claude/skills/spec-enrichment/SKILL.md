---
name: spec-enrichment
description: Enrich a raw, hand-written spec against this project's own standards/docs, then decompose it into one docs/specs/<name>-spec/tasks/<task>-task.md file per unit of work. Use when driven by `cosmo spec add`.
---

You are turning one rough, hand-written spec into a small set of well-scoped,
independently queueable tasks. This is a planning/decomposition step, not an
implementation step -- you do not write application code here, and no
OpenSpec change exists yet (that happens later, lazily, inside each task's
own `PROPOSING` step once it's actually queued and run).

## Inputs

- The raw spec itself, at `docs/specs/<name>-spec.md` -- can describe
  several pieces of work, not just one. Read it in full before deciding how
  to split it.
- This project's own standards, wherever present: `docs/backend/`,
  `docs/frontend/`, `docs/data-model.md`, `docs/base-standards.md` (the same
  files this repository's own `CLAUDE.md` names under "Project knowledge").
  Enrichment means using these to fill in what the raw spec left implicit --
  conventions, existing patterns, constraints -- not rewriting the user's
  intent.

## Deciding how to split the work

- One task per independently implementable, independently gate-able unit of
  work. A task that can't be built and validated on its own is too small to
  split out; a task that bundles two unrelated concerns is too large.
- Real dependencies between tasks are expected and fine -- record them, don't
  flatten them into one giant task to avoid the bookkeeping.
- If the raw spec genuinely describes only one small piece of work, one task
  is a completely valid decomposition. Do not manufacture a fan-out that
  isn't there.

## Output: one file per task

For each task, write `docs/specs/<name>-spec/tasks/<task>-task.md`
(`<task>` a short kebab-case slug, unique within this spec). Each file is
YAML frontmatter plus a markdown body -- the same frontmatter-plus-body
shape every skill/agent file in this directory already uses:

```
---
task_id: <task>
depends_on: [<other-task-id>, ...]   # omit or [] if none
priority: 0                          # higher runs first among eligible tasks; optional, default 0
title: <short human-readable title>
---

<the enriched task description -- specific enough that a fresh session with
no other context can implement it correctly: what to build, the relevant
convention(s) from this project's own docs, and what "done" looks like.>
```

`depends_on` entries must name other `task_id`s from this same batch (or an
existing queued task_id, if this work genuinely depends on something already
in flight) -- `cosmo spec queue` rejects a cycle across the whole batch
before inserting any of it.

## What this skill does not do

- Does not call `openspec new change` or touch `openspec/` at all -- no
  OpenSpec change exists yet. That happens per-task, lazily, the first time
  each task actually runs (`PROPOSING` reads its own `*-task.md` as source
  content and creates the change from it).
- Does not insert anything into Cosmo's task queue. `cosmo spec add` only
  writes the files above as a preview; `cosmo spec queue` is the separate,
  explicit step that inserts them for real, after a human has had the
  chance to read (and if needed, hand-edit) what you wrote here.
- Does not implement anything. Stop once every task file is written.
