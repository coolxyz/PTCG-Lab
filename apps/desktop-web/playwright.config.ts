import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 30000,
  reporter: [
    ["list"],
    ["json", { outputFile: "../../var/p3/browser-results.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:5183",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    launchOptions: process.env.PTCG_BROWSER_PATH
      ? { executablePath: process.env.PTCG_BROWSER_PATH }
      : {},
  },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5183",
    reuseExistingServer: false,
    timeout: 15000,
  },
});
