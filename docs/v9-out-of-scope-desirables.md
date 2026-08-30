# Cosmo — v9: out of scope, deferred, and open design decisions

## Status

**Tracking document, not a plan** — a consolidated pointer to every place
the project has, on purpose, declared something not-built. None of this is
newly decided here; every item traces to the spec's own §12, a later vN
plan's own Status line, or a specific decision recorded in
`v3-implementation-state.md`. Collected into one place (2026-08-28) because
that context was previously scattered across five documents with no single
page answering "what did we deliberately not build, and why." Update an
entry in place if it ships (move it out, don't just delete the line —
future readers benefit from knowing it *used* to be out of scope), and add
new entries here rather than letting `docs/handoff.md` accumulate them
session over session.

## Non-Goals (v1) — spec §12

The spec's own canonical list
([v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md)
§12). Still accurate except the one exception noted below.

- Web dashboard — CLI/terminal only, no code exists.
- Any harness other than Claude Code CLI — the harness abstraction exists
  (`HarnessAdapter` ABC) but only one real implementation
  (`ClaudeCodeAdapter`) has ever been written.
- Parallel task execution — everything runs serially. The worktree design
  (spec §3.2) already removes the largest structural blocker, but real
  runtime isolation (port allocation, per-task DB namespacing, `/dev/shm`
  budgeting across concurrent browser instances) doesn't exist.
- Automatic merge into `master` — by design, permanently manual, not a
  "not yet built" item. `develop` is as far as Cosmo's own merge goes.
- Resuming partial in-flight harness work after a crash — a crash during
  `IMPLEMENTING`/`VALIDATING` restarts that state from scratch on the next
  `cosmo run`. `session_id` is already captured and persisted
  specifically so this can be added later with no schema change, but nothing
  reads it back yet.
- Full OpenTelemetry span-tree migration — blocked on the GenAI semantic
  conventions reaching stable rather than *Development*; the existing
  event envelope (spec §9.1) is shaped so this becomes a mapping exercise
  later, not a rewrite.
- Automated flaky-test quarantine — proposal-only; a human still approves
  every quarantine, nothing auto-quarantines.
- Template token substitution (spec §10.6) — template copying is
  literal, no `{{project_name}}`-style placeholders resolved at copy time;
  today's workflow is copy-then-hand-edit.

**One exception**: the spec's §12 list also originally named Telegram/any
real-time notification channel as out of scope for v1. That was
superseded by the later [v5-improvements-plan.md](v5-improvements-plan.md)
and shipped for real in deviation 79 (human-readable event formatting, the
`cosmo notify config` setup wizard, real Telegram delivery confirmed via
`cosmo-notify.service`). It's the one item on the spec's own non-goals
list that has since actually been built — kept here so the exception is
visible next to the rule, not silently dropped from the list.

### Recorded for later, deliberately deferred (spec §12)

1. **Parallel task execution.** The worktree decision removes the
   structural blocker. Remaining work is runtime isolation: port
   allocation, per-task database namespacing, and `/dev/shm` budgeting
   across concurrent Chromium instances.
2. **Full OTel span-tree migration.** Blocked on GenAI semantic
   conventions reaching stable. The event envelope is shaped to make this
   a mapping exercise rather than a rewrite.
3. **Partial mid-state resumption.** `--resume` with the persisted
   `session_id`, combined with OpenSpec's own resume-from-first-unchecked-
   task behavior, would avoid restarting long applies from scratch.
   `session_id` is already captured so this needs no schema change to add
   later.
4. **Template token substitution.** Placeholder-based variable
   substitution (`{{project_name}}`, etc.) at template-copy time.
5. **A "Chaos" sibling agent.** No design work has started; recorded only
   because it motivated part of the naming in the spec's own Overview.

### Open Items for Follow-Up Specs (spec, end of document)

1. `PreToolUse` hook implementations and the diff-gate assertion-counting
   heuristic, done properly per-language (JUnit/AssertJ for Java,
   Vitest/Playwright for TS). **Still a regex heuristic today** — the real
   diff gate (`gate/diffgate.py`) counts `assertThat(`/`assert[A-Z]\w*(`/
   `expect(` call sites on added vs. removed lines rather than parsing a
   real AST. Fails safe (a real violation can slip through; honest work
   never gets a false failure), but a from-scratch
   JUnit/AssertJ/Vitest/Playwright AST parser was explicitly out of scope
   for what could be verified by hand when Phase 6 built this.
