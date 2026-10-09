import { defineConfig } from "@playwright/test";
import path from "node:path";
const root = path.resolve(import.meta.dirname, "../..");
const python = path.join(
  root,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 30000,
  // The complete source catalogue and battle session load asynchronously.
  // UI timing/animation budgets remain measured separately in performance.spec.
  expect: { timeout: 15000 },
  reporter: [
    ["list"],
    ["json", { outputFile: "../../var/browser-results.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:8766",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    launchOptions: process.env.PTCG_BROWSER_PATH
      ? { executablePath: process.env.PTCG_BROWSER_PATH }
      : {},
  },
  webServer: {
    command: `"${python}" -X utf8 -m uvicorn apps.api.main:create_app --factory --app-dir ../.. --host 127.0.0.1 --port 8766`,
    url: "http://127.0.0.1:8766/api/meta",
    reuseExistingServer: false,
    env: { PTCG_DB: path.join(root, "var", `e2e-${Date.now()}.sqlite`) },
    timeout: 15000,
  },
});
