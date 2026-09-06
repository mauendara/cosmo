# C1 — Codex harness adapter and template plan

Status: Complete — Phases 0-6 validated on 2026-09-06
Target branch: `codex-adapter`
Target worktree: `/home/dev/delta/codex-adapter`

## Objective

Add Codex as a first-class Cosmo harness, with a repository-local template,
structured event handling, process lifecycle management, role-specific policy,
and guardrails consistent with Cosmo's existing harness contract.

The adapter must use the non-interactive Codex CLI. It must not infer success,
quota state, review verdicts, or retry decisions from model-authored prose.

## Non-goals

- Do not replace or rewrite a user's root `AGENTS.md`.
- Do not depend on personal Codex configuration for execution behavior.
- Do not enable unrestricted filesystem access or bypass Codex's sandbox.
- Do not claim authoritative USD cost reporting unless Codex emits one.
- Do not add Codex session resume to the first implementation. Each Cosmo stage
  should remain a fresh invocation, especially review.
- Do not generalize configuration for unrelated harnesses unless a Codex need
  cannot be met cleanly within the adapter boundary.

## Authoritative inputs

- Cosmo adapter contract: `user-docs/en/how-to/write-a-new-adapter.md`
- Existing process implementation: `src/cosmo/harness/claude/invoker.py`
- Existing stream implementation: `src/cosmo/harness/claude/stream.py`
- Harness interfaces: `src/cosmo/harness/base.py`
- Template synchronization: `src/cosmo/bootstrap/assets.py`
- Harness symlinks: `src/cosmo/bootstrap/symlinks.py`
- Official Codex non-interactive documentation:
  <https://learn.chatgpt.com/docs/non-interactive-mode>
- Official Codex hook documentation:
  <https://learn.chatgpt.com/docs/hooks>
- Official Codex configuration reference:
  <https://learn.chatgpt.com/docs/config-file/config-reference>
- Official Codex skill documentation:
  <https://learn.chatgpt.com/docs/build-skills>

The installed CLI observed while preparing this plan was `codex-cli 0.153.0`.
Its behavior must be rechecked when implementation begins because CLI schemas
and flags may change.

## Design decisions

### Adapter boundary

Create a dedicated package:

```text
src/cosmo/harness/codex/
├── __init__.py
├── adapter.py
├── invoker.py
└── stream.py
```

Keep all Codex-specific flags, environment handling, event schemas, and error
classification inside this package. Core orchestration should interact only
through the existing `HarnessAdapter` and `HarnessResult` contracts.

Register the adapter as `codex` in `src/cosmo/harness/registry.py`. Reuse
Cosmo's existing role-based model resolution for propose, implement, and
review.

### Capability declaration

The initial capability values should be:

| Capability | Initial value | Reason |
| --- | --- | --- |
| `reports_native_progress` | `False` | Cosmo continues to observe `tasks.md`. |
| `supports_retry_context` | `True` | Retry context can be appended to the next prompt. |
| `has_internal_timeout` | `False` | Cosmo owns wall and stall timeouts. |
| `reports_native_cost` | `False` | JSONL exposes usage tokens, not authoritative USD cost. |
| `supports_structured_stream` | `True` | `codex exec --json` emits JSONL events. |
| `supports_gating` | `False` initially | Set to `True` only after the adversarial real-CLI gate passes. |

Hooks should still be installed while gating is reported as unavailable. They
are defense in depth until the complete enabled mutation surface is proven.

### Invocation profile

The intended command shape is:

```text
codex exec \
  --json \
  --ephemeral \
  --ignore-user-config \
  --ignore-rules \
  --strict-config \
  --sandbox workspace-write \
  -C <task-worktree> \
  --model <resolved-model>
```

The implementation must additionally:

- Set a non-interactive approval policy explicitly.
- Disable multi-agent execution, apps, plugins, browser/computer tools, web
  search, and other unnecessary hosted tools.
- Assert in tests that the full approval-and-sandbox bypass flag is absent.
- Preserve the normal `CODEX_HOME` so existing login credentials remain usable.
- Remove `CODEX_API_KEY` from the child environment unless Cosmo later adds a
  separately named, explicit API-billed mode.
