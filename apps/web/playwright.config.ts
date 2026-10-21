import { defineConfig, devices } from "@playwright/test";
import { existsSync } from "node:fs";
if (existsSync("../../.env")) process.loadEnvFile("../../.env");
export default defineConfig({
  testDir: "e2e", fullyParallel: false, workers: 1, retries: 0, timeout: 120_000,
  expect: { timeout: 15_000 }, reporter: [["list"], ["json", { outputFile: "test-results/results.json" }]],
  use: { baseURL: process.env.E2E_URL || "http://localhost:3000", trace: "off", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
