# Cosmo — v6: making the gate (and its failure classifier) project-template-aware

## Status

**Not started — design record only.** Written to capture a real gap found
by hand during the v5 acceptance run's own retry loop (see
[v3-implementation-state.md](v3-implementation-state.md)'s Phase 10
section for the concrete incident: a real task blocked repeatedly on
npm/Playwright/gitleaks-specific problems), not invented for coverage's
sake. Nothing here should be implemented opportunistically alongside an
unrelated change — it's a real architectural expansion (a second stack
needs to exist to prove the abstraction is right, not just declared), and
deserves its own pass the way v4 and v5 each got one.

## Context: what the spec actually commits to here

This isn't new scope invention either. Two separate axes, and the spec
treats them very differently:

- **Harness-agnostic is a hard, tested architectural line.**
  [v3-cosmo-autonomous-agent-spec.md](v3-cosmo-autonomous-agent-spec.md)
  §2: *"Cosmo never talks to a specific harness directly — it talks to an
  adapter implementing a common interface."* Enforced by
  `tests/test_harness_boundary.py`, which asserts no harness-specific
  string (`claude`, `ANTHROPIC_API_KEY`, …) appears anywhere in
  `cosmo.config`, `cosmo.cli.doctor`, or other core modules. §12 lists
  "any harness other than Claude Code CLI" as an explicit v1 non-goal, not
  a rejected idea.
- **Stack-agnostic is a stated intent with no equivalent enforcement.**
  §10.3: *"`templates/projects/<name>/` is stack-specific starter content
  for `docs/`. Multiple named project templates can coexist: `_blank` is
  the conservative default; `java-spring-react` matches this developer's
  current stack; more can be added as new stacks come up."* That's the
  whole mechanism today — it covers `docs/` scaffolding
  (`templates/projects/{_blank,java-spring-react,vite-react-local}/docs/`)
  and nothing else. There is no test analogous to
  `test_harness_boundary.py` that would catch a stack-specific string
  leaking into core `gate`/`store` code, because nothing has ever asked
  that question before this.

## What's actually stack-coupled today, with file:line evidence

Four layers, each more load-bearing than the last:

1. **Gate config is global, not per-project.** `GateConfig`
   (`src/cosmo/config/model.py:101-150`) has exactly one
   `backend_image`/`frontend_image`/`playwright_image`/`backend_dir`/
   `frontend_dir` for the whole Cosmo installation — not one per
   registered project, not one per project template. Two different
   projects on two different stacks, registered on the same host, would
   have to fight over the same `defaults.toml`/override file.
2. **The gate's own shell commands are hardcoded**, not just the images
   they run in — this is the deepest coupling, well past "wrong Docker
   tag": `src/cosmo/gate/runner.py:53` (`mvn -B -q -DskipTests package`),
   `:76`/`:286` (`sh -c "npm ci && npm run build"`), `:118` (`mvn -B -q
   test`), `:250` (`mvn -B -q spring-boot:run`), `:313` (`npm run
   preview`). A Python/Django backend or a pnpm/Yarn frontend can't run
   through this gate at all today, config changes alone wouldn't be
   enough.
3. **`store/failure_signature.py`'s taxonomy** (`missing_lockfile`,
   `node_engine_mismatch`, `enoent_node_modules`, and this session's
   `secrets_stray_backup_artifact`, `playwright_image_version_mismatch`)
   matches npm/Playwright/gitleaks output text specifically. Harness-
   agnostic (never looks at which harness produced the code — confirmed
   when this was raised), but not stack-agnostic.
4. **One partial, ad-hoc seam already exists and is worth keeping**:
   `gate/runner.py:222`, `has_backend = backend_dir.is_dir()` — a runtime
   filesystem check standing in for real per-project stack metadata. It's
   how `vite-react-local` (frontend-only, no `backend/`) already works
   without a backend-specific config flag. A real design should probably
   formalize this pattern (ask the project what it has), not just add more
   flags beside it.

## What a real fix looks like (sketch, not a committed design)

Open questions that need real decisions before this is buildable, not
prescriptions:

- **Where does a stack declare its build/test/e2e commands and images?**
  A manifest file living in each `templates/projects/<name>/` (e.g.
  `gate.toml` alongside the existing `docs/`), read at `cosmo init` time
  and folded into that project's own config the way `docs/` is already
  copied? Or a small Python plugin per stack, registered the same way
  `cosmo.harness.registry` registers harness adapters (giving `gate/`
  the same adapter-interface treatment `harness/` already got in v1)?
  The harness adapter precedent (§2.2's `propose`/`implement`/`validate`/
  `cancel` interface) is the closest existing analogue and worth studying
  first.
- **Does `GateConfig` become per-project, or does `cosmo init` bake a
  resolved copy into that project's own config file?** Per-project-at-
  runtime avoids config duplication; baked-in-at-init avoids a second
  registered project's stack ever silently reconfiguring the gate under a
  task that's already running.
- **Does `failure_signature` need per-stack taxonomies, or does the small,
  generic core (`enoent_node_modules`-style "file/dep missing" shapes)
  stay shared while stack-specific ones move into the project template?**
  `secrets_stray_backup_artifact` (gitleaks + a `*_old`-style directory
  name) is arguably stack-agnostic already — gitleaks and "someone renamed
  a directory aside" aren't npm-specific — worth checking case by case
  rather than assuming everything moves.
- **What's the actual second stack to build this against?** Per this
  project's own working discipline (see `docs/v3-implementation-state.md`
  and every prior phase's handoff: "fake the external process, test the
  mechanics — except where 'check by hand, then use the real thing already
  proved out'"), an abstraction with only one real implementation behind
  it (Java+Spring/Vite+React) is unverified by construction — this needs a
  second, real stack (a Python/FastAPI backend and/or a plain Node/Express
  one would be a reasonably different shape to prove the interface, not
  just a second frontend flavor) exercised for real before calling it done,
  the same way multi-harness support will need a second real harness
  adapter to actually prove `harness/registry`'s abstraction wasn't
  quietly shaped around Claude Code CLI's own specifics.

## Non-goals for this document

- Not a mandate to rewrite `gate/runner.py` opportunistically. This stays
  a backlog item until a real second stack is the actual, concrete reason
  to build it — matching how v4/v5 each started from a real, named trigger
  rather than speculative generalization.
- Not blocking on multi-harness support landing first, or vice versa —
  the two are independent axes (see Context above); either can be built
  first without the other.
