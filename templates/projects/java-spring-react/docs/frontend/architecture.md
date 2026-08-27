# Frontend Architecture

Vite + TypeScript + React + Tailwind.

## Structure

Feature-folder organization: `src/features/<feature>/` holds that feature's
components, hooks, and API calls together, rather than splitting by type
(`components/`, `hooks/`, `api/`) across the whole app. `src/components/`
holds genuinely shared, feature-agnostic UI primitives only -- if a
component is only ever used by one feature, it lives in that feature's
folder, not in shared.

## Data flow

Server state (anything fetched from the backend) is fetched and cached
through a dedicated data layer (e.g. TanStack Query) -- never stored
ad hoc in `useState`/`useEffect`, which loses caching, refetch-on-focus, and
a consistent loading/error shape for free. See `state-management.md` for the
local-vs-server-state boundary.

## Key dependencies

- **Vite** for dev server + build -- fast HMR, native ESM. The gate's build
  stage runs inside a fixed Node image (`gate.frontend_image`,
  `node:24.19-bookworm` as of this writing), which satisfies current Vite's
  `engines.node` requirement (`^20.19.0 || >=22.12.0` as of Vite 8) with
  headroom. If a future Vite major raises that floor again, bump
  `gate.frontend_image` to match rather than pinning Vite to an old major --
  check `npm view vite engines` against whatever this repo's `docs/` (or a
  prior task's committed `package.json`) says the gate image actually is
  before assuming `npm install`'s "latest" is safe. A mismatch here fails
  the gate with an opaque native-binding error, not a clear version-mismatch
  message.
- **TypeScript**, strict mode. A type error is a build failure, not a
  warning to fix later.
- **Tailwind** for styling -- see `styling.md`.
- **Playwright** for e2e, run headless against the built app inside the
  validation gate's Docker container (spec 1.1) -- never against a
  developer's locally-running dev server, so the gate result reflects what
  actually ships.

## Gate compatibility: `vite.config.ts`

The gate's e2e stage reaches `vite preview` by its Docker network container
hostname, not `localhost` -- Playwright and the frontend run in separate
containers on a shared network (Phase 6). Vite's preview server rejects
any other Host header by default (a DNS-rebinding guard) and fails with
"Blocked request. This host ... is not allowed", which surfaces in Playwright
as a confusing "element not found" rather than a clear cause. Set
`preview.allowedHosts: true` in `vite.config.ts` so the gate can reach it.
