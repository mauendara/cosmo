# Cosmo — v4 Workflow Changes: raw-spec-in, OpenSpec-hidden

## Status

**Implemented.** See
[v3-implementation-state.md](v3-implementation-state.md)'s "v4 workflow
changes — Complete" section for what actually got built, every real
decision made along the way, and every place this document's own guesses
turned out differently once real code met them (most notably: `task_queue.
status`'s CHECK constraint needed its own migration, and a review's verdict
is delivered via a worktree file rather than any field on `HarnessResult`).
This document is kept as the original design record — read it for the
*why* behind the shape of the thing; read the state doc for what's real.

Recorded here, rather than only in an ephemeral plan file, so a future
session has the full reasoning and the exact real-code anchor points
without having to rediscover them.

## Context

The user's real development workflow, done by hand today outside Cosmo, is:

1. Write a rough spec (can describe several tasks, not just one).
2. Enrich it against the project's own standards/docs (backend, frontend,
   data model conventions).
3. `opsx:propose` the enriched spec → creates the real OpenSpec change(s).
4. `opsx:apply` → does the implementation work.
5. Run an adversarial-review skill against the result.
6. Run a "finish spec" skill that cleans up and runs `opsx:archive`.

Cosmo today already owns steps 3-4's *mechanics* (via `PROPOSING`/
`IMPLEMENTING`/`VALIDATING`/`COMMITTING`/`MERGING`), but assumes a human (or
a previous ad-hoc Claude session) has already turned a rough idea into one
real, already-existing OpenSpec change folder before `cosmo queue add` is
ever called — one change, one task, no fan-out, no enrichment step, no
review step, no archive step. The goal is to fold all six steps into
`cosmo` commands alone, with OpenSpec never surfacing as something the user
has to run themselves.

Design decisions already confirmed:
- **Archiving is per-task**, not per-batch — each fanned-out task archives
  its own change right after it merges.
- **A failed adversarial review retries like a gate failure** — same
  bounded `attempt_count`/`max_attempts` budget, not an automatic hard
  block.
- **Abstraction is behavioral-only** — the user never types an `openspec`
  command, but `cosmo report`/`queue show` can still display the
  underlying change name/path.
- **Decomposition is preview-first** — fanning one raw spec into N tasks
  with dependencies is shown to the user before anything is queued,
  mirroring `cosmo run --dry-run`'s existing precedent.
- **A new Cosmo file convention, in the target repo, for the raw-spec →
  task fan-out**: a raw spec lives at `docs/specs/<name>-spec.md`; once
  enriched and decomposed, its tasks live at
  `docs/specs/<name>-spec/tasks/<task>-task.md` — one file per fanned-out
  unit of work. Both are real files, versioned in the target repo's own
  git history alongside `docs/backend/`, `docs/frontend/`, etc.
- **The finish step is named `finish-change`, not `finish spec`, and is
  deliberately scoped down for v1**: it only runs `openspec archive` for
  now. No extra cleanup logic yet — more gets folded in later, once
  there's a concrete need for it.

## Current architecture this builds on (verified in the real code)

- Task state machine, one function per state, in `src/cosmo/task/machine.py`:
  `run_task()` (top-level loop) calls `_do_proposing` (line 344) →
  `_do_implementing`/gate retry loop (lines 176-288) → `_do_committing`
  (line 524) → `_do_merging` (line 635). `_do_merging` returns
  `TaskStatus.DONE if merge_result.outcome.merged else TaskStatus.BLOCKED`
  (line 692) — the exact point the new finish-change step hooks into.
- The gate-passed → committing transition is at `machine.py:274-290`: once
  `gate_result.passed` is true, it falls through to `_do_committing`
  directly — the exact point a new review step hooks into, using the same
  `FAILED_RETRY` → `continue` pattern the gate's own `CODE_FAILURE` retry
  already uses (lines 274-288) for a failed review.
- `cosmo queue add` (`src/cosmo/cli/main.py:729-782`) already has the
  logic a new command needs to reuse: cycle detection via `run.dag.
  find_cycle` against every non-`done` task plus the new one
  (`find_cycle`, `src/cosmo/run/dag.py`), then `StoreWriter.queue_add`.
- `openspec` CLI verified for real against the real installed binary:
  `openspec new change <name> [--description] [--goal]` scaffolds a
  change; `openspec instructions <artifact> --change <name>` returns the
  exact template per artifact (`proposal`/`design`/`specs`/`tasks`);
  `openspec archive [change-name]` exists and is what `finish-change`
  calls. `Path(task.spec_path).stem` is already how `run.loop.
  _run_one_task` derives a change's short name from its full path — the
  same derivation gives the name `openspec archive` needs.
- `templates/harness/claude/skills/openspec-workflow/SKILL.md` and
  `templates/harness/claude/agents/implementer.md` are the two existing
  harness-facing files this pattern extends — new skills/agents follow
  their exact shape (YAML frontmatter + focused instructions), the same
  frontmatter mechanism the new `*-task.md` convention below reuses.
