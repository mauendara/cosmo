# Frontend Architecture

Vite + TypeScript + React + Tailwind, frontend-only. There is no backend and
no `docs/backend/` in this template -- see `../persistence.md` for how state
survives a reload without one.

## Structure

- `src/components/` -- one component per file, presentational. A component
  reads state and calls callbacks; it does not own business logic or reach
  into `localStorage` directly.
- `src/hooks/` -- domain/business logic lives here as custom hooks (e.g. a
  `useTodos` hook owns CRUD + filtering for the todo app; a `useStreak` hook
  owns streak computation for the habit tracker). Components stay thin
  enough that reading one tells you what renders, not how the domain works.
- `src/lib/` -- pure functions with no React dependency (date-boundary math,
  score calculation, serialization helpers) -- anything a hook needs that
  doesn't itself need to be a hook. Keeping these plain functions makes them
  trivial to unit-test without React Testing Library.
- `src/types/` -- the TypeScript interfaces for every persisted entity; see
  `../data-model.md`.

## Rendering rule: DOM + CSS grid, never `<canvas>`

Grid-based UIs (a Memory/concentration board, Snake, 2048) render as real DOM
elements in a CSS grid layout, never a `<canvas>` bitmap. These are discrete
grid games, not continuous motion/physics, so DOM is the natural fit here,
not a compromise made to save effort.

This is a hard rule, not a style preference: a canvas board has no
accessibility tree and no semantic elements for Playwright to query, which
forces either fragile pixel/screenshot e2e assertions or a
`window.__gameState` escape hatch read via `page.evaluate()`. That escape
hatch is itself a new test-gaming surface -- nothing stops a future change
from making the exposed state object drift from what's actually on screen,
and an e2e suite that can be satisfied by a fake state object without the
real board matching it defeats the point of e2e coverage (§6.1's guardrail
is about exactly this kind of gap). `getByRole`/`getByText` against real DOM
closes that gap structurally; a canvas escape hatch reopens it.

## Data flow

Everything is client state -- there is no server state, no data-fetching
layer, and no loading/error shape to manage for a network call that doesn't
exist. See `state-management.md` for the local-state discipline this implies
and `../persistence.md` for how a hook's state gets to and from
`localStorage`.

## Repo layout: the app lives under `frontend/`, not the repo root

Even though this template has no backend counterpart, the Vite app must
still live at `<repo-root>/frontend/`, not flattened into the repo root
itself. Cosmo's validation gate resolves both the build and unit-test stages
by looking for a `frontend/` directory in the worktree (the same convention
`java-spring-react` uses for its own frontend half) -- an app at the repo
root simply isn't found, and the gate silently treats the task as having no
frontend to build or test at all, which is a `passed: true` (nothing done),
not a stage failure. There is no `backend/` directory in this template, and
that's fine: the gate's e2e stage runs Playwright against the frontend alone
when no `backend/` exists (it does not require a backend, only a `frontend/`
to exist) -- see `testing.md`.

## Gate compatibility: `vite.config.ts`

The gate's e2e stage reaches `vite preview` by its Docker network container
hostname, not `localhost` -- Playwright and the frontend run in separate
containers on a shared network. Vite 5's preview server rejects any other
Host header by default (a DNS-rebinding guard) and fails with "Blocked
request. This host ... is not allowed", which surfaces in Playwright as a
confusing "element not found" rather than a clear cause. Set both of these
in `vite.config.ts`'s `preview` block (mirrors the fixture repo Cosmo's own
gate tests were validated against -- see `tests/fixtures/gate_repo/frontend/
vite.config.ts` in Cosmo's own repo if you need a working reference):

```ts
preview: {
  port: 4173,
  host: true,          // bind 0.0.0.0, not just localhost
  allowedHosts: true,  // accept the container-hostname Host header
}
```

## Key dependencies

- **Vite** for dev server + build -- fast HMR, native ESM.
- **TypeScript**, strict mode. A type error is a build failure, not a
  warning to fix later.
- **Tailwind** for styling -- see `styling.md`.
- **Playwright** for e2e, run headless against the built app inside the
  validation gate's Docker container -- never against a developer's locally
  running dev server, so the gate result reflects what actually ships.
