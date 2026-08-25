/**
 * Where the frontend expects the backend. Overridable at build time via
 * VITE_BACKEND_URL so the gate's e2e stage can point a container build at a
 * sibling container by Docker network hostname instead of localhost.
 */
export function backendUrl(): string {
  return import.meta.env.VITE_BACKEND_URL ?? "http://localhost:8080";
}
