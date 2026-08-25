# Handoff — continue at Phase 2

You are picking up Cosmo mid-build. Phases 0 and 1 are complete. Your job is
Phase 2: process supervision — process-group kill semantics, orphan sweep,
and timers.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth.** v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map. Phase 2 is your scope |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Things that will matter later" section under Phase 1 before writing code |

`v1-*` and `v2-*` in this folder are earlier spec drafts. v3 is a superset of
both. Do not implement from them.

**Do not edit the plan document.** It is the agreed scope. Record what you
build, and any decision you make along the way, in `v3-implementation-state.md`.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the four documents above
├── src/cosmo/
│   ├── checks.py                # CheckResult / CheckStatus
│   ├── config/                  # typed model, defaults.toml, three-layer loader
│   ├── doctor.py                 # core preflight checks, incl. the store's schema check
│   ├── harness/                  # base ABC, registry, claude adapter (stub)
│   ├── store/                    # SQLite schema, StoreWriter, reader queries (Phase 1)
│   ├── events/                   # envelope + EventEmitter, transactional sequence (Phase 1)
│   ├── cli/main.py               # `cosmo` command: config, harness, doctor, queue, events, project
│   ├── proc/                     # EMPTY — Phase 2 fills this
│   └── {git,gate,task,run,knowledge}/   # EMPTY — later phases
├── tests/                       # 71 passing
└── check.sh                     # ruff + format + mypy --strict + pytest
```

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # Phase 1 should be committed at HEAD
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # should print two tables; disk may warn/fail depending on host free space
```

If `cosmo` is not on PATH, run `uv tool install --editable .` from the repo root.
Editable means your source edits are live — no rebuild between changes.

Try the Phase 1 surface before touching anything, so you have a mental model
of what state already exists to build on:

```bash
mkdir -p /tmp/cosmo-try/target-repo
cosmo project register /tmp/cosmo-try/target-repo
cosmo queue add openspec/changes/add-foo/proposal.md --task-id add-foo
cosmo queue add openspec/changes/add-bar/proposal.md --task-id add-bar --depends-on add-foo
cosmo queue ls
cosmo events tail
```

## Conventions this codebase follows

- **Python 3.12+, `uv`-managed.** Add dependencies with `uv add`, not by hand.
- **`mypy --strict` passes.** Annotate everything, including test helpers.
- **Comments explain *why*, never *what*.** Existing comments cite the spec
  section that forced the decision. Match that.
- **Config over constants.** Every tunable goes in `config/model.py` and
  `config/defaults.toml`, annotated with its spec section. No magic numbers.
- **Validators catch what would fail silently.** See the existing timeout and
  playwright-tag validators for the pattern: reject at startup what would
  otherwise misbehave at 3am.
- **Tests isolate from the developer's environment.** Anything touching config
  must set `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths — see the autouse
  fixture in `tests/test_cli.py`.
- **Boundary tests are load-bearing, not optional.** `test_harness_boundary.py`
  keeps harness-specific tokens out of core; `test_store_boundary.py` keeps
  `connect_writer` from leaking outside `store/writer.py` and
  `store/migrations.py`. If Phase 2 introduces a genuinely new writer or a
  genuinely harness-aware module, add it to the relevant allowlist — don't
  weaken the assertion.
- **Run `./check.sh` before committing.** All four must pass.

## Phase 2 scope

Spec §2.4 (all six contract points) and §3.3 (timers). Summary from the plan:

1. **`proc.ManagedProcess`**: `Popen(..., start_new_session=True)`, non-blocking
   stdout/stderr drain to a per-task rotating log file (`raw_log_path`).
2. **`cancel()`**: `os.killpg(os.getpgid(pid), SIGTERM)` → **20 s** grace
   (already in config as `timeouts.kill_grace`) → `killpg(SIGKILL)`.
3. **Orphan sweep**: `docker ps -q --filter label=orchestrator.run_id=<id>` →
   `docker rm -f`; scan for processes holding the worktree path and log
   `critical`.
4. **Two independent timers per managed run**: wall-clock and stall. The
   stall timer accepts heartbeat pokes from *either* source (§4) so a long
   legitimate subtask does not trip it.
5. **Reap failure** emits `task.failed` with `failure_type=environment_error`
   and **double-weights** the circuit breaker (§6.5) — the weight is already
   in config as `circuit_breaker.reap_failure_weight`.

### Exit criteria

- Test: a shell script spawning a grandchild that ignores `SIGTERM` is fully
  reaped, verified by PID absence — the child does not survive re-parented to
  init.
- Test: a labeled container left running by a killed process is removed by
  the sweep.
- Test: stall timer fires at the configured interval and is correctly reset
  by a poke.

## Things to know before you start

**Emit through `EventEmitter`, write through `StoreWriter`, and route
background threads through `submit()`/`drain()` — this is Phase 1's whole
point.** A reap failure's `task.failed` event and the circuit-breaker weight
should go through the same `store`/`events` machinery Phase 1 built, not a
parallel path. If `proc` needs to write from a thread that is not the main
loop's thread, that write goes through `StoreWriter.submit()` — do not open a
second `connect_writer`. `tests/test_store_boundary.py` will fail loudly if
you do; if `proc` turns out to be a legitimate new writer owner, that test's
allowlist is the place to say so deliberately, not to route around.

**Timeout and kill-grace values already exist in config.** `kill_grace` (20s),
`timeouts.implementing_stall` / `.implementing_wall` /
`.validating_stall` / `.validating_wall` are all in `config/defaults.toml`
already, annotated with their spec section. Don't reintroduce them as
constants in `proc/`.

**The circuit breaker itself is Phase 8.** Phase 2 only needs to emit the
right event with the right `failure_type` and know its own reap-failure
weight; it does not implement breaker trip logic. Don't reach ahead.

**Nothing in Phase 2 should invoke the real Claude Code CLI or a real gate
container beyond what's needed to prove the kill/reap/timer mechanics.** Use
a test fixture script (e.g. a small Python or shell script that spawns an
ignoring-SIGTERM grandchild) rather than `claude -p`. The `FakeHarnessAdapter`
proper is Phase 3's job; Phase 2's tests exercise `proc` directly.

**Docker labels are a hard requirement, not a nicety.** `--label
orchestrator.run_id=... --label orchestrator.task_id=...` is what makes the
orphan sweep possible at all (spec 2.4 step 5). Any code that launches a gate
container — even a Phase 2 test fixture standing in for one — must set both
labels from day one, or the sweep test has nothing real to find.

**`doctor` may need a new check.** Consider whether an orphaned-container or
leaked-process check belongs in `core_checks()` (harness-agnostic, so yes if
it's added) — not required by the exit criteria, but worth a look since §9.5
disk-floor and §8 database checks already set the precedent of `doctor`
catching problems before a run starts.

## When you finish

1. `./check.sh` green.
2. Update `v3-implementation-state.md`: mark Phase 2 complete, list what
   exists, record every decision made and anything a future session would
   otherwise rediscover. Append any new spec deviation to the cumulative
   table at the bottom.
3. Commit to `develop` with a message explaining *why*, in the style of the
   Phase 0 and Phase 1 commits.
4. Rewrite this handoff for Phase 3 (harness abstraction and the Claude Code
   CLI adapter, §2.1-2.3, §4, §7.2) — or delete it if the next session
   continues immediately.

Phase 3 is next: the real `ClaudeCodeAdapter` and its `stream-json` reader,
plus `FakeHarnessAdapter` for every later phase's tests. It depends on Phase
2's process-group kill semantics being correct — an adapter that can't
reliably cancel a hung `claude -p` process poisons every phase after it.
