import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
export default defineConfig({
  testDir: "e2e",
  workers: 1,
  timeout: 30000,
  outputDir: "../../var/p8e-ios/browser-results",
  use: {
    browserName:
      process.env.PTCG_TEST_BROWSER === "webkit" ? "webkit" : "chromium",
    isMobile: process.env.PTCG_TEST_BROWSER === "webkit",
    hasTouch: process.env.PTCG_TEST_BROWSER === "webkit",
    baseURL: "http://127.0.0.1:5193",
    launchOptions:
      process.env.PTCG_BROWSER_PATH &&
      process.env.PTCG_TEST_BROWSER !== "webkit"
        ? { executablePath: process.env.PTCG_BROWSER_PATH }
        : {},
  },
  webServer: {
    command: "npm run dev --workspace @ptcg/mobile",
    cwd: fileURLToPath(new URL("../..", import.meta.url)),
    url: "http://127.0.0.1:5193",
    reuseExistingServer: false,
  },
});
