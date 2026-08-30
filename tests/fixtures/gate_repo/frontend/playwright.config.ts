import { defineConfig } from "@playwright/test";

/**
 * No `webServer` block on purpose: the gate runner starts backend and
 * frontend as separate long-lived containers before this runs (spec 1.2's
 * serial build -> unit -> e2e ordering already finished by this point), and
 * BASE_URL points at the frontend container's address on the shared network.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  reporter: [["json", { outputFile: "playwright-report/results.json" }], ["line"]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