- Pass `COSMO_TASK_ID`, `COSMO_DB_PATH`, and a role indicator such as
  `COSMO_HARNESS_ROLE` where hooks need them.
- Use the hook-trust bypass only if the Phase 0 experiment proves it is needed
  to load audited Cosmo-owned hooks. It must never be confused with bypassing
  approvals or sandboxing.

The current global `permission_mode = "dontAsk"` can map to Codex's
non-interactive approval policy in the first version. Unsupported values should
fail preflight clearly rather than being silently reinterpreted.

### Policy and repository instructions

Do not create or overwrite a root `AGENTS.md`: repositories own that file.
Keep the full Cosmo policy in `.agent/codex/CODEX.md` and add a short
TOML-safe `developer_instructions` value to each invocation telling Codex to
read it. Existing repository `AGENTS.md` instructions then remain active.

Do not use `model_instructions_file`; it replaces built-in instructions and is
the wrong mechanism for composing Cosmo policy with repository policy.

### Template layout

Add:

```text
templates/harness/codex/
├── CODEX.md
├── hooks.json
├── hooks/
│   ├── _hooklib.py
│   ├── test_path_guard.py
│   ├── annotation_guard.py
│   ├── commit_integrity_guard.py
│   ├── background_task_guard.py
│   ├── review_write_guard.py
│   └── secret_read_guard.py
└── skills/
    ├── openspec-workflow/
    │   └── SKILL.md
    └── spec-enrichment/
        └── SKILL.md
```

Bootstrap should synchronize this directory to `.agent/codex`. It should then
expose Codex-discoverable skills through a safe symlink:

```text
.agents/skills -> ../.agent/codex/skills
```

The proposed `.codex -> .agent/codex` link was removed after a real 0.153.0
workspace-write invocation rejected it as an unsafe sandbox mount. Explicit
hook injection already exposes the audited hooks, so the link is unnecessary.

Nested parent creation must be handled explicitly. Existing real files,
directories, or non-Cosmo symlinks must never be overwritten.

Do not add Codex to byte-for-byte Claude template parity tests. Codex has
different tool names and payloads; enforce semantic behavior through dedicated
tests instead.

### Stream parsing and results

`stream.py` should incrementally split arbitrary byte chunks into JSONL lines,
tolerate malformed or truncated lines, and classify only structured fields.

Capture:

- `thread.started.thread_id` as the session ID.
- `turn.completed`, `turn.failed`, and `error` as terminal structured events.
- Structured item events as activity heartbeats.
- Tool-call counts from the relevant item types, deduplicated by item ID where
  necessary.
- Short activity text from structured command/path fields for display only.

Result rules:

- `success` is exactly `process exit code == 0`.
- `output_summary` describes structured terminal state or the process exit; it
  never summarizes model prose as a decision signal.
- `raw_log_path` points to the saved JSONL/stderr capture.
- `files_changed` stays empty because the downstream Git diff is authoritative.
- `total_cost_usd`, `quota_window`, and `quota_resets_at` remain `None` unless a
  documented and observed structured Codex signal supports them.

### Stage behavior

- **Propose:** pin the exact `context["spec_id"]` and forbid selecting a
  different change.
- **Implement:** direct Codex to inspect the current worktree before acting and
  append Cosmo's synthetic retry context when present.
- **Review:** always start a fresh invocation. Permit writing only the canonical
  `.cosmo/review-result.json` and deny source mutations.

Do not use Codex session resume in the first implementation. Cosmo retries are
new calls with explicit retry context, and review freshness is a correctness
requirement.

### Process lifecycle

Reuse `ManagedProcess` and the existing cancellation pattern:

- Start Codex in its own process group.
- Drain stdout and stderr without risking pipe deadlock.
- Tee the raw stream to the harness log.
- Emit activity callbacks from structured events.
- Keep a thread-safe registry of running calls.
- Cancel and reap the entire process group, including grandchildren.
- Do not add an adapter-owned timeout when the capability says none exists.

### Guardrail translation

Claude hooks cannot be copied unchanged. Codex commonly represents file edits
as `apply_patch`, with the patch text in `tool_input.command`.

Codex hooks must therefore:

