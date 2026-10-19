import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { chromium, type FullConfig } from '@playwright/test';
import { OWNER_STATE_PATH } from './tests/support/auth';
import { demoOwner } from './tests/support/credentials';

// A freshly started stack serves the web app from the Vite dev server, which
// compiles the module graph (and pre-bundles dependencies) on the first page
// load. Load the app once here, with a generous timeout, so the tests start
// against a warm server and keep the default assertion timeouts.
//
// Then sign the demo Owner in once, through the API from a page on the web
// origin (as the #23/#24 specs do), and save the browser state for the specs
// that need a signed-in page (#28 H1). This is one login of the run's budget.
const WARM_UP_TIMEOUT_MS = 120_000;

export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0]?.use.baseURL;
  if (!baseURL) {
    throw new Error('global-setup: no baseURL configured');
  }
  const browser = await chromium.launch();
  try {
    const context = await browser.newContext({ baseURL });
    const page = await context.newPage();
    await page.goto('/', { timeout: WARM_UP_TIMEOUT_MS });
    // Signed out, the app shows the login page (its h1).
    await page
      .getByRole('heading', { level: 1 })
      .waitFor({ state: 'visible', timeout: WARM_UP_TIMEOUT_MS });

    const status = await page.evaluate(async (credentials) => {
      const response = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credentials),
      });
      return response.status;
    }, demoOwner);
    if (status !== 200) {
      throw new Error(`global-setup: demo Owner login returned ${status}`);
    }
    mkdirSync(dirname(OWNER_STATE_PATH), { recursive: true });
    await context.storageState({ path: OWNER_STATE_PATH });
  } finally {
    await browser.close();
  }
}