- Schema migrations are forward-only, recreate-copy-swap only when a CHECK
  constraint changes (`src/cosmo/store/migrations.py`, 3 migrations as of
  Phase 9); a **plain nullable column with no CHECK constraint** (what
  this plan needs) is a much simpler `ALTER TABLE ... ADD COLUMN`
  migration, no table rebuild required.
- `FakeHarnessAdapter`/`FakeGate` (`src/cosmo/harness/fake/`,
  `src/cosmo/gate/fake.py`) are what every state's tests script against
  instead of a real harness call — the new `REVIEWING` state's tests
  should follow the same pattern instead of inventing a new fake
  mechanism.

## What changes

### 1. New file convention: `docs/specs/` (target repo, Cosmo-enforced)

```
docs/specs/
  add-login-spec.md              <- raw, user-written (step 1)
  add-login-spec/
    tasks/
      backend-task.md            <- one file per fanned-out unit of work
      frontend-task.md
```

- `<name>-spec.md` is the one file the user actually writes by hand (or
  points Cosmo at from anywhere and Cosmo copies it here — see command
  design below). Naming is enforced by Cosmo (`*-spec.md`), not
  OpenSpec's.
- `<name>-spec/tasks/<task>-task.md` is Cosmo's own decomposition output —
  the enriched, split-up result. Each file gets **YAML frontmatter**
  (`task_id`, `depends_on`, `priority`, `title`) plus a markdown body with
  the actual enriched task description, the same frontmatter-plus-body
  shape every skill/agent file in `templates/harness/claude/` already
  uses — no new file format invented, just a new location/purpose for it.
- These files are real, git-tracked content in the target repo — this
  **replaces an earlier draft's JSON-manifest-in-Cosmo's-own-data-dir
  idea**. It's a strictly better fit for "preview-first": the user can
  literally open and hand-edit a `*-task.md` file during the preview
  window before committing it to the queue, not just read a rendered
  table.
- Because the task files carry their own dependency/identity metadata,
  **no OpenSpec change needs to exist yet** at this point. `openspec new
  change` is never called during decomposition — only later, lazily,
  inside each task's own `PROPOSING` state (see below). Decomposition
  touches nothing under `openspec/` at all.

### 2. New task-machine states: `REVIEWING` and `FINISHING`

New ordering: `QUEUED → PROPOSING → PROPOSED → IMPLEMENTING → VALIDATING →
REVIEWING → COMMITTING → MERGING → FINISHING → DONE / BLOCKED`

- **`PROPOSING` gains a new responsibility, not a new contract.** Its
  call signature and retry/classification logic are unchanged
  (`_do_proposing`, `machine.py:344`). What changes is what
  `ctx.spec_path` points at for a task that came from this new flow: a
  `*-task.md` file under `docs/specs/.../tasks/`, not an existing
  `openspec/changes/<name>` folder. The prompt built for
  `adapter.propose()` needs to say "read this task file as your source
  content, then create the OpenSpec change (`openspec new change`) and
  author its artifacts from it" — a prompt change, not a state-machine
  change. A task queued the old way (`cosmo queue add` pointing straight
  at an existing OpenSpec change) keeps working exactly as it does today;
  `PROPOSING` just verifies/refines instead of creating from scratch.
