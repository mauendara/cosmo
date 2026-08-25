# Cosmo — Implementation State

Running record of what actually exists in the codebase, phase by phase. Updated at
the end of each working session.

The plan ([v3-implementation-plan.md](v3-implementation-plan.md)) says what *will*
be built. This document says what *is* built, and records decisions and gotchas
made during implementation that a future session would otherwise have to
rediscover.

| | |
|---|---|
| Last updated | 2026-08-24 |
| Working branch | `develop` |
| Head commit | `bc62bfc` — Phase 1 (Phase 2 not yet committed) |
| Spec | [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) |

## Phase status

| Phase | Status |
|---|---|
| 0 — Repository skeleton and configuration | **Complete** |
| 1 — Persistent state and the event log | **Complete** |
| 2 — Process supervision | **Complete** |
| 3 — Harness abstraction and Claude Code adapter | Stub only (see below) |
| 4 — Template system and `cosmo init` | Not started |
| 5 — Worktree lifecycle and git operations | Not started |
| 6 — Validation gate | Not started |
| 7 — Task state machine | Not started |
| 8 — Run loop, DAG, circuit breaker, quota | Not started |
| 9 — Observability, logs, deployment | Not started |
| 10 — Acceptance run | Not started |

---

## Phase 0 — Complete

All exit criteria met. 38 tests passing; `ruff`, `ruff format`, and `mypy --strict`
clean. `./check.sh` runs all four in one command.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/checks.py` | `CheckResult` / `CheckStatus` — the neutral result type both core and adapter preflight produce |
| `src/cosmo/config/model.py` | The full typed config model, every spec tunable, with cross-field validators |
| `src/cosmo/config/defaults.toml` | Shipped spec defaults; each value annotated with its spec section |
| `src/cosmo/config/loader.py` | Three-layer loading: shipped defaults → user config → CLI overrides |
| `src/cosmo/doctor.py` | Core (harness-agnostic) preflight checks |
| `src/cosmo/harness/base.py` | `HarnessAdapter` ABC, `HarnessCapabilities`, `HarnessResult` |
| `src/cosmo/harness/registry.py` | Name → adapter mapping and resolution-with-provenance |
| `src/cosmo/harness/claude.py` | Claude adapter: capabilities + `preflight()` implemented; execution methods raise `NotImplementedError` |
| `src/cosmo/cli/main.py` | `cosmo` command: `--version`, `config show`, `harness list`, `doctor` |
| `src/cosmo/{store,events,proc,git,gate,task,run,knowledge}/` | Empty packages, each with a one-line comment naming its phase and spec section |
| `check.sh` | Lint + format + types + tests |

Working commands:

```
cosmo --version
cosmo config show [--paths]
cosmo harness list
cosmo doctor [--harness NAME] [--config PATH]
```

### Decisions made during Phase 0

**1. `doctor` is split along the harness boundary.**
The original plan put an `ANTHROPIC_API_KEY` check in a generic `cosmo doctor`.
That check is meaningless to a Cursor or Codex adapter and hardcodes one harness
into the harness-agnostic layer, which §2 forbids. Now:
- `cosmo/doctor.py` holds core checks only and **does not import `cosmo.harness`
  at all**.
- Harness-specific preconditions come from `preflight()` on the resolved adapter.
- `cli/main.py` composes the two and renders them as separate tables.

**2. `HarnessAdapter.preflight()` — extension to spec §2.2.**
The spec's interface lists `propose`/`implement`/`validate`/`get_progress`/`cancel`.
A sixth method was needed so each adapter declares its own environmental
preconditions rather than core hardcoding them. Fold into a future spec revision.

**3. `validate()` deliberately omitted from the adapter interface.**
Spec §2.2 lists `validate(task_id)` as an adapter method while also stating that
validation "bypasses the LLM harness entirely (direct Docker invocation)." Those
conflict. Validation is owned by `cosmo.gate` (Phase 6). Recorded in a comment at
the bottom of `harness/base.py`.

**4. Top-level `stream/` package removed from the plan's layout.**
`stream-json` is Claude Code's wire format, not a universal one. A core-level
reader would leak this harness's wire protocol across the §2 boundary. The reader
belongs in `harness/claude/` in Phase 3. Found by the boundary test on its first
run, not by inspection.

**5. Paths default to XDG, not the spec's `/var/cosmo`.**
`/var` requires root on a WSL2 development box. `paths.data_dir` /
`paths.work_dir` / `paths.log_dir` default to `~/.local/share/cosmo/*`; the
droplet overrides them to `/var/cosmo` via its own config file. Same code,
different config per host.

**6. Harness resolution returns provenance.**
`resolve_harness_name(flag, project, configured) -> (name, source)`. Every
command prints which adapter it chose and why; an audit log should never have to
guess. Order: `--harness` flag > project registration (Phase 1) > config default.

**7. Config rejects settings that would fail silently at runtime.**
Beyond type checking, the model refuses:
- a stall timeout at or above its wall clock — it could never fire, silently
  disabling the only guard against a hung harness (§3.3)
- `retries.delay_min > delay_max`
- a `playwright_image` pinned to `:latest` or with no tag — §1.1 requires atomic
  version pinning
- unknown keys (`extra="forbid"`), so a config typo fails loudly rather than
  being ignored

**8. A cost ceiling of `0.0` means "disabled."**
The posture for a subscription-billed harness, where §7.1 usage windows govern
instead. `CostConfig.run_limit_enabled` / `.task_limit_enabled` express this so
callers never compare against zero themselves.

### Things that will matter later

**The boundary test is load-bearing.**
`tests/test_harness_boundary.py` fails if any harness-specific token
(`ANTHROPIC_API_KEY`, `stream-json`, `--permission-mode`, `max-turns`,
`dangerously-skip-permissions`) or the bare literal `"claude"` appears in a core
module, or if `doctor.py` imports `cosmo.harness`. It already caught one real
violation. **When adding a genuinely harness-aware module, add it to
`ALLOWED_HARNESS_AWARE` rather than weakening the test.**

**`defaults.toml` is the only place in core that names a harness.**
It is configuration data, not logic, and is on the allow-list for that reason.

**`harness/claude.py` becomes a package in Phase 3.**
`harness/claude/` with `adapter.py` and `stream.py`, so the stream reader sits
beside the adapter rather than in core.

**Project registration is the missing middle tier of harness resolution.**
`cli/main.py` currently passes `None` for the project tier, with a comment. Phase 1
adds the `projects` table (§10.4 step 6); wire it in there.

**Tests must never read the developer's real user config.**
`tests/test_cli.py` sets `COSMO_CONFIG` and `XDG_DATA_HOME` to temp paths in an
autouse fixture. Any new test touching config needs the same isolation, or it will
pass or fail depending on whose machine it runs on.

**`cosmo doctor` warns rather than fails on a `/mnt` work dir.**
Slow WSL2 filesystem I/O distorts every §3.3 timeout, but it is not a hard block.

**Repo branch model mirrors the spec's target-repo model.**
`develop` is the working branch, `master` is promoted manually — the same shape
§3.2 describes for managed projects. Cosmo's own branches are unrelated to
`git.base_branch` in config, which refers to the *target* repo.

### Environment as verified on 2026-08-24

| | |
|---|---|
| Python | 3.14.4 |
| `uv` | 0.11.28 |
| `git`, `docker`, `claude`, `openspec` | all on PATH; `docker` resolves to the Docker Desktop shim under `/mnt/c` |
| `ANTHROPIC_API_KEY` | unset (correct) |
| Free disk | ~940 GB on the WSL2 ext4 filesystem |
| Install | `uv tool install --editable .` → `cosmo` on PATH at `~/.local/bin/cosmo`, pointing back at the source tree |

---

## Phase 1 — Complete

All exit criteria met. 71 tests passing; `ruff`, `ruff format`, and `mypy --strict`
clean.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/store/clock.py` | One `utcnow_iso()` used by every timestamp column |
| `src/cosmo/store/enums.py` | `TaskStatus`, `BlockedReason`, `FailureType`, `FailureStage`, `NextAction`, `RunStatus`, `PauseReason`, `StopReason`, `HeartbeatSource`, `Severity` — each mirrors a CHECK constraint in the schema |
| `src/cosmo/store/connection.py` | Pragmas (§8.1) applied on every connection; `connect_writer` (rw) and `connect_reader` (genuine SQLite `mode=ro`) |
| `src/cosmo/store/migrations.py` | Forward-only migration runner, `schema_migrations` table, migration 1 = the full schema DDL |
| `src/cosmo/store/writer.py` | `StoreWriter` — the single write connection; `task_queue` add/retry/block, `task_transitions` append, `projects` registration, `submit()`/`drain()` for cross-thread handoff |
| `src/cosmo/store/reader.py` | Read-only queries, each opening and closing its own `connect_reader` connection |
| `src/cosmo/events/envelope.py` | `Event` dataclass (§9.1 envelope), `EventType` (§9.2), `EVENT_SCHEMA_VERSION = 1` |
| `src/cosmo/events/emitter.py` | `EventEmitter` — transactional `sequence` allocation via one `INSERT ... ON CONFLICT ... RETURNING` plus one `INSERT` per emit, both in the same `sqlite3` transaction |
| `src/cosmo/cli/main.py` | Adds `cosmo queue add\|ls\|show\|retry\|block`, `cosmo events tail`, `cosmo project register\|list`; wires the project tier into `doctor --project-path` |
| `src/cosmo/doctor.py` | Adds `check_database` — readable + at the expected schema version, `ok` (not `fail`) when the DB simply doesn't exist yet |

Working commands (all new):

```
cosmo queue add <spec_path> [--task-id ID] [--depends-on ID ...] [--priority N]
                [--max-attempts N] [--allow-test-edits]
cosmo queue ls [--status STATUS]
cosmo queue show <task_id>
cosmo queue retry <task_id>
cosmo queue block <task_id> --reason REASON
cosmo events tail [--run ID] [--task ID] [--severity SEV] [--limit N]
cosmo project register <path> [--harness NAME] [--project-template NAME]
cosmo project list
cosmo doctor --project-path <path>   # now resolves the project tier for real
```

### Decisions made during Phase 1

**1. `task_queue` does not carry a `project_id`, deliberately.**
§5 lists an exact column set and it does not include one. A queue-to-project
association is real and will be needed, but nothing in Phases 1-3 requires it
yet (`cosmo run`, which would need it, is Phase 8) — adding it now would be
speculating ahead of the spec. Revisit when Phase 4's `cosmo init` or Phase 8's
run loop actually needs to resolve a task to a target repo.

**2. `cosmo project register` is a deliberate, minimal addition — not part of
`cosmo init`.**
The handoff's instruction to "wire up the missing harness-resolution tier"
needs *some* way to populate `projects` ahead of Phase 4's full bootstrap flow
(templates, symlinks, `openspec/` seeding). This command does none of that —
it only inserts a row so `doctor --project-path` has something to resolve
against. `cosmo init` in Phase 4 should treat this as the persistence
primitive it already has, not reimplement it.

**3. Single-writer discipline is enforced two ways, not one.**
`connect_writer` (the only function that opens a writable connection) is
import-restricted to `store/writer.py` and `store/migrations.py` by
`tests/test_store_boundary.py`, mirroring the Phase 0 harness-boundary test.
Separately, `connect_reader` opens a genuine SQLite `mode=ro` connection —
not just a convention — so a read path cannot become a second writer even by
accident. `StoreWriter.submit()`/`.drain()` exist now for the cross-thread
handoff the plan describes, ahead of Phase 2/3 having any threads that
actually need it.

**4. `sequence` is scoped per `run_id`, with `''` as the scope for run-less
events.**
§9.1 says "monotonic within the run," but §9.2 also names project-level
events (`agent_assets.synced`) that carry no `run_id`. A single
`event_sequence` table keyed by scope (empty string for the run-less case)
covers both without a nullable-column special case in the counter logic.

**5. `EventType`/`Severity`/`blocked_reason`/`failure_type`/`failure_stage`
are real Python enums whose `.value` sets are hand-kept in sync with the
schema's CHECK constraints.**
No codegen from one to the other — the plan's "enums in the schema, not free
text" is about the schema; mirroring it in Python is what makes a typo a
mypy error instead of a runtime CHECK failure. `event_type` itself is *not*
CHECK-constrained in the schema (only the three the plan names are) — new
event types are expected to be added over the project's life without a
migration.

**6. Migration transactionality is achieved with a literal `BEGIN`/`COMMIT`
inside each migration's own SQL text, not with Python-level transaction
control.**
`sqlite3.Connection.executescript()` implicitly commits any pending
transaction before running and gives no transaction guarantee across the
statements it runs — the transaction control has to live in the script
itself. `migrate()` appends a generic `schema_migrations` stamp (the table
creation plus the version-stamping `INSERT`) to every migration's script
before wrapping the whole thing in `BEGIN; ...; COMMIT;`, so the schema
change and its version stamp can never land separately.

**7. Timestamps are UTC ISO 8601 with millisecond precision everywhere**,
via one `store.clock.utcnow_iso()`, so every timestamp column sorts and
compares as a plain string with no parse step.

### Things that will matter later

**The concurrency exit criterion is tested against the pragmas, not just the
discipline.** `tests/test_store_concurrency.py` deliberately opens *two*
independent write connections (the shape single-writer discipline exists to
avoid) and hammers both for a second, asserting zero `SQLITE_BUSY`. This
proves the §8.1 pragma set actually holds under real contention, which is
what the plan's exit criterion literally asks for — the boundary test
(`test_store_boundary.py`) separately proves Cosmo's own code never creates
that situation.

**The "no gaps or duplicates" exit criterion is tested as a transaction
atomicity property, not by an actual `kill -9`.** `tests/test_events.py`
wraps the real connection in a proxy that fails exactly the `INSERT INTO
events` call on the second `emit()`, then asserts the next successful emit
gets sequence 2, not 3. `sqlite3.Connection` is a C type with no per-instance
`__dict__`, so a `monkeypatch.setattr` on the connection *object* doesn't
work — patch `StoreWriter._conn` instead (a plain Python attribute) and route
through a proxy that delegates everything except the one call under test.

**`StoreWriter.submit()`/`.drain()` has no real caller yet.** Phase 2's
process supervision and Phase 3's stream reader are the first consumers.
`tests/test_store_writer.py` exercises the mechanism directly (a background
thread submits a job, the main thread drains it) since there is no watcher to
exercise it end-to-end yet.

**`doctor --project-path` only resolves what has been registered.** Until
Phase 4 ships `cosmo init`, the only way to populate `projects` is `cosmo
project register`. This is expected, not a gap — see decision 2 above.

**Any new migration must go through `MIGRATIONS`, never edit `_SCHEMA_V1`.**
Once shipped, a migration's SQL is frozen (forward-only, Open Item 5). A
schema change is always `Migration(2, ...)` appended to the list.

---

## Phase 2 — Complete

All exit criteria met. 91 tests passing; `ruff`, `ruff format`, and `mypy --strict`
clean.

### What exists

| Path | Contents |
|---|---|
| `src/cosmo/proc/managed.py` | `ManagedProcess` — `Popen(..., start_new_session=True)`, non-blocking stdout/stderr drain to a raw log file, `cancel()` |
| `src/cosmo/proc/timers.py` | `WallClockTimer`, `StallTimer`, `LivenessTimers` — the two independent timers per managed run (spec 3.3) |
| `src/cosmo/proc/orphans.py` | `sweep_containers` (docker label filter + `rm -f`), `find_worktree_holders` (`/proc` scan), `sweep` (both, spec 2.4 steps 4-5) |
| `src/cosmo/proc/reap.py` | `cancel_and_reap` — ties `cancel()` + `sweep()` together and emits `task.failed`/`environment_error` with the breaker weight on a failed reap (spec 2.4 step 6) |
| `src/cosmo/doctor.py` | Adds `check_no_leaked_gate_containers` to `core_checks()` |
| `tests/fixtures/spawn_ignoring_grandchild.sh` | Shell fixture: a process with no SIGTERM trap spawns a grandchild that ignores SIGTERM and loops forever |
| `tests/fixtures/fake_docker.sh` | Recording stand-in for the `docker` CLI, including a `FAKE_DOCKER_FAIL` mode (see decision 3) |

### Decisions made during Phase 2

**1. `cancel()`'s liveness check is `killpg(pgid, 0)`, not "has our direct
child's `wait()` returned."**
The obvious-looking implementation -- `Popen.wait(timeout=grace_s)` then
escalate -- only proves the *direct* child exited. A grandchild that traps
and ignores `SIGTERM` survives its parent's death, is still in the same
process group, and is exactly the leak spec 2.4's opening paragraph
describes. `_wait_for_group_empty` polls `os.killpg(pgid, 0)` (raises
`ProcessLookupError` once no process anywhere carries that pgid) instead,
which is the only check that actually proves the whole tree is gone. This
also drives the escalation to `SIGKILL` correctly: a `SIGTERM`-only reap that
looked "done" because the direct child died would never send the `SIGKILL`
a stubborn grandchild needs.

**2. Reaping the direct child is interleaved with the liveness poll, not
deferred until the group is confirmed empty.**
The first version of `_wait_for_group_empty` deferred `Popen.wait()` to
avoid a pid-reuse race (reaping early frees the pid, which is also the pgid,
for the kernel to hand to an unrelated process while still polling). That
created a real deadlock instead: a child with *no* surviving descendants of
its own becomes a zombie the moment it exits, and only its actual parent
(us) can reap it -- nothing else ever will. `killpg(pgid, 0)` sees the
zombie as "still there" forever, so `cancel()` timed out even against a
plain `sleep` that dies cleanly on `SIGTERM` (`tests/test_proc_managed.py`
caught this immediately). Fixed by calling `self._proc.poll()` on every
polling iteration -- `Popen.poll()` reaps via `waitpid(WNOHANG)` the instant
the child exits. The pid-reuse race this reopens is real but is the same
order of risk every process supervisor using pgids accepts; not worth
architecting around for a serial, single-host v1.

**3. `sweep_containers` / `check_no_leaked_gate_containers` check
`returncode`, not just whether stdout parsed into plausible-looking lines.**
Found by manually running `cosmo doctor` on this WSL2 box, not by a unit
test: the local `docker` resolves to the Docker Desktop shim, which is
non-functional here and, on failure, prints its "command not found" banner
to **stdout** (not stderr) and exits 1. The original code treated every line
of that banner as a container id and would have called `docker rm -f` on
them. Both call sites now short-circuit to "found nothing" / "warn, docker
ps failed" on a non-zero exit rather than parsing stdout at all.
`fake_docker.sh` gained a `FAKE_DOCKER_FAIL` mode specifically to pin this
regression in tests, since the real shim's behavior can't be exercised in
CI.

**4. `ManagedProcess._drain` reads via `os.read(fd, 4096)`, not
`pipe.read(4096)`.**
A `BufferedReader.read(size)` blocks until it fills the requested size *or*
hits EOF -- it does not return early just because some bytes are available.
A short heartbeat line followed by silence would sit unflushed in the pipe
buffer for however long the harness stayed quiet, which defeats the entire
point of "non-blocking drain" the plan asks for. `os.read` mirrors the raw
POSIX `read(2)`: it returns as soon as *any* data is available, up to the
requested size. Also caught by a test hang, not by inspection.

**5. `docker`/container tests use a recording fake script, not a live
daemon.**
This sandbox's `docker` does not actually work (see decision 3) -- the
handoff's own instruction to fake `claude -p` rather than invoke it for real
extends naturally to a fake `docker`. `tests/fixtures/fake_docker.sh` logs
every invocation and returns canned `ps -q` output (or, in `FAKE_DOCKER_FAIL`
mode, a non-zero exit with stdout-only error text). A future integration
test against real Docker belongs with Phase 6's gate work, where a real
container is actually launched.

**6. `check_no_leaked_gate_containers` added to `core_checks()`, per the
handoff's "worth a look."**
Scoped narrowly: it only checks for containers still carrying
`orchestrator.run_id`, which only step 5's convention makes possible. It
does not attempt the worktree-holder half of the sweep across a process
restart (that needs the task queue's `worktree_path` column cross-referenced
against a "run in progress" concept that doesn't fully exist before Phase
8) -- left for whichever later phase actually needs it.

### Things that will matter later

**`ManagedProcess.cancel(grace_s=...)` takes the grace period as an
argument, not read from config internally.** `cancel_and_reap` is what wires
`config.timeouts.kill_grace` in. Keeping `ManagedProcess` itself
config-ignorant kept the fast unit tests fast (grace periods of 0.3s instead
of the real 20s) without needing a config fixture in every test.

**The circuit breaker is still not implemented (Phase 8, as planned).**
`cancel_and_reap` emits `task.failed` with `failure_type=environment_error`
and `circuit_breaker_weight` in the payload on a failed reap, but nothing
consumes that weight yet -- there is no breaker to trip. This matches the
handoff's explicit scope boundary; don't reach ahead of it in Phase 3 either.

**`find_worktree_holders` is a `/proc` scan, not `lsof` or `psutil`.**
No new dependency needed for a Linux-only, best-effort scan; skips any pid
it can't introspect (permission denied, exited mid-scan) rather than
raising. Untested on anything but Linux/WSL2 -- consistent with spec 2.4's
own "on POSIX" framing, so this is not considered a gap.

**Docker now works on this box (fixed 2026-08-24, after Phase 2 was written).**
Earlier in the session `docker` was on `PATH` but non-functional -- Docker
Desktop's WSL2 integration for this "Ubuntu" distro was stuck (its
per-distro setup step, which `wslexec`s into the distro to write
`~/.docker/config.json`, was timing out; Docker Desktop's own "WSL
integration ... unexpectedly stopped" dialog surfaced this and a "Restart
the WSL integration" click fixed it). `docker` now resolves to the real
integrated binary at `/usr/bin/docker`, and `sweep_containers`/`sweep()` was
verified end-to-end against a real labeled container (created, found by its
labels, force-removed) -- not just the `fake_docker.sh` unit tests. The
returncode-guard fix from decision 3 was written defensively before this was
fixed and remains correct/necessary regardless (a real `docker` can still
exit non-zero for other reasons, e.g. daemon down). No code changes were
needed once the environment was fixed. Also observed independently: this
box is usually near the 10 GB disk floor, so `cosmo doctor`'s disk check may
still show `FAIL` for reasons unrelated to whatever change is under test.

## Deviations from the spec, cumulative

Kept here so a future spec revision can absorb them in one pass.

| # | Deviation | Spec ref | Phase | Rationale |
|---|---|---|---|---|
| 1 | `preflight()` added to the adapter interface | §2.2 | 0 | Adapters must declare their own preconditions; core cannot know them |
| 2 | `validate()` not on the adapter interface | §2.2 | 0 | Contradicts §2.2's own statement that validation bypasses the harness |
| 3 | State paths default to XDG, not `/var/cosmo` | §3.2 | 0 | `/var` needs root on WSL2; droplet overrides via config |
| 4 | `cosmo project register` CLI added, ahead of `cosmo init` | §10.4 | 1 | The `projects` table (step 6) needs a populator before Phase 4's full bootstrap exists; this is the persistence primitive only, no templates/symlinks |
