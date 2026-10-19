import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { SIGNED_OUT_STATE } from '../support/auth';

// WCAG 2.0, 2.1 and 2.2 level A and AA (the A7 tags in
// _docs/testing-guidelines.md). No rule is disabled; a per-test exclusion
// needs a reason comment here and a line in "E2E axe exceptions".
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

const THEMES = ['light', 'dark'] as const;

// Home and 404 are behind the guard: scanned signed in (the shared state).
// /login is scanned signed out.
const ROUTES = [
  { name: 'home', path: '/', signedIn: true },
  { name: '404', path: '/e2e-route-that-does-not-exist', signedIn: true },
  { name: 'login', path: '/login', signedIn: false },
];

// Under machine load, the first pages of a cold dev server (and the session
// request before a guarded page renders) can take longer than the default
// waits: the first heading gets a longer wait, inside a longer test timeout.
const FIRST_RENDER_TIMEOUT_MS = 60_000;
const TEST_TIMEOUT_MS = 90_000;

/** Every violation as `rule-id (impact): selector, selector`. */
async function scan(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (violation) =>
      `${violation.id} (${violation.impact ?? 'unknown'}): ${violation.nodes
        .map((node) => node.target.join(' '))
        .join(', ')}`,
  );
}

for (const theme of THEMES) {
  for (const route of ROUTES) {
    test.describe(() => {
      test.describe.configure({ timeout: TEST_TIMEOUT_MS });
      if (!route.signedIn) {
        test.use({ storageState: SIGNED_OUT_STATE });
      }
      test(`${route.name} has no axe violations in ${theme}`, async ({
        page,
      }) => {
        // The stored theme choice, applied before the app's first render.
        await page.addInitScript((choice) => {
          window.localStorage.setItem('classora.theme', choice);
        }, theme);

        await page.goto(route.path);
        await expect(page).toHaveURL(route.path);
        await expect(page.getByRole('heading', { level: 1 })).toBeVisible({
          timeout: FIRST_RENDER_TIMEOUT_MS,
        });
        const html = page.locator('html');
        if (theme === 'dark') {
          await expect(html).toHaveClass(/\bdark\b/);
        } else {
          await expect(html).not.toHaveClass(/\bdark\b/);
        }

        const violations = await scan(page);
        expect(violations, violations.join('\n')).toEqual([]);
      });
    });
  }
}
