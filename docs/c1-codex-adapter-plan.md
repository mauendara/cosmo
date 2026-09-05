# C1 — Codex harness adapter and template plan

Status: Phase 0 in progress; see `docs/codex-phase0-findings.md`
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
expose the parts Codex discovers through safe symlinks:

```text
.codex         -> .agent/codex
.agents/skills -> ../.agent/codex/skills
```

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

- Add recorded JSONL fixtures and parser tests first.
- Implement incremental classification and activity descriptions.
- Build deterministic argv and environment construction.
- Integrate `ManagedProcess`, raw logs, cancellation, and result mapping.
- Add a fake Codex executable for success, failure, malformed output, hangs,
  and grandchildren.

### Phase 2 — Adapter and registry

- Implement propose, implement, review, preflight, progress, and cancel.
- Add exact role prompt construction and retry-context tests.
- Register `codex` and extend boundary allowlists only where required.
- Keep preflight cheap and side-effect-free: binary presence, supported config,
  and unsafe billing-variable checks only. Authentication belongs in probe.

### Phase 3 — Template and bootstrap

- Add `CODEX.md`, hooks, and Codex-discoverable skills.
- Add safe `.codex` and `.agents/skills` symlink support.
- Add bootstrap, resynchronization, collision, and idempotency tests.
- Verify every hook/config reference resolves inside `.agent/codex`.

### Phase 4 — Guardrail and integration tests

Add at least:

- `tests/test_harness_codex_stream.py`
- `tests/test_harness_codex_adapter.py`
- `tests/test_hooks_codex_*.py`
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