- **`REVIEWING`** (`machine.py`, new `_do_reviewing` alongside
  `_do_committing`): inserted right after `gate_result.passed` is
  confirmed true (line ~289, before the `_do_committing` call). Calls a
  new `adapter.review(...)`-shaped harness invocation — **a genuinely
  fresh, separate `claude -p` call with no memory of the implementation
  session**, given only the diff and the change's spec/tasks.md, so the
  review is real rather than the same session grading its own work. This
  needs one new method on `HarnessAdapter`'s ABC (`src/cosmo/harness/
  base.py`), mirroring how `probe()`/`propose()`/`implement()` already
  exist there. On rejection: record a `task_failures` row and transition
  to `FAILED_RETRY` → back into the `IMPLEMENTING` retry loop, exactly the
  pattern `machine.py:274-288` already uses for a `CODE_FAILURE` gate
  result — same `attempt_count`/`max_attempts` budget, no new ceiling.
  New `FailureStage.ADVERSARIAL_REVIEW` value needed
  (`src/cosmo/store/enums.py` + a migration widening `task_failures.
  failure_stage`'s CHECK constraint — same recreate-copy-swap recipe as
  migration 2 in `migrations.py`, which already did exactly this for
  `SECRETS`).
- **`FINISHING`** (`machine.py`, new `_do_finishing`, called from
  `_do_merging` right before its final `return TaskStatus.DONE ...` at
  line 692, only on the `merged` branch): **v1 scope is exactly one
  thing** — run `openspec archive <spec_id>` (spec_id derived the same
  way `run.loop._run_one_task` already does), nothing else. Purely
  mechanical/scripted (a subprocess call, no new harness invocation),
  matching `COMMITTING`'s own existing precedent of being
  deterministic-only (deviation 16: the implementer agent already does
  knowledge-notes+commit as `IMPLEMENTING`'s own last step). Deliberately
  built as its own named step (not folded into `COMMITTING`/`MERGING`) so
  more can be added later without another state-machine reshuffle.
  **Failure must be best-effort and non-blocking** — the code already
  merged successfully by this point; failing the task retroactively over
  an archive-step problem would be wrong. Log a warning event on failure,
  return `DONE` regardless.

### 3. New CLI front door: `cosmo spec add` / `cosmo spec queue`

Two new commands under a new `spec_app` Typer group in `src/cosmo/cli/
main.py`, following the existing `queue_app`/`events_app` pattern:

- **`cosmo spec add <name> --repo <target-repo>`**: the enrichment +
  decomposition step. If `docs/specs/<name>-spec.md` doesn't exist yet in
  the target repo, this is where the user's raw text becomes that file
  (either they've already written it there directly, or point the command
  at an external file to be copied in under the enforced name). Drives a
  fresh harness invocation running a new `skills/spec-enrichment/
  SKILL.md` (reads `docs/backend/`, `docs/frontend/`, `docs/data-model.md`,
  `docs/base-standards.md` — the exact files `templates/harness/claude/
  CLAUDE.md`'s own "Project knowledge" section already names) against the
  raw spec, and writes one `docs/specs/<name>-spec/tasks/<task>-task.md`
  file per identified unit of work, each with `depends_on` frontmatter
  declaring the dependency graph. **Does not touch `task_queue` or
  `openspec/` at all.** Prints the resulting task list + dependency graph
  read back from the frontmatter, mirroring `cosmo run --dry-run`'s
  rendering — the preview.
- **`cosmo spec queue <name>`**: scans `docs/specs/<name>-spec/tasks/
  *-task.md`, parses each file's frontmatter, and inserts one task per
  file into the real queue — reusing `queue_add`'s exact cycle-check +
  `StoreWriter.queue_add` logic (extract that block into a shared helper
  both this command and the existing `queue_add` call, rather than
  duplicating it). `spec_path` for each task is the `*-task.md` file's own
  path. Tags each inserted row with `spec_batch_id = <name>-spec` (the
  batch id is just the spec's own name — no separate opaque id to invent
  or track). A human can hand-edit any `*-task.md` file (content or
  `depends_on` frontmatter) between `spec add` and `spec queue` — that
  edit window is the preview, not a separate confirmation UI.

`cosmo queue add <path>` **stays**, unmodified, as the existing low-level
path (a user who already has a real OpenSpec change can still queue it
directly) — not removed, just no longer the documented front door.

### 4. Schema: one small additive migration

`task_queue.spec_batch_id TEXT NULL` — migration 4 in `migrations.py`, a
plain `ALTER TABLE task_queue ADD COLUMN spec_batch_id TEXT`, no CHECK
constraint involved, no recreate-copy-swap needed. Lets `cosmo report`/a
future `cosmo spec status <name>` group tasks that came from the same raw
spec. `StoreWriter.queue_add` gains an optional `spec_batch_id` parameter
(defaulting to `None`, same additive-default convention every prior phase
has used for new columns).

### 5. New harness-facing templates

- `templates/harness/claude/skills/spec-enrichment/SKILL.md` — new,
  documents the exact `*-task.md` frontmatter schema it must emit
  (`task_id`, `depends_on`, `priority`, `title`) and where the files go.
- `templates/harness/claude/agents/reviewer.md` — new, the adversarial
  reviewer. Explicitly instructed to be skeptical, given only the diff +
  spec (not implementation chat history), and to say precisely why on
  rejection (same "your summary is the only place that context survives"
  discipline `implementer.md` already states).
- Config: new `[review]` section in `config/model.py` +
  `defaults.toml` — `enabled: bool` (default `true`, but a project should
  be able to turn this off) and reuse of `retries.max_attempts` for the
  budget per the decision above (no separate ceiling to invent).

### 6. What does *not* need to change

`run.loop.run_queue`, the DAG scheduler, circuit breaker, quota/cost
machinery, `cosmo run`, `cosmo report`, `cosmo events tail`, `cosmo queue
failures` — all already generic over however many tasks are in the queue.
Once `cosmo spec queue` has inserted N tasks with real `depends_on` edges,
every downstream piece of Phase 5-9 machinery needs zero changes. This is
the strongest argument for the design: the expensive, already-hardened
parts of Cosmo stay untouched.

## Verification (once implemented)

- Fake the enrichment skill's output (a fixture set of `*-task.md` files
  with frontmatter, written directly by the test rather than through a
  real harness call) and the reviewer's accept/reject result the same way
  `FakeHarnessAdapter` fakes `propose`/`implement` today, for fast unit
  tests of `_do_reviewing`/`_do_finishing` and the `spec add`/`spec queue`
  CLI glue.
- Real invocations (matching this codebase's own established discipline
  of never trusting a mocked green alone): a real `openspec archive` call
  against a real change; a real reviewer-agent invocation against a
  deliberately-planted bug, to confirm it actually catches something
  rather than rubber-stamping; and a full real `cosmo spec add` → `cosmo
  spec queue` → `cosmo run` walkthrough against `tests/fixtures/
  gate_repo`, the existing real Spring Boot + Vite/React fixture.
- `./check.sh` (ruff + format + mypy --strict + pytest) must stay green
  throughout, same as every prior phase.
