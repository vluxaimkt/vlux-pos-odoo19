import { defineConfig } from "@playwright/test";

/**
 * Golden Path of the register PWA in a real browser against a real, disposable
 * Odoo (tools/e2e/pwa_e2e_env.py prepares it). Nothing is simulated: the app
 * is the build Odoo serves at /vlux-pos/ and every check reads back what Odoo
 * stored. See docs/PRUEBAS_PWA_E2E.md.
 *
 *   VLUX_E2E_BASE_URL  Odoo serving the disposable database (default http://127.0.0.1:8069)
 *   VLUX_E2E_FIXTURE   JSON written by pwa_e2e_env.py (default e2e/.fixture.json)
 *   VLUX_E2E_CHANNEL   installed browser to use: "chrome" (CI), "msedge" (Windows); empty = Playwright's Chromium
 */
const channel = process.env.VLUX_E2E_CHANNEL || undefined;

export default defineConfig({
  testDir: "e2e",
  // One register, one session at a time: the projects run one after the other.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 15_000 },
  reporter: [
    ["list"],
    ["json", { outputFile: "e2e-results/results.json" }],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],
  outputDir: "test-results",
  use: {
    baseURL: process.env.VLUX_E2E_BASE_URL || "http://127.0.0.1:8069",
    locale: "es-MX",
    timezoneId: "America/Mexico_City",
    serviceWorkers: "allow",
    actionTimeout: 30_000,
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...(channel ? { channel } : {}),
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
    { name: "mobile", use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 } },
  ],
});
