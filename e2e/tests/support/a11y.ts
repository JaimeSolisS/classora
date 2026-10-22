import { expect, type Page } from '@playwright/test';

// Switching the colour scheme on a page that is already rendered (#241).
// The app sets `html.dark` from a `matchMedia` listener, and shell controls
// then animate their colours (`transition-all`, `transition-colors`, ...).
// An axe scan sampled mid-transition reports `color-contrast` on colours no
// user sees at rest. So a spec that switches the scheme on a rendered page
// and then runs axe goes through `switchColorScheme`: it emulates the
// scheme, waits for the `html` class, then waits for every running CSS
// transition to finish. It waits rather than disabling transitions (no
// `reducedMotion`, no injected CSS), so the page stays as users see it.
// Infinite CSS animations (the spinner) are not transitions and are ignored.
// A spec that sets the scheme before load (`test.use({ colorScheme })`) or
// reloads after `emulateMedia` does not need it.

export async function switchColorScheme(
  page: Page,
  scheme: 'light' | 'dark',
): Promise<void> {
  await page.emulateMedia({ colorScheme: scheme });
  const html = page.locator('html');
  if (scheme === 'dark') {
    await expect(html).toHaveClass(/\bdark\b/);
  } else {
    await expect(html).not.toHaveClass(/\bdark\b/);
  }
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a instanceof CSSTransition)
        .map((a) => a.finished.catch(() => undefined)),
    ),
  );
}