- Parse patch headers to identify every affected path.
- Inspect added patch lines for forbidden test annotations.
- Guard protected paths against both patch and shell-based modifications.
- Deny destructive Git commands and pushes.
- Reject detached/background shell constructs such as `&`, `nohup`, and
  `disown` where they can outlive the one-shot harness call.
- In review mode, deny every write except the exact canonical verdict file.
- Prevent access to known secret paths within the worktree where the Codex
  sandbox alone does not provide confidentiality.

The enabled tool surface should be kept deliberately small. Codex documents
that hooks do not cover every hosted or specialized tool, so disabling those
tools is part of the security boundary.

## Implementation phases

### Phase 0 — Real CLI contract spike

Use a disposable Git repository and a strict cost ceiling. Record sanitized
fixtures for:

1. Successful no-op execution.
2. Command execution and `apply_patch` events.
3. Hook denial.
4. Non-zero/API/authentication failure.
5. Malformed or truncated output handling.
6. Cancellation of a long command with a child process.

Explicitly answer these gating questions:

- Does an untrusted fresh worktree load `.codex/hooks.json` through a symlink?
- Does the hook-trust flag allow only the audited project hook without weakening
  the execution sandbox?
- Does `--ignore-user-config` preserve authentication while excluding personal
  hooks, MCP servers, and behavioral settings?
- If project hooks are skipped, can hook configuration be injected explicitly
  with `-c` while pointing only at `.agent/codex/hooks/`?

Do not proceed with a claimed gating capability until these are resolved.

### Phase 1 — Parser and invoker

- [x] Add recorded JSONL fixtures and parser tests first.
- [x] Implement incremental classification and activity descriptions.
- [x] Build deterministic argv and environment construction.
- [x] Integrate `ManagedProcess`, separate stdout/stderr raw logs, cancellation,
  and result mapping.
- [x] Add a fake Codex executable for success, failure, malformed output, hangs,
  and grandchildren.

Completed 2026-09-05. `./check.sh` passed with 701 tests passing and 9 skipped.
No authenticated model turn was used; the installed 0.153.0 CLI accepted the
inline config/hook shape through a no-model-call config parse.

### Phase 2 — Adapter and registry

- [x] Implement propose, implement, review, preflight, progress, and cancel.
- [x] Add exact role prompt construction and retry-context tests.
- [x] Register `codex` and extend boundary allowlists only where required.
- [x] Keep preflight cheap and side-effect-free: binary presence, supported config,
  and unsafe billing-variable checks only. Authentication belongs in probe.

Completed 2026-09-05. `./check.sh` passed outside the nested sandbox with 714
tests passing and 9 skipped. The same run inside the sandbox passed lint,
format, mypy, and 708 tests but could not run six pre-existing socket tests
because socket creation is prohibited there.

### Phase 3 — Template and bootstrap

- [x] Add `CODEX.md`, hooks, and Codex-discoverable skills.
- [x] Add safe `.agents/skills` symlink support and remove an incompatible
  legacy Cosmo-owned `.codex` link on re-bootstrap.
- [x] Add bootstrap, resynchronization, collision, and idempotency tests.
- [x] Verify every hook/config reference resolves inside `.agent/codex`.

Completed 2026-09-05. The nested `.agents/skills` link uses a real `.agents`
parent and refuses to traverse a user-owned symlink; root discovery links are
relative, idempotent, and refresh only when their existing target is exactly
the one Cosmo owns.

### Phase 4 — Guardrail and integration tests

Added:

- `tests/test_harness_codex_stream.py`
- `tests/test_harness_codex_adapter.py`
- `tests/test_hooks_codex.py`
- `tests/fixtures/fake_codex.sh`

Also extend registry, CLI listing/wizard, bootstrap, symlink, boundary, and
template tests. Cover:

- Exit-code-only success.
- Prose that resembles success, failure, or quota text but changes no decision.
- Role model resolution and absence of template-pinned models.
- User-config isolation.
- The forbidden unrestricted bypass flag.
- Protected-path mutations through both `apply_patch` and shell commands.
- Forbidden annotation insertion.
- Git push/reset/destructive-command attempts.
- Review mutations outside the verdict file.
- Cancellation with no surviving descendants.
- Raw stdout/stderr preservation.

