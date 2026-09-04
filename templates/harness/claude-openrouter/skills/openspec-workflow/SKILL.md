---
name: openspec-workflow
description: How to drive OpenSpec's propose/apply flow from inside a Cosmo-managed repo using the raw openspec CLI. Use whenever a task involves creating, checking status on, or advancing an OpenSpec change.
---

This repo's `openspec/` directory is managed by the `openspec` CLI. Cosmo does
not pre-generate OpenSpec's own tool-specific skills/commands here
(`--tools none` at init, deliberately, so `.claude/` stays Cosmo's own
symlinked directory rather than fighting with OpenSpec's) -- this skill is
the replacement for those, scoped to this repo.

## Checking where a change stands

```
openspec status --change <change-id>
```

Prints per-artifact progress (`proposal`, `design`, `specs`, `tasks`) and
which artifacts are blocked on which. Run this first whenever you're not
sure what state a change is in -- do not assume from context.

## Getting the exact next step

```
openspec instructions <artifact> --change <change-id>
```

Returns everything needed for that one artifact: the task description, any
dependency files to read first, and the exact template/format the artifact
must follow. This is generated from the repo's own schema config, not a
static doc -- always fetch it fresh rather than recalling a previous run's
output, since it can differ change to change if the schema changes.

`tasks.md` is the artifact that matters for implementation: it MUST use
literal `- [ ] N.M Description` checkboxes, because Cosmo's own progress
watcher parses that exact format (spec 4) -- a task not in that shape is
invisible to Cosmo, not just to a human skimming the file.

## Creating a new change

```
openspec new change <name>
```

Use a short kebab-case name. This is normally done once, at proposal time --
implementation sessions are working against a change that already exists.

## Validating before finishing a phase

```
openspec validate <change-name>
```

Run this before considering `proposal`/`specs`/`design` complete for a
change -- it catches structural problems (missing sections, malformed
capability references) that would otherwise only surface much later.

## What this skill does not cover

Whether code changes are *correct* is never decided here or by anything in
this skill -- see the repository's `CLAUDE.md` for that. This skill only
covers driving OpenSpec's own document/checklist machinery correctly.
