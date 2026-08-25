# Handoff — continue at Phase 7

You are picking up Cosmo mid-build. Phases 0-6 are complete. Your job is
Phase 7: the full task state machine — `QUEUED` through `DONE`,
`FAILED_RETRY`/`BLOCKED`, per-state timeouts, the progress watcher, the
heartbeat, the failure classifier, informed retries, and `COMMITTING`'s
spec 11 knowledge-file step.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth.** v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map. Phase 7 is your scope (§3.2, §3.3, §4, §6.2, §6.3, §11) |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Phase 6 — Complete" section in full before writing code — several of its decisions are load-bearing for Phase 7 |

`v1-*` and `v2-*` in this folder are earlier spec drafts. v3 is a superset of
both. Do not implement from them.

**Do not edit the plan document.** It is the agreed scope. Record what you
build, and any decision you make along the way, in `v3-implementation-state.md`.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the four documents above
├── templates/                  # Phase 4: harness + project templates (source of truth)
│   ├── harness/claude/           # CLAUDE.md, settings.json, hooks/, agents/, skills/
│   └── projects/{_blank,java-spring-react}/docs/
├── src/cosmo/
│   ├── checks.py                 # CheckResult / CheckStatus
│   ├── config/                   # typed model, defaults.toml, three-layer loader
│   ├── doctor.py                  # core preflight checks
│   ├── harness/                  # base ABC (+cwd, +probe), registry, claude/, fake/
│   │   └── fake/                   # FakeHarnessAdapter -- target this in every new test
│   ├── bootstrap/                 # Phase 4: template discovery, sync, symlinks, cosmo init
│   ├── git/                      # Phase 5: worktree lifecycle, gitleaks (hook + gate backstop), merge ladder
│   │   ├── worktree.py             # create_worktree/remove_worktree/sweep_stale_worktrees
│   │   ├── secrets.py              # install_gitleaks_pre_commit_hook, run_gitleaks_scan (Phase 6)
│   │   └── merge.py                # attempt_merge_ladder/merge_task -- GateRerun is your real caller now
│   ├── gate/                     # Phase 6: the Docker validation gate -- COMPLETE, this is your caller
│   │   ├── runner.py               # run_validation_gate -- pure mechanics, the whole build->unit->e2e sequence
│   │   ├── validate.py             # validate_task -- ties runner to StoreWriter/EventEmitter; NOT YET CALLED from anywhere real
│   │   ├── fake.py                 # FakeGate, ScriptedGateResult, FakeGate.as_gate_rerun() -- target this in tests
│   │   └── types.py                # GateResult, StageResult, TestCounts, etc.
│   ├── store/                    # SQLite schema, StoreWriter, reader queries (Phase 1; task_failures/task_progress/task_heartbeat all unused until now)
│   ├── events/                   # envelope + EventEmitter, transactional sequence (Phase 1)
│   ├── proc/                     # ManagedProcess (+on_stdout_chunk), WallClockTimer/StallTimer (Phase 2), orphan sweep, reap
│   ├── cli/main.py               # `cosmo` command: config, harness, doctor, queue, events, project, init, templates, validate
│   └── {task,run,knowledge}/     # EMPTY — later phases (task is yours)
├── tests/                       # 238 passing + 6 opt-in real-Docker (COSMO_GATE_DOCKER_E2E=1)
│   └── fixtures/gate_repo/        # real Spring Boot + Vite/React fixture -- Phase 6's, reusable for your end-to-end test too
└── check.sh                     # ruff + format + mypy --strict + pytest
```

`src/cosmo/task/` is empty and is exactly where Phase 7's state machine
goes. `src/cosmo/run/` (Phase 8, DAG scheduling) and `src/cosmo/knowledge/`
(also partly Phase 7 — the §11 knowledge-file step) are still empty.

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # Phase 6 should be committed at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # core checks + harness checks in two tables
```

