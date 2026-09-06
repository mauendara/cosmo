# Cosmo — v8: real-invocation validations still owed

## Status

**Tracking document, not a plan** — nothing here is a design proposal or a
piece of unbuilt functionality. Every item below is code that already
exists and is unit-tested, but hasn't yet been proven against the one real
condition that a test double can't stand in for (a real `sudo` install, a
real multi-hour quota window, a real interactive terminal session, a real
circuit-breaker trip). Extracted from `docs/handoff.md`'s "What still needs
validating" section as of the deviation-79 handoff (2026-08-28) so it
survives independently of handoff.md's own churn — that file gets rewritten
every session; this list should only shrink, item by item, as each gets a
real run and its result recorded here.

**When an item here gets validated**: update its entry in place with what
was actually run and what happened (don't just delete it — a one-line
"confirmed, see deviation NN" is more useful than silence), and drop the
pointer from `docs/handoff.md` once nothing here is still open.

## Open items

- **A real system-wide (`sudo cp .../etc/systemd/system/`) install** of
  both `deploy/cosmo-run.service` and `deploy/cosmo-notify.service`, as
  `deploy/README.md` actually documents for production. Every session so
  far has installed as `systemctl --user` units instead, for lack of
  `sudo` access in-session. Nothing found in any session so far suggests
  the real path wouldn't work (the unit files themselves are fixed —
  `StartLimitIntervalSec`/`StartLimitBurst` moved from `[Service]` to
  `[Unit]`, deviation 69) — but it has never actually been run.

- **`REVIEWING`/`VALIDATING` timeout retuning** (spec §3.3, Open Item 2)
  has real data now but hasn't been formally decided. 8 real `REVIEWING`
  passes across the Phase 10 acceptance run: 33s-161s, comfortable under
  the 900s wall. `todo-e2e`'s two failing real `VALIDATING` attempts:
  ~24-25 real minutes each, over half the 2700s wall — the first real
  signal this value might deserve a closer look, not proof it's wrong.
  Retuning is a decision for a human, not something to change
  opportunistically. See [v9-out-of-scope-desirables.md](v9-out-of-scope-desirables.md)
  for this item's origin as one of the spec's own open follow-up items.

- **`cosmo notify config`'s own interactive flow has never been run for
  real** — only tested against a mocked `discover_chat_id`/
  `send_test_message` (`test_cli_notify.py`). The underlying Telegram API
  calls it wraps (`notify.setup`) are real and unit-tested against a faked
  `urlopen`, and end-to-end delivery is confirmed working (the real
  `cosmo-notify.service` has been sending real messages since deviation 79
  landed) — just not through the wizard's own prompts, since every
  session's real config already existed from before deviation 79 shipped.

- **A real `cosmo run resume` against a real circuit-breaker-tripped run**
  was never exercised. The *quota*-paused case is partially covered, but
  by a different mechanism than `cosmo run resume`: a real
  `quota_exhausted_5h` pause against `pomodoro-frontend-app` auto-resumed
  **in-process** (`run.loop._handle_quota_pause_or_stop` sleeps and
  resumes within the same still-running `cosmo run`, never exiting) —
  confirmed for real, including the exact resume ETA computed from the
  pause event's own `resume_delay_seconds` payload. `cosmo run resume`,
  the separate CLI command that re-attaches to an already-`PAUSED` run
  from a *fresh* process, is a distinct code path and remains unexercised
  for both trigger conditions (circuit-breaker trip and a quota pause that
  outlived the original process).

- **A real `bypass_5h_with_credits=true` run** needs a real, deliberate
  5-hour quota exhaustion window to test against — real spend, real
  waiting, not something to force casually.

- **Codex harness Phase 5 validations — RESOLVED on 2026-09-06.** The exact
  production adapter captured successful command/file-change events and a
  session id; hostile calls denied protected patch/shell edits, forbidden
  annotations, Git push/reset, background work, review source writes, and a
  known secret read. The workspace sandbox independently denied an
  out-of-worktree sentinel write. `--ignore-user-config` ignored a malicious
  personal config while preserving saved login, cancellation reaped a
  SIGTERM-resistant descendant, and fresh review wrote only an approved
  canonical verdict. A disposable single-task run completed propose through
  merge/archive and left the target clean. Corrections from the real run:
  remove the sandbox-incompatible `.codex` symlink, rebind a composed invoker's
  `cwd`, parse read-only `sed` options precisely, and let core capture pending
  implementation output because Codex cannot write linked-worktree Git
  metadata. `supports_gating=True`; native USD cost and quota signals remain
  unsupported. Full evidence: [Codex Phase 0 findings](codex-phase0-findings.md)
  and [Codex handoff](codex-handoff.md).

