# v10 — Discrepancies found while writing the public user documentation

**Status: tracking document, not a plan.** Six places where the brief in
`docs/ignored/prompts/user-doc-completion.md` (or the internal specs behind
it) described Cosmo differently from what the code actually does. The brief
was explicit that a mismatch must be documented as the real behavior and
flagged, never silently reconciled — this is that flag list, kept so the next
session doesn't rediscover each one from scratch.

Each entry records **what the code does**, **where the user docs say so**,
and **whether anything is owed**. Four of the six are simply reality being
narrower than the brief and need no code change. Two (items 1 and 5) are
genuine loose ends worth a decision.

None of these were fixed this session — the session's scope was
documentation, not code.

---

## 1. `task.guardrail_tripped` is declared but never emitted

**Code.** `EventType.TASK_GUARDRAIL_TRIPPED = "task.guardrail_tripped"` is a
real member of the event enum in `events/envelope.py`, listed among spec
9.2's own event types. Nothing anywhere in `src/` emits it — a grep for the
member and for the literal string finds the declaration and nothing else.

A guardrail denial today surfaces two other ways: in the harness session's
own raw log (the hook's deny message reaches the agent, not Cosmo), and
indirectly in whatever failure the denial ultimately causes. There is no
queryable "a hook fired" signal.

**Documented in.** `user-docs/reference/event-schema.md` gives it its own
entry under the task-level events, marked **"Declared but not emitted"**,
explaining that guardrail denials surface in the session log and the
resulting failure instead, and telling the reader not to build alerting on
it.

**Owed.** A real decision, either way: emit it from the hook path (which
means the hooks — separate OS processes — need a way to write an event, and
they already have `COSMO_DB_PATH` for exactly that kind of need), or remove
the enum member. Leaving a declared-but-dead event type is the sort of thing
that reads as a bug to anyone querying the schema. Not urgent; nothing
depends on it.

## 2. `task.failed` is emitted only by the process-reap path

**Code.** Spec 9.3 describes `task.failed` as the per-failure event, with a
payload shape (`failure_type`, `failure_stage`, `error_summary`,
`next_action`, …) that `store.writer`'s own `task_failures` columns
deliberately mirror. In practice the only emitter is
`proc/reap.py`, for a failed process reap, at `severity=critical` with a
reap-specific payload (`circuit_breaker_weight`, `containers_removed`,
`worktree_holder_pids`).

Ordinary task failures are recorded to the `task_failures` **table**, not to
this event. `run/loop.py`'s only reference to the type is a *read* — it
scans past `task.failed` events to recover the circuit-breaker weight a reap
failure recorded.

This is not a defect so much as an unstated redundancy: the table is the
real per-attempt record, and it carries `error_detail` (assertion text, stack
excerpts) that the event payloads deliberately never do.

**Documented in.** `user-docs/reference/event-schema.md` — the `task.failed`
entry opens with "**Emitted only by the process-reap path**" and points at
`cosmo queue failures`. The same reference's closing "Related tables" section
makes the table-versus-event split explicit, and
`user-docs/concepts/validation-gate-and-guardrails.md` repeats it where it
explains the failure record.

**Owed.** Nothing. Documented as-is. Worth knowing only if someone later
tries to build failure alerting on the event stream alone — it won't work,
and the docs now say so.

## 3. The diff gate rejects *any* modification to an existing test file

**Code.** `gate/diffgate.py` raises five violation kinds. The one that
surprises: `test_path_modified`, for a test file that was changed at all, not
just weakened. A newly *added* test file is explicitly exempt (with a
comment recording that an early version rejected every task that added an e2e
test, "which defeats the point of an autonomous agent that writes its own
tests"), but it is still subject to the assertion-count, skip-annotation and
LOC checks.

So the practical rule is: **add tests freely, touch an existing test never** —
unless the task carries `allow_test_edits`, which bypasses the diff gate
entirely for it.

The brief described this layer as "a diff gate counts assertions and blocks
weakened tests," which is accurate about two of the five kinds and
understates the other three. The blunt rule is deliberate — distinguishing an
honest test update from a self-serving one is exactly the judgment an
unsupervised agent cannot make about its own work — but a user who doesn't
know it will read a `test_integrity` block as a false positive.

Also worth stating precisely: `assertion_count_decreased` is computed
**net across the whole diff**, not per file.

**Documented in.**

- `user-docs/concepts/validation-gate-and-guardrails.md` — a table of all
  five violation kinds, plus two paragraphs on the added-versus-modified
  asymmetry and why the rule is blunt on purpose.
- `TROUBLESHOOTING.md` — under the `test_integrity` symptom, called out as
  "the second one catches people out."
- `FAQ.md` — the `allow_test_edits` answer now leads with it.

**Owed.** Nothing code-side. This is the single most likely source of "why
did Cosmo reject my perfectly reasonable change" confusion, so it is stated
in three places on purpose rather than once.

## 4. The MCP control plane does not exist

**Code.** Nothing. The brief listed it (with its own "(not yet implemented)"
note) as a capability distinct from Cosmo *using* an agent as a harness, and
asked that the two be documented separately and not conflated.

**Documented in.** `user-docs/concepts/architecture-overview.md`, under an
explicit **"Not implemented yet"** heading at the end, alongside the
missing second adapter and parallel execution. `FAQ.md` answers "Can an agent
drive Cosmo through MCP?" with "Not yet … **No such server exists today.**
Use the CLI."

Deliberately kept out of the README's differentiator bullets — a planned
capability in a feature list reads as a shipped one.

**Owed.** Nothing, unless it gets built. If it does, it belongs in
`v9-out-of-scope-desirables.md`'s framing first.

## 5. Gate stage *commands* are hardcoded; only images and directories are configurable

**Code.** `gate/runner.py` hardcodes `["mvn", "-B", "-q", "-DskipTests",
"package"]` for the backend build and `["sh", "-c", "npm ci && npm run
build"]` for the frontend, with the unit and e2e stages similarly fixed.
`GateConfig` makes the *images* (`backend_image`, `frontend_image`,
`playwright_image`) and the *directories* (`backend_dir`, `frontend_dir`)
configurable, and stage selection is directory-driven — a missing
`backend_dir` skips the backend stages, a missing `frontend_dir` skips e2e.

The brief said "the harness and template system are not hard-coded to
[the target stack]." That's true of the template system and true of the
images, but not of the build commands. A Go or Rails project can use the
template system for its documentation today and get nothing usable out of the
gate.

Practical downstream constraint this implies for any new frontend template:
the repo it produces must have a committed lockfile and an `npm run build`
script, or the build stage fails on `npm ci`.

**Documented in.**

- `user-docs/how-to/add-project-template.md` — a "Match the gate to the
  stack" section stating the limit outright and naming the lockfile/`build`
  script requirement.
- `FAQ.md` — "Does it work with my stack?" gives the honest answer rather
  than the brief's.
- `user-docs/reference/config-schema.md` — the `[gate]` table documents
  what *is* configurable, which by omission shows what isn't.

**Owed.** This is precisely what
[v6-project-template-aware-stuff-plan.md](v6-project-template-aware-stuff-plan.md)
exists to address, and v6 is deliberately not started (it needs a real second
stack first). Nothing new is owed — but the user docs now describe the
current limit honestly instead of implying v6 already landed.

## 6. `templates/` requires an editable install

**Code.** `bootstrap/discover.py` resolves `templates/` relative to its own
file location (three parents up from `src/cosmo/bootstrap/discover.py`),
because the directory lives at the repo root alongside `src/`, not inside the
installed package. Its own docstring records that this "only resolves
correctly for an editable install" and that a packaged wheel would need to
ship `templates/` as real package data instead.

Confirmed live while capturing output for the docs: running `cosmo init` from
a globally installed (non-editable) `uv tool` build fails with

```
Cosmo's templates/ directory was not found at
/home/dev/.local/share/uv/tools/cosmo/lib/python3.14/templates. This requires
an editable install (`uv tool install --editable .`) from a full checkout of
Cosmo's own repository.
```

The error is clear and actionable, which is why this is a documentation item
rather than a bug. But `uv tool install --editable .` reads like an optional
convenience in an install section unless the reason is stated.

**Documented in.**

- `user-docs/tutorial.md` — the install step shows the exact error and says
  to use `--editable` from a full checkout.
- `TROUBLESHOOTING.md` — "Templates not found" reproduces the message.
- `CONTRIBUTING.md` — the setup block carries the reason inline.
- `user-docs/how-to/setup-vps.md` — "Keep the checkout" is called out where
  someone would otherwise be tempted to install and delete.

**Owed.** Nothing, while the documented install method is the editable one.
Shipping a real wheel later means moving `templates/` into package data —
already noted in `discover.py`'s own docstring, and deliberately not solved
there.

---

## Non-discrepancies worth recording

Two things the brief described that turned out to be **exactly right**, noted
so nobody re-audits them:

- **The three-layer anti-test-gaming defense** is real and correctly
  described: `PreToolUse` hooks (prevention, `templates/harness/claude/hooks/`),
  the diff gate ahead of the tests (detection), and the same diff gate
  standing alone as post-hoc inspection for an adapter declaring
  `supports_gating: false`. The third "layer" is the second one's code path
  without the first in front of it — stated that way in the docs rather than
  implying three separate mechanisms.
- **The permission model's fail-closed posture** — `dontAsk` as default,
  `bypassPermissions` asserted absent rather than merely omitted, deny rules
  absolute across modes — matches the adapter exactly, including the
  non-obvious workspace-trust workaround (the allow-list passed as a CLI flag
  *and* in `settings.json`, because a freshly created worktree can never have
  been through the interactive trust dialog).
