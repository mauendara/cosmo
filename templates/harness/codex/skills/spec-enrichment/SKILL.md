---
name: spec-enrichment
description: Enrich a raw docs/specs specification and decompose it into independently queueable task files. Use when invoked by cosmo spec add.
---

Read the raw `docs/specs/<name>-spec.md` and applicable project standards under
`docs/`. This is planning only: do not implement code, create an OpenSpec
change, or insert queue records.

Write one independently implementable unit per
`docs/specs/<name>-spec/tasks/<task>-task.md`:

```text
---
task_id: <task>
depends_on: []
priority: 0
title: <human-readable title>
allow_test_edits: true
---

<self-contained enriched task and acceptance criteria>
```

Omit `allow_test_edits` unless the deliverable must edit `src/test/**`,
`e2e/**`, or JS/TS `*.spec.*` / `*.test.*` files. Quote YAML titles containing
`: ` and ensure every dependency names another task in the batch or a genuine
existing prerequisite. Do not invent scope absent from the raw specification.