Completed 2026-09-05. Codex-specific hooks parse every `apply_patch` file
header and added line, inspect shell mutations, deny destructive Git and
background operations, protect secret paths, and enforce review-only verdict
writes. `supports_gating` remained `False` until the Phase 5 clean-host
adversarial validation passed.

`./check.sh` passed: Ruff check and formatting clean, mypy clean across 178
source files, and 741 tests passed with 9 skipped.

### Phase 5 — Mandatory real validation

In disposable repositories, run:

1. Exact adapter invocation and JSONL/session/activity capture.
2. Gating attacks for protected tests, annotations, destructive Git commands,
   shell rewrites, background tasks, and secret reads.
3. Malicious personal configuration isolation.
4. Cancellation and descendant cleanup.
5. Review isolation and canonical verdict creation.
6. A complete Cosmo lifecycle: propose, implement, validate, review, commit,
   merge, and finish.
7. Authentication, usage-token, quota, and cost-degradation observation.

Verify afterward that no real queue, repository, credential file, or unrelated
worktree was changed. Only then decide whether `supports_gating` can become
`True`.

Completed 2026-09-06. Exact adapter calls captured real command and file-change
events, isolated malicious personal configuration while retaining saved auth,
reaped a SIGTERM-resistant descendant, produced a fresh canonical review
verdict without changing source, and denied every listed hostile operation.
The workspace sandbox rejected an out-of-worktree write. A full disposable
single-task lifecycle reached `DONE`, merged `HELLO.md`, and archived/promoted
the OpenSpec change. The runs also found and corrected the incompatible
`.codex` link, stale composed-invoker `cwd`, an overbroad read-only `sed`
classifier, and linked-worktree Git metadata protection. Cosmo now creates a
bounded implementation commit after a successful call when the harness leaves
pending source changes. `supports_gating` is `True` for the validated profile.
Across all diagnostic and decisive calls, Codex reported 2,006,282 input tokens
(1,572,224 cached), 38,004 output tokens, and 9,985 reasoning tokens, with no
authoritative USD cost or quota event.

### Phase 6 — Documentation and release evidence

Update the English and Spanish user documentation for:

- Harness selection and setup.
- Codex authentication.
- Subscription versus API-billed execution.
- Model configuration.
- Cost and quota limitations.
- Guardrail coverage and any residual limitations.
- Adapter authoring guidance.

Record the implementation in `docs/v3-implementation-state.md` using the next
available deviation number, and update validation tracking documents in place.
Run `./check.sh` and record the exact test result before declaring completion.

Completed 2026-09-06. English and Spanish documentation now covers every item
above. Release evidence is recorded in the Codex handoff, Phase 0 findings,
deviation 90, and the deferred-validation tracker. Final verification:
`./check.sh` exited 0 on 2026-09-06; Ruff and formatting passed, mypy found no
issues in 178 source files, and pytest reported 755 passed and 9 skipped in
90.66 seconds.

## Acceptance criteria

The work is complete only when:

- `codex` is discoverable and selectable anywhere Cosmo exposes harnesses.
- Propose, implement, retry, review, cancellation, and raw logging satisfy the
  existing harness contract.
- Success depends only on the Codex process exit code.
- No model prose is parsed as a control signal.
- Personal configuration cannot silently change automated execution behavior.
- The unrestricted sandbox bypass is structurally absent.
- Review cannot modify source files.
- Guardrail capability is reported honestly based on real adversarial results.
- Bootstrap is idempotent and preserves user-owned paths.
- Unit, integration, boundary, and real lifecycle validations pass.
- English and Spanish documentation describe the actual observed behavior.
- `./check.sh` exits successfully.

## Suggested commit sequence

1. Codex stream fixtures, parser, and invoker mechanics.
2. Adapter contract, prompts, registry, and configuration integration.
3. Template, skills, hooks, and bootstrap symlinks.
4. Guardrail, cancellation, boundary, and end-to-end test hardening.
5. Real validation corrections and user documentation.

Each commit should explain why the change is needed. Follow the repository's
AI-attribution policy and never add model-naming `Co-Authored-By` or
`Assisted-by` trailers.