**Known, pre-existing environment noise on this host** (not something Phase
7 broke, don't chase it): `cosmo doctor` may show `disk space: FAIL` — this
WSL2 box runs close to the 10 GB floor at the *test* data path it checks
(`/tmp` is a small tmpfs on this box); the real filesystem has hundreds of
GB free, confirmed by hand in Phase 6. This box has no *global* git identity
either — only this repo's own local config has one — so any test fixture
your own work adds that calls `git commit` needs `-c user.name=...`/`-c
user.email=...` passed explicitly (see `tests/test_git_merge.py`'s `_git`
helper, or `tests/test_gate_diffgate.py`'s). `gitleaks` is installed at
`~/.local/bin/gitleaks` (on this shell's PATH via the profile) and `docker`
works — Phase 6 pulled and ran real Maven/npm/Playwright containers on this
box repeatedly; see its state-doc section for two real environment gotchas
worth knowing before you touch anything Docker- or npm-related again:
**`npm install` can hang indefinitely and non-deterministically on this
host if a previous run was killed mid-install, unrelated to network** (the
fix is a verified-clean `rm -rf node_modules package-lock.json` before
reinstalling, not waiting longer or blaming the network), and **Docker
containers write bind-mounted build artifacts as root**, which blocks a
later unprivileged `rm -rf` of `target/`/`node_modules` (worked around with
a throwaway `alpine` container to clean up as root). Phase 6's state-doc
section has the full diagnosis for both — read it before you burn an hour
rediscovering either one.

One more: **this session's shell has `XDG_DATA_HOME=/tmp/cosmo-test/data`
set** (sandboxing `cosmo`'s own runtime state away from the real home
directory — this is also why `cosmo doctor`'s disk-space check reads a
small tmpfs, see above). `uv` itself respects this same variable for where
it stores *tool* venvs, though -- if you ever need to run `uv tool install
--editable .` again (e.g. after adding a dependency, which is exactly what
broke the global `cosmo` command mid-Phase-6), run it as `env -u
XDG_DATA_HOME uv tool install --editable --force .` or it will silently
reinstall into `/tmp/cosmo-test/data/uv/tools/cosmo` instead of the real
`~/.local/share/uv/tools/cosmo` the existing `~/.local/bin/cosmo` symlink
points at, leaving the symlink dangling. `uv run cosmo ...` (this project's
own `.venv`) is never affected by this and is the more reliable invocation
for anything scripted.

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
- **Validators catch what would fail silently.** See the existing
  timeout-below-wall-clock, floating-tag, and quarantine-expiry validators
  for the pattern: reject or exclude at the source what would otherwise
  misbehave or drift silently.
- **Tests isolate from the developer's environment.** Anything touching config
  must set `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths — see the autouse
  fixture in `tests/test_cli.py`. Anything touching a real git repo should
  build one in `tmp_path`, never touch this repo or a real target repo.
- **Fake the external process, test the mechanics — except where "check by
  hand, then use the real thing" already proved out.** `FakeHarnessAdapter`
  and `FakeGate` are the two test doubles later phases should target
  directly rather than reimplementing their own. Real-process/real-Docker
  tests exist (`tests/test_git_secrets.py`'s real-gitleaks tests,
  `tests/test_gate_fixture_e2e.py`'s real-Docker gate runs) but are
  skip-guarded — the first on binary-not-on-PATH, the second on an explicit
  opt-in env var (`COSMO_GATE_DOCKER_E2E=1`) because a real gate run takes
  minutes even warm. Follow this same posture for Phase 7: fake
  harness/gate for the state-machine unit tests, one real end-to-end task
  against the real adapter and real gate for the exit criterion.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`,
  `test_store_boundary.py`, `test_git_boundary.py`, and (new in Phase 6)
  `test_gate_boundary.py` all enforce structural invariants via `ast`
  inspection, not text search. Check whether anything you add to
  `src/cosmo/task/` needs a similar boundary test — the task state machine
  legitimately imports *both* `cosmo.harness` and `cosmo.gate` (it's the
  first module allowed to import both), so no new import ban is obviously
  needed here, but re-read the existing four before assuming that.
- **Run `./check.sh` before committing.** All four must pass.
- **When something fails, check with a real invocation before trusting a
  unit test's green.** This has found a real bug or made a real design
  decision correctly in every phase so far. Phase 6 alone found: a
  diff-gate bug that rejected every newly-added test file (only caught by
  running a real scenario, not by the unit tests, which had been written to
  match the buggy behavior); `npm ci`'s stdout breaking JSON parsing when
  combined with Vitest's own stdout on the same stream; Vite 5's
  `preview.allowedHosts` guard blocking the entire e2e stage; and the
  `npm install` hang described above. None of these were predictable from
  reading the spec — all six required fixture scenarios (green run, compile
  failure, unit failure, e2e failure, weakened test, injected flaky test)
  were run for real against a real Docker daemon before Phase 6 was called
  done. Do the same for Phase 7: at minimum, one real task should be driven
  through every state against the real `FakeHarnessAdapter`+`FakeGate`
  pair, *and* the plan's own integration exit criterion (one real task
  against the real adapter and real gate on the fixture repo) should
  actually be run, not just asserted possible.

## Phase 7 scope

Spec §3.2 (task state machine), §3.3 (timeout values), §4 (progress &
liveness), §6.2 (failure types), §6.3 (per-task retries), §11 (knowledge
management — the `COMMITTING` step specifically).

Summary from the plan:

1. **State machine**: `QUEUED → PROPOSING → PROPOSED → IMPLEMENTING →
   VALIDATING → COMMITTING → MERGING → DONE`, with `FAILED_RETRY` and
   `BLOCKED`, every transition persisted (`task_transitions`, already
   schema'd and partially exercised by `queue_add`/`queue_block`/
   `queue_retry`/`queue_complete` — Phase 5's note: those four write plain
   transitions without a paired `task.state_changed` event; Phase 7 owns
   making every transition emit one) and emitting `task.state_changed`.
2. **Per-state timeouts** wired to `proc.timers`' `WallClockTimer`/
   `StallTimer` (Phase 2, unused as *state-machine* timers until now — Phase
   3 used them for the harness probe's own ad hoc timeout, not this) with
   spec §3.3's exact semantics — in particular, **`VALIDATING` timeouts do
   not consume the code-level retry budget** (a hanging gate is an
   environment problem — this already matches how Phase 6's
   `run_validation_gate` classifies a stage timeout as
   `FailureType.ENVIRONMENT_ERROR`, not `CODE_ERROR`; the state machine
   should trust that classification rather than re-deriving it), while
   `IMPLEMENTING` timeouts do count.
3. **Progress watcher** (§4): `watchdog`/inotify on the change's
   `tasks.md`, polling fallback at 5-10s. Store numerator and denominator
   separately, never percent alone (`task_progress` is already schema'd for
   exactly this). Debounced writes through `StoreWriter.submit()`/`drain()`
   (Phase 1's cross-thread handoff, exercised in `test_store_writer.py`
   but with no real background-thread caller yet — this is that caller).
4. **Heartbeat** (§9.2) with an explicit `source: stream | file | mtime`
   (`task_heartbeat` schema and `HeartbeatSource` enum already exist);
   mtime fallback where `supports_structured_stream` is false
   (`HarnessCapabilities.supports_structured_stream`, Phase 3).
5. **Failure classifier** producing the §6.2 quadrant. Most of this already
   exists as a *result*, not yet as a *decision*: `HarnessResult.success`
   (Phase 3) tells you the harness's own verdict, and Phase 6's
   `GateResult.failure_type`/`failure_stage` already do this classification
   for the `VALIDATING` state specifically. What Phase 7 adds is the
   equivalent classification for `PROPOSING`/`IMPLEMENTING` failures (a
   harness process failure, a timeout, a rate-limit signal) and the
   retry-vs-block decision that `gate.validate_task` deliberately left as a
   "conservative placeholder" (see its docstring) pending this phase's real
   circuit-breaker-aware logic.
6. **Informed retries** (§6.3): the retry prompt carries the previous
   `error_detail` (already a first-class field on `GateResult` and on
   `task_failures`, spec 9.3) plus `previous_attempts_summary`, passed as
   `retry_context` to `HarnessAdapter.implement()` (already a parameter,
   Phase 3 — `supports_retry_context` is declared but nothing has passed a
   non-`None` value yet). 30-60s delay between attempts
   (`config.retries.delay_min`/`delay_max`, already exist). Stage-varying
   budget: build/compile failures get the full budget; e2e failures pass
   through §6.4 (Phase 6's `confirm_by_rerun`, already wired inside the
   gate itself) before consuming an attempt at all — this already happens
   *inside* `run_validation_gate`, so by the time Phase 7 sees a `VALIDATING`
   failure, flaky e2e failures have already been filtered out; only genuine
   `code_error`/`environment_error` reach the state machine.
7. **`COMMITTING`'s spec 11 knowledge step**: append 2-3 lines to the
   relevant `docs/` file as an edit/reconcile instruction (revise a
   contradicted line, don't stack contradictions), append a structured
   `decisions-log.md` entry, and fail `COMMITTING` if a knowledge file
   exceeds its 400-line cap (`config.knowledge.max_file_lines`, already
   exists, Phase 0). `src/cosmo/knowledge/` is empty — this is its first code.
8. **No mid-state resumption** (§3.2) — but `session_id` is persisted
   already (`task_queue.session_id`, Phase 1 schema; `HarnessResult
   .session_id`, Phase 3) so deferred item 3 needs no schema change later.

### Exit criteria (from the plan)

- `cosmo run --task <id>` drives one task through every state against
  `FakeHarnessAdapter` + `FakeGate`, with a complete event trail.
- Tests: retry exhaustion → `BLOCKED` with correct `blocked_reason`;
  environment error does not consume an attempt; `VALIDATING` timeout does
  not consume an attempt; a checkbox count that shrinks mid-run does not
  produce a nonsense percent.
- One real end-to-end task against the real adapter and real gate on a
  fixture repo. *(Integration — `tests/fixtures/gate_repo` already exists
  for this; you may not need a second fixture.)*

## Things to know before you start

**Nothing before Phase 8 should implement circuit-breaker trip logic or run
scheduling** — unchanged since Phase 3's handoff, still applies. Phase 7 is
about one task's state machine being correct — not about what a multi-task
run loop does with several of them, or when it pauses.

**`gate.validate_task`'s retry/next-action logic is explicitly provisional**
— read its docstring (`src/cosmo/gate/validate.py`) before assuming it's
Phase 7's real decision. It applies spec 6.2/6.3's literal rule
(`code_error` counts, `environment_error` doesn't) but does not know about
the circuit breaker (§6.5, Phase 8) or about `attempt_number`/`max_attempts`
in the way the real state machine will track them across `PROPOSING` and
`IMPLEMENTING` too, not just `VALIDATING`. Decide whether Phase 7 calls this
function as-is (accepting its placeholder policy for `VALIDATING`
specifically) or whether the state machine's own classifier should
subsume it — either is defensible, but document whichever you choose.

**The merge ladder (`git.merge.merge_task`) has a real, tested `gate_rerun`
parameter but no real caller yet** — same seam shape as Phase 4's
`sync_harness_assets(run_id=...)` before Phase 5 gave it one, and as
`GateRerun`/`FakeGate.as_gate_rerun` (Phase 6) before Phase 7 gives it one.
`MERGING`'s real handler is very likely this phase's second real caller
of `run_validation_gate` (via `FakeGate.as_gate_rerun`-shaped closure,
or the real gate wrapped the same way) — re-read `git/merge.py`'s module
docstring before wiring this.

**Phase 6's `run_validation_gate` already absorbs a fair amount of what
looks like Phase 7 scope** — flaky-test confirm-by-rerun, the diff gate,
the gitleaks backstop, and stage-level `FailureType`/`FailureStage`
classification all happen *inside* the gate, before Phase 7 ever sees a
result. Don't re-implement any of this in the state machine; call
`gate.validate_task` (or `FakeGate.validate` in tests) and trust its
`GateResult`.

**Worktree cleanup after a real gate run may hit root-owned files** (Phase
6 decision 11, state doc) — if `remove_worktree` fails or behaves oddly
after `VALIDATING` runs a real gate in your end-to-end test, this is the
first thing to check, not a new bug.

## When you finish

1. `./check.sh` green.
2. Update `v3-implementation-state.md`: mark Phase 7 complete, list what
   exists, record every decision made and anything a future session would
   otherwise rediscover. Append any new spec deviation to the cumulative
   table at the bottom (next number is 16).
3. Commit to `develop` with a message explaining *why*, in the style of the
   Phase 0-6 commits.
4. Rewrite this handoff for Phase 8 — or delete it if the next session
   continues immediately.

Phase 8 is next: the run loop itself (DAG scheduling over the task queue,
dependency ordering), the global circuit breaker (§6.5 — explicitly not
Phase 7's job), quota detection and the dollar-cost hard stop (§7), and
tying `sync_harness_assets`/worktree creation/the merge ladder/the gate
into one coherent per-task pipeline the run loop drives task by task.
