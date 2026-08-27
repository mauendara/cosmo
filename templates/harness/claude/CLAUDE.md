# Operating policy -- Cosmo-managed repository

This repository is driven unattended by **Cosmo**, an orchestrator that runs
OpenSpec's propose/apply flow overnight with no human watching. You are being
invoked headless (`claude -p`), once per Cosmo state, with no chance to ask a
clarifying question. When in doubt, make the smallest reasonable choice,
record it, and keep moving -- do not stall the run.

## The one rule that matters most

**The validation gate is the only source of truth about correctness.**
Checking a box in `tasks.md`, printing "done," or reporting success does not
make a task done -- Cosmo runs a real build/test/e2e gate afterward, on its
own, outside this session. Nothing you say here is taken on faith. This means
there is no incentive to make output *look* successful; there is only an
incentive to make the code actually pass the gate. Padding a summary,
skipping a step, or declaring victory early only wastes your own turn budget.

## This call is one-shot -- there is no "later"

`claude -p` returns exactly once, the moment this turn ends, however it
ends -- a final text response, or a tool call that implicitly assumes
you'll be resumed. Nothing schedules a second call into this same session;
Cosmo's adapter is done listening the instant this process exits.
Concretely: **a tool like `ScheduleWakeup` does nothing useful here.** It
exists for interactive/managed sessions with a host that keeps running --
found by hand: a real `IMPLEMENTING` session launched `npm install` in the
background, correctly reasoned it should wait rather than poll, called
`ScheduleWakeup`, and ended its own turn on the assumption it would resume
once notified. It never did. The install was still running when the
process exited; `package-lock.json` was never written; the next state's
gate run failed on a confusing `npm ci` error that had nothing to do with
the actual code. If a command takes a while, wait on it synchronously in
this same turn -- foreground it, or if it must background, poll it
yourself until it actually finishes -- before doing anything that depends
on its result, and before ending the turn.

## How to drive OpenSpec

Do not guess OpenSpec's workflow -- ask it directly, every time:

```
openspec status --change <change-id>            # what's done, what's next, what's blocked
openspec instructions <artifact> --change <id>   # exact task + template for one artifact
```

`<artifact>` is one of `proposal`, `specs`, `design`, `tasks` during propose,
or work through `tasks.md` directly during implement. The `instructions`
output is self-contained: task description, dependencies to read first, and
the exact template/checkbox format expected. Follow it, don't improvise a
different structure -- Cosmo's progress watcher parses `tasks.md` looking for
literal `- [ ] N.M Description` / `- [x] N.M Description` lines (spec 4); a
task not in that format is invisible to it.

- Check off a box only when that specific subtask is actually done. The
  count moving backwards mid-run because you split a task into more subtasks
  is fine and expected -- Cosmo tracks numerator/denominator separately for
  exactly this reason. Don't check a box speculatively to "look ahead."
- If a later subtask reveals the plan needs to change, edit `tasks.md`
  accordingly rather than forcing the original plan to fit.

## Guardrails -- read this before your first edit

Several actions are blocked before they execute, regardless of what you
intend by them. These are enforced by `PreToolUse` hooks and
`permissions.deny` rules in this repo's `.claude/settings.json`
(`.agent/claude/settings.json` is the real file; do not edit either --
Cosmo regenerates this directory wholesale on every task and your edit
will be silently discarded on the next sync):

| Action | What happens | Why |
|---|---|---|
| Editing a file under `src/test/**`, `e2e/**`, or matching `**/*.spec.ts(x)` / `**/*.test.ts(x)` / `**/*.spec.jsx` / `**/*.test.jsx` | Denied, unless this task was explicitly flagged `allow_test_edits` | The tests are the thing being measured. If a task genuinely requires touching tests, that should already be reflected in how the task was queued -- it is not something to work around from inside a session. |
| Introducing `@Disabled`, `@Ignore`, `test.skip`, `it.skip`, `describe.skip`, or `xit(...)` anywhere | Denied | Disabling a test to make a suite pass is the same failure mode as deleting it. If a test is failing, fix the code or fix the test's assertions -- don't silence it. |
| `git commit --no-verify` | Denied | Bypasses local pre-commit secret scanning. |
| `git push` (any form, including force variants) | Denied | Pushing is Cosmo's job, run after the validation gate passes, not yours. |
| `git reset --hard` | Denied | Can silently discard work Cosmo has not yet evaluated. |
| Reading `.env*`, `secrets/**`, `*.pem`, `id_rsa*` | Denied | A secret you cannot read is a secret you cannot leak into a commit or a log. |

None of these are things to retry with a different phrasing or a shell
one-liner that avoids the pattern-match -- they are policy, not a puzzle.
If a task's own goal appears to require one of these (e.g. it looks like the
task is "add a test"), that is a legitimate reason to leave the corresponding
subtask unchecked and move on; Cosmo's retry/blocked machinery is what
decides what happens next, not a workaround from inside this session.

## Committing

Committing itself (without `--no-verify`) is allowed and expected as part of
finishing implementation work -- write a normal commit describing the change.
Do not push. Do not merge. Cosmo owns both after the validation gate passes.
Commits carry no `Co-Authored-By: Claude` trailer (`.claude/settings.json`
sets `attribution.commit` to an empty string) -- an unattended loop writing
every commit itself has no separate human co-author to credit, and Cosmo's
own deterministic commits (the `COMMITTING` step's decisions-log entry,
merge/rebase commits, spec 3.4) already carry no such trailer either. This
is not something to work around by adding the trailer back by hand.

## Toolchain versions -- pin, don't take "latest"

The validation gate builds inside a fixed Docker image per language/stack
(`gate.backend_image`/`gate.frontend_image` in Cosmo's own config -- if you
need the exact tag, check an existing `package.json`/`pom.xml`/lockfile
already committed in this repo from a prior task, or this project's own
`docs/`). A package manager's "latest" is not pinned to that image and can
silently outpace it over time -- letting `npm install`/`pip install`/etc.
resolve whatever is newest today is how a scaffold looks fine in this
session but fails the gate's build stage with an opaque native-binding or
version-resolution error instead of a clear "incompatible version" message.
If this project's own `docs/` names a specific version or major range for a
dependency, follow it exactly rather than defaulting to whatever a package
manager resolves on its own; if it doesn't name one, prefer an older,
already-widely-supported major over the newest release. Always commit the
real lockfile your install step actually produced (`package-lock.json`,
etc.) -- the gate's install step is a clean, from-scratch one (e.g.
`npm ci`), which fails outright without one; hand-authoring a manifest
without running the real install command is the same mistake.

## Project knowledge

`docs/` in this repository holds architecture and decision notes that are
project-specific and persist across every task. Its exact shape depends on
the project template this repo was initialized from -- a backend-and-frontend
stack has `docs/backend/` and `docs/frontend/`, a frontend-only one may only
have `docs/frontend/` and a top-level `docs/persistence.md`, and so on. Skim
whatever `docs/` actually contains before your first edit rather than
assuming a fixed set of files; `docs/base-standards.md` and
`docs/data-model.md` are the two names common across templates,
`docs/decisions-log.md` exists only if a prior task already created one. If
a task you complete establishes a
constraint or decision future tasks need to know about, append 2-3 lines to
the relevant file -- and if it contradicts something already written there,
revise that line rather than stacking a contradiction beneath it. These files
have a line cap; if you're about to exceed it, say so in your summary instead
of trimming older content yourself.

Do not narrate "what happened" into these files -- that already lives in git
history and Cosmo's own event log. Only write what future tasks would
otherwise have to rediscover.
