import { chromium, type FullConfig } from '@playwright/test';

// A freshly started stack serves the web app from the Vite dev server, which
// compiles the module graph (and pre-bundles dependencies) on the first page
// load. Load the app once here, with a generous timeout, so the tests start
// against a warm server and keep the default assertion timeouts.
const WARM_UP_TIMEOUT_MS = 120_000;

export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0]?.use.baseURL;
  if (!baseURL) {
    throw new Error('global-setup: no baseURL configured');
  }
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    await page.goto(baseURL, { timeout: WARM_UP_TIMEOUT_MS });
    await page
      .getByRole('heading', { level: 1 })
      .waitFor({ state: 'visible', timeout: WARM_UP_TIMEOUT_MS });
  } finally {
    await browser.close();
  }
}
