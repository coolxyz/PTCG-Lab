import base from "./playwright.config";
import { defineConfig } from "@playwright/test";
export default defineConfig({...base, testMatch: "h2-experts.spec.ts", webServer: undefined, use: {...base.use, baseURL: process.env.EXPERT_TEST_URL || "http://127.0.0.1:8767"}, reporter: [["list"], ["json", {outputFile: "../../var/expert-product-check/browser-results.json"}]]});
