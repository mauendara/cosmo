import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
  preview: {
    port: 4173,
    host: true,
    // Vite 5's Host-header allowlist (a DNS-rebinding guard) rejects
    // anything but localhost/IP by default. The gate's e2e stage reaches
    // this server by Docker network hostname, not localhost -- confirmed
    // by hand via a real Playwright run: without this, the page 404s with
    // "Blocked request. This host ... is not allowed" and every e2e
    // assertion fails as "element not found" rather than a clear cause.
    allowedHosts: true,
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
