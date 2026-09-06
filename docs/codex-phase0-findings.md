# Codex adapter Phase 0 findings

Status: complete; clean-host Phase 5 closed the gating questions
Observed CLI: `codex-cli 0.153.0`
Observed: 2026-09-05 in a disposable Git repository under `/tmp`

This spike tested the contract in `c1-codex-adapter-plan.md` before production
adapter code was started. No Cosmo command, queue, database, target repository,
or credential file was modified. Authentication was exposed to the disposable
`CODEX_HOME` through a symlink to the existing `auth.json`; the credential
contents were never copied into a fixture or printed.

## Spend boundary

The spike was capped at three authenticated turns, all with low reasoning and
minimal prompts. The CLI reported 85,694 input tokens (48,000 cached), 265
output tokens, and 56 reasoning-output tokens in total. It emitted no
authoritative USD cost. The unexpectedly large fixed input overhead means a
call-count ceiling is more honest than pretending this subscription-backed
route has a precise dollar ceiling.

## Results

| Question | Result | Evidence |
| --- | --- | --- |
| Successful JSONL | Confirmed | Exit 0 emitted `thread.started`, `turn.started`, item events, and `turn.completed.usage`. |
| Non-zero/API failure JSONL | Confirmed | An invalid model exited 1 and emitted `error` followed by `turn.failed`. |
| `apply_patch` event shape | Partially confirmed | A real attempt emitted `item.started` and `item.completed` with `type=file_change`, absolute paths, `kind=add`, and `status=in_progress/failed`. This host prevented the actual write. |
| Command event shape | Not confirmed locally | The nested Codex sandbox could not initialize because its bubblewrap registry is mounted read-only by the parent Codex sandbox. No successful command event was emitted. |
| Symlinked project hooks | Confirmed with trust bypass, but incompatible with the planned flags | With `.codex -> .agent/codex`, a `SessionStart` hook ran and a `PreToolUse` hook denied a Bash write before execution. Without the trust bypass the fresh project hook was skipped. |
| Hook denial output | Confirmed | The denied operation produced no command item in JSONL; the exact denial appeared on stderr and the target file stayed absent. Raw stderr must therefore be retained alongside JSONL. |
| `--ignore-user-config` isolation | Confirmed, with a blocking discovery | A deliberately unknown key in `$CODEX_HOME/config.toml` failed under `--strict-config` without the flag. The same authenticated invocation succeeded with `--ignore-user-config`, proving saved auth was retained while that file was ignored. |
| Project hooks with `--ignore-user-config` | **Do not load** on 0.153.0 | Neither a real `.codex` directory nor the proposed symlink loaded its `SessionStart` hook when this flag was present. Removing only the flag made both load. This contradicts the narrower wording in the current official documentation and invalidates the plan's original argv if hooks are expected. |
| Explicit hook injection | Confirmed | `-c 'hooks.SessionStart=[...]'` loaded an audited script under `.agent/codex/hooks/` while `--ignore-user-config` remained active. This is the viable direction for Phase 1 argv construction. |
| Hook-trust bypass scope | Needs more validation | The workspace-write sandbox flag remained present and a real PreToolUse denial worked, but hook input reported `permission_mode="bypassPermissions"`. That value is surprising for the hook-trust-only flag and must not be treated as proof that the sandbox is unchanged. |
| Cancellation with descendant | Confirmed within the available process boundary | Interrupting Codex while an injected SessionStart hook waited on a SIGTERM-ignoring grandchild terminated the invocation. The grandchild's delayed survival marker was still absent after 31 seconds. Phase 1 must repeat this through `ManagedProcess`. |

## Host constraint

The current development session already runs inside a Codex filesystem
sandbox. A nested 0.153.0 workspace-write sandbox tries to lock
`/tmp/codex-bwrap-synthetic-mount-targets-1000/lock`, but that registry is
read-only in the parent mount namespace. The deprecated Landlock fallback also
failed real writes. This prevented successful command and patch execution; it
does not justify using the forbidden approval-and-sandbox bypass.

## Sanitized fixtures

Recorded and derived fixtures live in `tests/fixtures/codex_jsonl/`. Thread
IDs, workspace paths, and account/model error text are sanitized. The malformed
and truncated files are deliberately derived from observed event shapes; they
were not emitted verbatim by the CLI.

## Consequences for Phase 1

- Keep `supports_gating = False`.
- Keep `--ignore-user-config`, but inject each audited hook matcher explicitly
  with `-c`; do not rely on project hook discovery.
- Preserve stdout JSONL and stderr as separate raw evidence streams.
- Treat exit code as process success only. One real exit-0 turn ended after a
  failed file-change attempt and model-authored `done`, which confirms that Git
  and the validation gate must remain authoritative.
- Add a clean-host validation for successful command and patch events and for
  the effective sandbox under the hook-trust flag before revisiting gating.

These were the intentionally conservative Phase 1 decisions. The later
clean-host results below supersede the temporary `supports_gating = False`
posture.

## Clean-host closure (2026-09-06)

Phase 5 repeated the contract through the production adapter on Codex 0.153.0:

- A normal call emitted real `command_execution` and successful `file_change`
  lifecycles, a thread id, terminal usage, and exit 0.
- The proposed root `.codex -> .agent/codex` link itself caused a sandbox mount
  rejection. Bootstrap now installs only `.agents/skills` and removes only the
  exact legacy Cosmo-owned `.codex` link. Explicit hook injection remains the
  source of policy enforcement.
- A hostile call produced real hook denials for protected patch and shell
  edits, forbidden annotations, Git push/reset, detached background work, and
  a known secret-file read. Codex's workspace sandbox separately rejected a
  write outside the worktree; the target sentinel never existed.
- An isolated `CODEX_HOME` containing a deliberately invalid model and unknown
  strict key still succeeded under `--ignore-user-config` while using the
  saved-login authentication link. No credential content was read or copied.
- Cancellation during a command with a SIGTERM-resistant child returned after
  the configured grace period; a delayed survival marker remained absent.
- A fresh review left tracked source unchanged and wrote only
  `.cosmo/review-result.json` with `{"verdict":"approved"}`.
- A disposable single-task Cosmo run completed proposing, implementing,
  validating, reviewing, committing, merging, and finishing. `HELLO.md` was
  merged onto `develop`, the target was clean, and the OpenSpec change was
  archived and promoted.

The lifecycle found two integration constraints beyond the initial spike.
Task orchestration changes `adapter.cwd`, so a composed invoker must synchronize
its own `cwd` at call time. Also, the workspace-write sandbox protects a linked
worktree's Git metadata even while source files remain writable. Codex is now
instructed to leave changes unstaged, and harness-agnostic orchestration creates
a bounded implementation commit after any successful harness call with pending
output; existing self-committing harnesses take the clean no-op path.

The adversarial gate is therefore closed and the production capability is
`supports_gating = True`. Hooks remain defense in depth and the validated
hosted/specialized tool surface stays disabled.

Across 24 completed diagnostic/decisive calls (26 sessions including killed
calls), the CLI reported 2,006,282 input tokens, 1,572,224 cached input tokens,
38,004 output tokens, and 9,985 reasoning tokens. It still emitted no
authoritative USD cost, quota window, or reset event, so native cost reporting
and primary quota detection remain unsupported rather than estimated.

Official references used for the spike: [Hooks](https://learn.chatgpt.com/docs/hooks),
[Non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode), and
[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

## Repository verification

`./check.sh` exited 0 after the fixture and documentation changes: Ruff check
and format check passed, mypy found no issues in 170 source files, and pytest
reported 671 passed and 9 skipped in 76.09 seconds.
