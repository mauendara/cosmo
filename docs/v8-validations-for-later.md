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
