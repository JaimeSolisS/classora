import { defineConfig, devices } from '@playwright/test';

// Cross-application tests against the isolated `classora-e2e` Compose stack.
// `make e2e` (e2e/scripts/run.sh) starts, migrates and seeds that stack, sets
// E2E_BASE_URL and tears it down again; there is no webServer block here.
// Rules: _docs/testing-guidelines.md (E2E authoring rules).

/** Web port of the E2E stack (see E2E_WEB_PORT in e2e/scripts/run.sh). */
const DEFAULT_BASE_URL = 'http://127.0.0.1:15173';

export default defineConfig({
  testDir: 'tests',
  // Loads the app once before the tests (warms the Vite dev server).
  globalSetup: './global-setup.ts',
  outputDir: 'test-results',
  // Tests are independent of each other and of their order.
  fullyParallel: true,
  // A test that fails once is a failure; `test.only` fails the run in CI.
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: [
    ['list'],
    ['html', { open: 'never', outputFolder: 'playwright-report' }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? DEFAULT_BASE_URL,
    // Fixed time zone (the center's default, ADR-0004) and language. M1-15
    // adds one project per UI language.
    timezoneId: 'America/Mexico_City',
    locale: 'es-MX',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], headless: true },
    },
  ],
});
