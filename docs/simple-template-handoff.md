# Handoff — Cosmo Greenfield Testbed Setup

## Context

Cosmo (the autonomous dev-loop orchestrator, v3 spec) needs its first greenfield tests. The goal of this batch is to isolate Cosmo's own behavior (queue handling, retries, guardrails, task sizing) from stack complexity — so any failure observed can be attributed to Cosmo, not to Spring Boot / MariaDB / Docker plumbing.

This handoff covers two deliverables for the next session:

1. A new project template, `templates/projects/vite-react-local/`, alongside the existing `_blank` and `java-spring-react` templates.
2. Six small greenfield project ideas, each intended to become a short OpenSpec spec (single-digit task count — small enough that a failure is traceable to one step, not buried in a long list).

---

## Part 1 — Template: `templates/projects/vite-react-local/`

### Stack decision (already made, do not re-litigate)

- **Vite + React + TypeScript + Tailwind**, frontend-only, no backend.
- **Persistence:** `localStorage` via a small `useLocalStorage` hook — no server, no DB, no Docker.
- **Testing:** Vitest + React Testing Library for unit/component tests; Playwright for e2e.
- **Rendering rule, including for the game ideas (Memory, Snake/2048): DOM + CSS grid only, never `<canvas>`.**
  - Rationale: these are discrete-grid games (not continuous motion/physics), so DOM is the natural fit, not a compromise.
  - Canvas would force either pixel/screenshot-based e2e assertions (fragile) or exposing a `window.__gameState` escape hatch for Playwright to read via `page.evaluate()` — and that escape hatch is itself a new test-gaming surface (the agent could report a fake state object that doesn't match what's actually rendered), which works against Cosmo's §6.1 test-integrity guardrail.
  - A canvas/Phaser-rendered game is **deliberately excluded** from this batch — see the note at the end of Part 2.

### Directory structure to create

```
templates/
  projects/
    vite-react-local/
      docs/
        frontend/
          architecture.md
          state-management.md
          styling.md
        persistence.md
        data-model.md
        testing.md
        base-standards.md
```

Note what's *absent* compared to `java-spring-react`: no `backend/*.md`, no `api-spec.yml`, no standalone `security.md` (folded into `base-standards.md` as a short paragraph, since there's no network/auth surface to warrant its own file).

### File-by-file content guidance

Write **real starter content**, not schema-only placeholders (this template plays the same role `java-spring-react` does, not the role `_blank` does).

**`frontend/architecture.md`**
- Folder layout: `src/components/`, `src/hooks/`, `src/lib/`, `src/types/`.
- One component per file.
- Domain/business logic lives in custom hooks; components stay presentational.
- Explicit rule: grid-based/game UIs render as DOM + CSS grid, never `<canvas>`.

**`frontend/state-management.md`**
- Default to `useState`/`useReducer` + custom hooks.
- No state management library (Redux, Zustand, Jotai, etc.) unless a specific task explicitly justifies it.
- One-line rationale: prevents the agent from introducing unnecessary dependencies on a simple CRUD/game app.

**`frontend/styling.md`**
- Tailwind utility classes only, mobile-first.
- No CSS-in-JS.

**`persistence.md`** (replaces the backend-side persistence doc from `java-spring-react`)
- Key naming convention: `<app-slug>:<entity>:v<n>`.
- Versioning/migration strategy: **on version mismatch, wipe and reinitialize.** Acceptable here because this is a disposable testbed, not production user data — don't over-engineer migration logic.
- Handle `QuotaExceededError` on write.
- Serialization pattern: `JSON.stringify`/`JSON.parse` wrapped in `try/catch`, falling back to default state on parse failure.

**`data-model.md`**
- TypeScript types/interfaces for every persisted entity — single source of truth.
- Short comment per entity on invariants (e.g., "streak count never negative").

**`testing.md`** (new — didn't exist in `java-spring-react`)
- Vitest + React Testing Library for unit/component tests.
- Playwright for e2e, using semantic queries (`getByRole`, `getByText`) against the DOM/accessibility tree — never pixel or screenshot assertions.
- Defines what counts as a "test path" for Cosmo's test-gaming guardrail (§6.1 of the v3 spec) — e.g. `src/**/*.test.ts(x)`, `e2e/**` — so the `PreToolUse` hook and diff gate have concrete patterns to protect.

**`base-standards.md`**
- TypeScript strict mode.
- Naming conventions, commit message format.
- Short paragraph on input sanitization/XSS (any user-entered text rendered back to the DOM must be escaped) — this is the one carry-over from `security.md`, right-sized for a no-backend app.

### Steps for next session

1. Create the directory tree above under Cosmo's own repo, at `templates/projects/vite-react-local/`.
2. Write each doc file with real content per the guidance above (not headings-only).
3. Confirm `cosmo templates list` picks up `vite-react-local` alongside `_blank` and `java-spring-react` — no code change should be needed if the template resolution is purely directory-based (per §10.3/§10.4 of the v3 spec), but verify.
4. No changes needed to `templates/harness/claude/` — harness policy templates are stack-agnostic by design.

---

## Part 2 — Six test ideas

All six use the `vite-react-local` template. Each should be scoped tightly enough that its OpenSpec spec produces a small, single-digit task list — the point is to observe Cosmo's loop behavior clearly, not to stress-test a large backlog yet.

1. **Todo list with categories and filters** — CRUD on todos, category tagging, filter/sort by status or category. The baseline "hello world" run for the loop.
2. **Habit tracker with daily streaks** — mark a habit done per day, compute current/longest streak. Exercises date-boundary and timezone edge cases.
3. **Pomodoro timer with session history** — focus/break state machine, countdown timer, persisted log of past sessions with aggregate stats.
4. **Memory (concentration) card game** — flip-two-match gameplay, move counter, best-score persistence. First "game-like" case; DOM/CSS grid rendering per the template rule.
5. **Snake or 2048** — grid-based movement/merge logic, keyboard input, game-over/collision detection, high score. More algorithmic than #4; still DOM/CSS grid.
6. **Expense tracker** — log income/expenses with categories, running totals and simple aggregation (e.g., spend by category). More calculation-heavy than the others, less pure CRUD.

### Explicitly excluded from this batch

A **canvas- or Phaser-rendered game** is a deliberate 7th idea, held out for a later, separate test — specifically to see how Cosmo handles a rendering stack where e2e state can't be read from the DOM/accessibility tree. That test needs its own guardrail thinking (e.g., how to validate a `window.__gameState` exposure without opening a new test-gaming vector) and shouldn't be mixed into this DOM-only batch.

---

## Open questions for next session

- The original scoping note said specs should generate "between 1 and [a number] tasks max" — the upper bound wasn't actually specified. Confirm the intended ceiling before writing the OpenSpec specs.
- Decide execution order across the six ideas (e.g., simplest CRUD first, games last).
- Decide whether the `docs/` content for `vite-react-local` is finalized by this session, or left for Cosmo to fill in via `cosmo init` against a first draft.