2. Empirical retuning of spec §3.3 timeouts once real p95 gate-duration
   data exists. **Partially answered now** — see
   [v8-validations-for-later.md](v8-validations-for-later.md)'s
   `REVIEWING`/`VALIDATING` entry for the real numbers; still an open
   human decision, not yet acted on.
3. Quarantine ownership and expiry policy, and the escalation path for
   when `quarantine-candidates.yml` grows. Untouched since the spec was
   written.
4. Concrete contents of `templates/harness/claude/` (the actual hooks,
   agent definitions, and skills, not just their placement). Largely
   overtaken by real implementation since the spec was drafted — worth a
   sanity check against what actually shipped before treating this as
   still open, rather than assuming the spec's original framing still
   applies.
5. SQLite schema DDL and the Claude Code CLI adapter implementation. Both
   long since built; kept here only for the spec's own historical
   completeness, not because either is still open.

## Later plans' own still-open items

- **[v6-project-template-aware-stuff-plan.md](v6-project-template-aware-stuff-plan.md)
  — not started, design record only.** Making the gate and its failure
  classifier project-template-aware, for stacks beyond the spec's own
  fixed target (Java+Spring backend, Vite+React frontend, a conventional
  `backend/`/`frontend/` monorepo layout — the spec names this stack but
  never specifies concrete build images, commands, or directory
  conventions beyond it). A repo that doesn't follow that exact layout, or
  uses a different build tool, is out of scope until this generalizes it
  (most likely via a per-repo manifest). The plan's own Status line says
  it needs a real second stack to prove the abstraction before it's
  buildable — the user is doing that second-stack testing themselves, then
  will come back to it. Don't start it opportunistically.

- **[v7-complete-queue-done-fixes-plan.md](v7-complete-queue-done-fixes-plan.md)
  item 4 — spec-authoring parallelism, still open.** Whether future spec
  batches should be authored with more independent parallel branches (so a
  blocked task doesn't stall the whole chain behind it) is a question for
  the *next* spec batch's authoring, not code. Partly answered already:
  the scheduler (`run.dag.resolve_execution_order` +
  `run.loop.run_queue`'s main loop) already interleaves independent
  branches correctly when a task blocks, since it recomputes the full
  eligible set every iteration rather than working one task ahead —
  `todo-frontend-app`'s own spec batch never exercised this for real only
  because its chain had no independent branch to begin with. The one real
  exception, still open, not just unexercised: a circuit-breaker trip
  pauses the *whole* run, independent branches included, by design (spec
  §6.5).

## Real implementation-time decisions still standing

Found and recorded during actual phase implementation (not from the
original spec's own non-goals list), still true as of this writing:

- **`HeartbeatSource.STREAM` is never produced.** `MTIME` is reused for
  both real file-mtime polling and native-progress polling — the schema
  has a `STREAM` value but nothing produces it. Realizing it needs an ABC
  change (a progress/event callback parameter on `HarnessAdapter.
  implement()`/`propose()`, exposed even for an adapter that declares
  `supports_structured_stream=True`), out of scope for the phase that
  found the gap; recorded for whichever future phase revisits the harness
  interface (`v3-implementation-state.md`'s cumulative deviations table,
  entry 17).
- **Per-stage container cache mounts (`~/.m2`, npm's cache) are not
  implemented.** Every gate-stage container run is `--rm` with a cold
  dependency cache, so a fresh `mvn`/`npm` resolution dominates real gate
  runtime (observed: ~2m40s of a real integration-test run was almost
  entirely cold dependency resolution). Noted as "the natural next item"
  as far back as Phase 6 and reconfirmed at the end of Phase 7; still
  unimplemented.
- **One project/repo per `cosmo run` (DAG mode).** `task_queue` has no
  `project_id`/repo column, so a single `cosmo run` invocation resolves
  its DAG against exactly one registered project. This is a decided,
  documented v1 posture, not an oversight — a multi-project run would need
  either a schema column or a per-task resolution step that nothing built
  so far requires. Revisit only if a real need for one run to span
  multiple registered repos shows up.
