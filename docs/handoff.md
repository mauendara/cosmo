# Handoff — continue at Phase 1

You are picking up Cosmo mid-build. Phase 0 is complete and committed. Your job is
Phase 1: persistent state and the event log.

## Read these first, in this order

| Document | What it is | How to treat it |
|---|---|---|
| [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md) | The authoritative specification | **Source of truth.** v1 and v2 are superseded — read them only for history |
| [v3-implementation-plan.md](v3-implementation-plan.md) | 11-phase build plan | The map. Phase 1 is your scope |
| [v3-implementation-state.md](v3-implementation-state.md) | What actually exists, plus decisions and gotchas | Read the "Things that will matter later" section before writing code |

`v1-*` and `v2-*` in this folder are earlier spec drafts. v3 is a superset of
both. Do not implement from them.

**Do not edit the plan document.** It is the agreed scope. Record what you build,
and any decision you make along the way, in `v3-implementation-state.md`.

## Where things are

```
/home/dev/delta/cosmo/          # working branch: develop
├── docs/                       # the four documents above
├── src/cosmo/
│   ├── checks.py               # CheckResult / CheckStatus
│   ├── config/                 # typed model, defaults.toml, three-layer loader
│   ├── doctor.py               # core preflight checks (harness-agnostic)
│   ├── harness/                # base ABC, registry, claude adapter (stub)
│   ├── cli/main.py             # the `cosmo` command
│   ├── store/                  # EMPTY — Phase 1 fills this
│   ├── events/                 # EMPTY — Phase 1 fills this
│   └── {proc,git,gate,task,run,knowledge}/   # EMPTY — later phases
├── tests/                      # 38 passing
└── check.sh                    # ruff + format + mypy --strict + pytest
```

## Get oriented (2 minutes)

```bash
cd /home/dev/delta/cosmo
git log --oneline           # should show 02ca48e Phase 0
git branch --show-current   # should say develop
./check.sh                  # must be green before you change anything
cosmo doctor                # should print two tables and say "ready"
```

If `cosmo` is not on PATH, run `uv tool install --editable .` from the repo root.
Editable means your source edits are live — no rebuild between changes.

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
- **Run `./check.sh` before committing.** All four must pass.

## Phase 1 scope

Spec §8, §8.1, §9.1, §9.2, §9.3, and the §5 queue columns. This closes half of
the spec's Open Item 5 (the SQLite DDL; the adapter half is Phase 3).

Full detail is in the plan under "Phase 1". Summary:

1. **Schema**, split by the §8 discipline:
   - *Append-only*: `events` (the full §9.1 envelope, including `sequence`,
     `schema_version`, `severity`), `task_transitions`, `task_failures`
   - *UPSERT / current-state*: `task_queue` (all §5 columns), `task_progress`,
     `task_heartbeat`, `run_state`, `run_cost`, `task_cost`, `projects`
2. **Enums in the schema**, not free text — `blocked_reason`, `failure_type`,
   `failure_stage`. §5 is explicit that free-text forces every consumer into regex
   parsing that will drift.
3. **Pragmas on every connection**, not once at creation: `journal_mode=WAL`,
   `busy_timeout=10000`, `synchronous=NORMAL`, `foreign_keys=ON`. Plus a
   `wal_checkpoint(TRUNCATE)` at run boundaries.
4. **Single-writer discipline (§8).** One write connection owned by the main loop.
   The file-watcher and stream reader push onto an in-process queue instead of
   opening their own write connections. Enforce this *structurally* — the writer
   connection must not be importable from those modules.
5. **Event emitter** with `sequence` allocated transactionally with the row.
6. **Forward-only migration runner** with a `schema_version` table.
7. **CLI:** `cosmo queue add|ls|show|retry|block`, `cosmo events tail`.

### Exit criteria

- `cosmo queue add` then `cosmo queue ls` round-trips a DAG with `depends_on`.
- A concurrency test writes progress events from a watcher thread while the main
  loop writes state, with zero `SQLITE_BUSY`.
- Killing the process mid-write leaves an event log whose `sequence` has no gaps
  or duplicates.

## Things to know before you start

**The database path already exists in config.** `config.paths.db_path` resolves to
`<data_dir>/cosmo.db`. Do not invent a second path constant.

**Wire up the missing harness-resolution tier.** `cli/main.py` passes `None` for
the project tier of `resolve_harness_name()`, with a comment saying Phase 1 adds
it. The `projects` table (§10.4 step 6) is that tier — connect it once the table
exists.

**Add a store/events preflight check.** `doctor.py` currently checks that state
directories are writable. Once the DB exists, consider a check that it is
readable and at the expected schema version. Keep it in core — it is not
harness-specific.

**`tests/test_harness_boundary.py` is load-bearing.** It fails if harness-specific
tokens leak into core modules. Phase 1 code is core, so it must stay clean. If you
add a genuinely harness-aware module, add it to `ALLOWED_HARNESS_AWARE` — do not
weaken the test.

**Nothing in Phase 1 should invoke Claude or Docker.** It is pure persistence. If
you find yourself needing either, you have wandered into Phase 2 or 3.

**Design the event table for the deferred OTel migration (§9.4).** The §9.1
envelope is deliberately shaped so `event_type` and payload keys map onto GenAI
span attributes later. Keep payloads as structured JSON with stable key names
rather than prose blobs.

## When you finish

1. `./check.sh` green.
2. Update `v3-implementation-state.md`: mark Phase 1 complete, list what exists,
   record every decision made and anything a future session would otherwise
   rediscover. Append any new spec deviation to the cumulative table at the bottom.
3. Commit to `develop` with a message explaining *why*, in the style of `02ca48e`.
4. Rewrite this handoff for Phase 2 (process supervision, §2.4) — or delete it if
   the next session continues immediately.

Phase 2 is next: process-group kill semantics, orphan sweep, and timers. It is
deliberately early because a leaked process pool poisons every later phase.