- **`ori-claude` harness Phase 6 validations (V1-V6) — RESOLVED, all run for
  real 2026-09-02**, from
  [v13-ori-cc-harness-template-plan.md](v13-ori-cc-harness-template-plan.md).
  User set up `ori` + `OPENROUTER_API_KEY` (via
  `/home/dev/.config/cosmo/.env`) themselves; all six items were driven
  against a scratch repo (`/home/dev/delta/cosmo-tests/_scratch-v13-phase6`,
  deleted after), never the real store or a real target repo. Total real
  OpenRouter spend across every call in this validation session: **$1.42**
  (a `[cost] max_cost_per_run_usd = 6.0` ceiling was added to the user's real
  `~/.config/cosmo/config.toml` first, via `write_user_config_table`, before
  the full-pipeline run).
  - **V1 — PASS.** `cosmo harness probe --harness ori-claude` produced a real
    `stream-json --verbose` NDJSON stream through Ori, terminal `result` with
    a populated `total_cost_usd` and `session_id`.
  - **V2 — PASS — the gate on the whole plan.** A real prompt drove the
    harness to call `Bash` with `git push origin develop` inside an
    `ori-claude`-synced scratch worktree, through the adapter's real argv
    (`--setting-sources project` included). `commit_integrity_guard.py`'s
    `PreToolUse` hook denied it for real: the terminal result's
    `permission_denials` carried the exact tool call, and the tool result
    was the guard's own deny message. Ori's inline `--settings` does **not**
    displace the project's `settings.json` hooks. `supports_gating=True` on
    `OriClaudeAdapter` is now a confirmed fact, not an assumption.
  - **V3 — PASS.** `--model` placed once, before `ori`'s own `--`;
    `ANTHROPIC_MODEL` honored end to end (confirmed both via a raw `claude`
    shim dumping the child's env, and via real successful completions at the
    requested model). One real gotcha found: the shipped default
    `harness.model = "claude-sonnet-5"` is a native Claude Code id, not an
    OpenRouter one — with no `[harness.overrides.ori-claude]` model set, Ori
    logs `"--model claude-sonnet-5 is not in the OpenRouter catalog, passing
    it through untouched"` and the call fails authentication. **A working
    `[harness.overrides.ori-claude] model = "..."` (an OpenRouter id, e.g.
    `anthropic/claude-sonnet-4.5`) is not optional for this harness** — worth
    a documentation callout, not a code fix (the shared `model` fallback
    existing at all is correct; it's just never a valid value for this
    route).
  - **V4 — PASS for the outer process** (SIGTERM reached the `ori`/`claude`
    group promptly on cancel — confirmed via `cosmo harness probe --timeout`
    triggering `adapter.cancel` mid-flight on a real `sleep 60` `Bash` call:
    exit code 143, ~10s elapsed, not the full 60s). Also reproduced, for
    real, the pre-existing and already-documented spec 2.4 step 4 limitation
    (`proc/orphans.find_worktree_holders`'s own docstring): Claude Code's own
    `Bash` tool implementation detaches each shell command into its own
    session, so a bare `killpg` (what a plain `adapter.cancel()` with no
    `run_id`/`emitter` does, e.g. `cosmo harness probe`) cannot reach it —
    the same behavior the native adapter already has, not new to Ori. The
    real `cosmo run` path always supplies `run_id`/`emitter`, so it goes
    through `cancel_and_reap`'s worktree-path-based orphan sweep instead;
    that mechanism is harness-agnostic (cwd-based, not pgid-based) and
    already covered by `test_proc_reap.py`, not re-tested here.
  - **V5 — PASS.** Full `cosmo run --repo <scratch> --task add-hello-doc
    --harness ori-claude` (a trivial one-file task, `_blank` project template
    so the gate's backend/frontend build/test stages were legitimately
    skipped rather than faked) reached `done` end to end: real
    `queued → proposing → proposed → implementing → validating → reviewing →
    committing → merging → done → finishing → done`, a real `openspec new
    change`, a real implementation commit, a real (trivially-passing, no
    stacks present) gate run, a real separate reviewing session writing
    `.cosmo/review-result.json`, a real merge to `develop`, and a real
    `openspec archive`. Incidentally re-confirmed crash recovery for this
    harness too: an accidental `SIGTERM` from a tool timeout mid-`IMPLEMENTING`
    was cleanly requeued and re-proposed on the next `cosmo run` invocation,
    same as the native adapter's already-documented behavior.
  - **V6 — informational, confirmed.** No rate-limit-shaped stream event
    (`rate_limit_event`/`system/api_retry`) appeared in any of the ~10 real
    terminal results captured across V1-V5. Matches the plan's prediction:
    quota detection degrades to spec 7.2's secondary/tertiary detectors on
    this route, exactly the documented fallback, no special-casing needed.
  - **Also found, testing-environment-only, not a code issue**: driving
    `cosmo`/`ori claude` from inside an *already-running* Claude Code session
    (as this validation session itself was) leaks that session's own
    `CLAUDECODE`/`CLAUDE_CODE_*` env vars into the child through
    `os.environ` inheritance, and the nested `claude` then reports "Not
    logged in · Please run /login" instead of authenticating via Ori's
    credential — confirmed root-caused by stripping exactly those vars
    (`env -u CLAUDECODE -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION
    -u CLAUDE_CODE_MESSAGING_SOCKET -u CLAUDE_CODE_MESSAGING_TOKEN -u
    CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_EXECPATH -u
    CLAUDE_AGENT_SDK_VERSION -u CLAUDE_PID -u CLAUDE_EFFORT -u
    CLAUDE_CODE_ENABLE_TASKS -u CLAUDE_CODE_ENABLE_SDK_FILE_CHECKPOINTING -u
    AI_AGENT`), after which every call succeeded. Won't affect a real
    deployment (systemd service, cron, a plain terminal) — only a developer
    testing `ori-claude` locally from inside their own Claude Code terminal
    hits this, and would otherwise misread it as a broken adapter.

  Now that V1-V6 have run, the plan's own Phase 6 docs commit (public
  user-docs updates) is unblocked — see `docs/v3-implementation-state.md`'s
  deviation 85 and `docs/handoff.md` for what shipped.
