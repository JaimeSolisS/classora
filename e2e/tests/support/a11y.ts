import AxeBuilder from '@axe-core/playwright';
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

// Every axe scan in the E2E specs goes through this file (#642).
// WCAG 2.0, 2.1 and 2.2 level A and AA.
export const WCAG_TAGS = [
  'wcag2a',
  'wcag2aa',
  'wcag21a',
  'wcag21aa',
  'wcag22aa',
];

export interface AxeScanOptions {
  /** CSS selector(s) the scan is limited to. */
  include?: string | string[];
  /** CSS selector(s) left out of the scan. */
  exclude?: string | string[];
  /** Shown with the violations when the assertion fails. */
  label?: string;
}

// Scan the resting state (#369, #642). A `Button` fades its opacity
// (`transition-all`, `disabled:opacity-50`) when it is enabled again, and a
// dialog animates in. axe sampled mid-transition reports `color-contrast` on
// a blended colour no user sees at rest. So before axe runs, wait for every
// finite CSS transition and animation to finish, a few rounds in case one
// ending starts another. Infinite animations (the spinner) are ignored. It
// waits rather than disabling transitions (no `reducedMotion`), so the page
// stays as users see it. Returns the number of animations awaited.
export async function settleAnimations(page: Page): Promise<number> {
  return page.evaluate(async () => {
    let awaited = 0;
    for (let round = 0; round < 5; round += 1) {
      const running = document
        .getAnimations()
        .filter(
          (a) =>
            a.playState === 'running' &&
            a.effect?.getComputedTiming().endTime !== Infinity,
        );
      if (running.length === 0) break;
      awaited += running.length;
      await Promise.allSettled(running.map((a) => a.finished));
    }
    return awaited;
  });
}

/** axe violations on the settled page, as `id (impact): targets`. */
export async function axeViolations(
  page: Page,
  { include, exclude }: Omit<AxeScanOptions, 'label'> = {},
): Promise<string[]> {
  await settleAnimations(page);
  let builder = new AxeBuilder({ page }).withTags(WCAG_TAGS);
  for (const selector of [include ?? []].flat()) {
    builder = builder.include(selector);
  }
  for (const selector of [exclude ?? []].flat()) {
    builder = builder.exclude(selector);
  }
  const results = await builder.analyze();
  return results.violations.map(
    (violation) =>
      `${violation.id} (${violation.impact ?? 'unknown'}): ${violation.nodes
        .map((node) => node.target.join(' '))
        .join(', ')}`,
  );
}

/** Asserts that the settled page has no axe violations. */
export async function expectNoAxeViolations(
  page: Page,
  options: AxeScanOptions = {},
): Promise<void> {
  const violations = await axeViolations(page, options);
  expect(
    violations,
    [options.label, ...violations].filter(Boolean).join('\n'),
  ).toEqual([]);
}
