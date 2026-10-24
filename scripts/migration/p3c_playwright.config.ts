import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";
const root = resolve(import.meta.dirname, "../..");
export default defineConfig({
  workers: 1,
  timeout: 180000,
  expect: { timeout: 15000 },
  reporter: [
    ["list"],
    ["json", { outputFile: resolve(root, "var/p3c/browser-results.json") }],
  ],
  outputDir: resolve(root, "var/p3c/test-results"),
  use: {
    actionTimeout: 15000,
    navigationTimeout: 30000,
    baseURL: "http://127.0.0.1:8783",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    launchOptions: process.env.PTCG_BROWSER_PATH
      ? { executablePath: process.env.PTCG_BROWSER_PATH }
      : {},
  },
  projects: [
    {
      name: "product",
      testDir: resolve(
        process.env.PTCG_PRACTICE_TEST_STAGE ?? "missing-private-stage",
        "app/apps/web/e2e",
      ),
      testMatch:
        /(?:workflows|card-artwork|collection-sync|deck-delete|upstream-sync)\.spec\.ts/,
    },
    {
      name: "practice",
      testDir: resolve(root, "apps/desktop-web/practice-e2e"),
    },
    {
      name: "integration",
      testDir: resolve(root, "apps/desktop-web/product-e2e"),
    },
  ],
  webServer: {
    command: `node "${resolve(root, "scripts/migration/p3b_server.mjs")}"`,
    url: "http://127.0.0.1:8783/api/meta",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
