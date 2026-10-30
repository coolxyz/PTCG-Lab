import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
export default defineConfig({ testDir: ".", testMatch: ["artwork-retry.spec.ts", "local-artwork-settings.spec.ts"], workers: 1,
  timeout: 30000, reporter: "list", use: { baseURL: "http://127.0.0.1:5189",
    launchOptions: process.env.PTCG_BROWSER_PATH ? { executablePath: process.env.PTCG_BROWSER_PATH } : {} },
  outputDir: "../../var/artwork-web-results",
  webServer: { command: "npm exec vite -- --config tests/web/vite.config.ts", cwd: fileURLToPath(new URL("../..", import.meta.url)),
    url: "http://127.0.0.1:5189/artwork-fixture.html", reuseExistingServer: false } });
