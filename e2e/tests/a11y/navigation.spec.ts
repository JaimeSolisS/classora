import { expect, test, type Page } from '@playwright/test';
import { axeViolations } from '../support/a11y';
import { SIGNED_OUT_STATE } from '../support/auth';
import { common } from '../support/catalog';
import { FIRST_RENDER_TIMEOUT_MS } from '../support/timeouts';

// #546 H7: the collapsible sidebar (lg and up) and the drawer (below lg), on
// the shared demo Owner state. Budgets (see support/auth.ts): 0 logins,
// 0 registrations, 0 one-time-link redemptions. #396 F1/F2: no page scrolls
// sideways.

// F1: the Owner's pages checked at 1024 and 1280 px.
const PAGES = [
  '/',
  '/users',
  '/schedule',
  '/billing/accounts',
  '/reports/kpis',
  '/audit',
];

const SIDEBAR_KEY = 'classora.sidebar';

const nav = (page: Page) =>
  page.getByRole('banner').getByRole('navigation', { name: common.nav.label });
const menuToggle = (page: Page) =>
  page.getByRole('button', { name: common.nav.toggle, exact: true });

async function open(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await expect(
    page.getByRole('main').getByRole('heading', { level: 1 }).first(),
  ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
}

async function expectNoSidewaysScroll(page: Page, where: string) {
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(scrollWidth, `${where} scrolls sideways`).toBeLessThanOrEqual(
    clientWidth,
  );
}

async function expectRailWidth(page: Page, collapsed: boolean) {
  await expect(async () => {
    const box = await nav(page).boundingBox();
    expect(box).not.toBeNull();
    if (collapsed) {
      expect(box!.width).toBeLessThanOrEqual(64);
    } else {
      expect(box!.width).toBeGreaterThan(200);
    }
  }).toPass();
}

for (const [width, height] of [
  [1280, 720],
  [1024, 768],
] as const) {
  test(`sidebar at ${width} px: left of main, sections, no sideways scroll expanded and collapsed`, async ({
    page,
  }) => {
    test.setTimeout(180_000);
    await page.setViewportSize({ width, height });
    await open(page, '/');

    await expect(nav(page)).toBeVisible();
    await expect(menuToggle(page)).toBeHidden();
    for (const section of [
      'administration',
      'finance',
      'people',
      'academics',
    ] as const) {
      await expect(
        nav(page).getByRole('list', { name: common.nav.sections[section] }),
      ).toBeVisible();
    }
    const navBox = await nav(page).boundingBox();
    const mainBox = await page.getByRole('main').boundingBox();
    expect(navBox!.x + navBox!.width).toBeLessThanOrEqual(mainBox!.x);

    for (const path of PAGES) {
      await open(page, path);
      await expectNoSidewaysScroll(page, `${path} expanded at ${width}`);
    }

    await nav(page)
      .getByRole('button', { name: common.nav.collapse, exact: true })
      .click();
    await expectRailWidth(page, true);
    for (const path of PAGES) {
      await open(page, path);
      await expectRailWidth(page, true);
      await expectNoSidewaysScroll(page, `${path} collapsed at ${width}`);
    }
  });
}

test('collapsed survives a reload, shows tooltips on keyboard focus and passes axe', async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.emulateMedia({ colorScheme: 'dark' });
  await open(page, '/');

  await nav(page)
    .getByRole('button', { name: common.nav.collapse, exact: true })
    .click();
  const expand = nav(page).getByRole('button', {
    name: common.nav.expand,
    exact: true,
  });
  await expect(expand).toBeFocused();
  await expectRailWidth(page, true);

  await page.reload();
  await expect(expand).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
  await expectRailWidth(page, true);
  expect(
    await page.evaluate((key) => window.localStorage.getItem(key), SIDEBAR_KEY),
  ).toBe('collapsed');

  // The keyboard: from the toggle, Tab reaches the first link, whose label
  // shows in a tooltip; the link keeps its name.
  await expand.focus();
  await page.keyboard.press('Tab');
  const home = nav(page).getByRole('link', {
    name: common.nav.home,
    exact: true,
  });
  await expect(home).toBeFocused();
  await expect(page.getByRole('tooltip')).toHaveText(common.nav.home);
  await page.keyboard.press('Escape');
  await expect(page.getByRole('tooltip')).toHaveCount(0);

  await expect(page.locator('html')).toHaveClass(/\bdark\b/);
  const collapsedDark = await axeViolations(page);
  expect(collapsedDark, collapsedDark.join('\n')).toEqual([]);

  await expand.click();
  await expectRailWidth(page, false);
  await page.emulateMedia({ colorScheme: 'light' });
  await expect(page.locator('html')).not.toHaveClass(/\bdark\b/);
  const expandedLight = await axeViolations(page);
  expect(expandedLight, expandedLight.join('\n')).toEqual([]);
});

for (const width of [390, 320]) {
  test(`drawer at ${width} px: focus, Escape, a link closes it, no sideways scroll`, async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 800 });
    await open(page, '/');
    const toggle = menuToggle(page);
    const main = page.locator('main#main');
    await expect(nav(page)).toBeHidden();
    await expectNoSidewaysScroll(page, `/ at ${width}, drawer closed`);

    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(nav(page)).toBeVisible();
    await expect(
      nav(page).getByRole('link', { name: common.nav.home, exact: true }),
    ).toBeFocused();
    await expect(main).toHaveAttribute('inert', '');
    await expect(
      nav(page).getByRole('list', {
        name: common.nav.sections.administration,
      }),
    ).toBeVisible();
    await expectNoSidewaysScroll(page, `/ at ${width}, drawer open`);
    const drawerOpen = await axeViolations(page);
    expect(drawerOpen, drawerOpen.join('\n')).toEqual([]);

    await page.keyboard.press('Escape');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(toggle).toBeFocused();
    await expect(main).not.toHaveAttribute('inert');

    await toggle.click();
    await nav(page)
      .getByRole('link', { name: common.nav.users, exact: true })
      .click();
    await expect(page).toHaveURL(/\/users$/);
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await expect(nav(page)).toBeHidden();
    await expect(main).not.toHaveAttribute('inert');
    await expectNoSidewaysScroll(page, `/users at ${width}`);
  });
}

test('widening to 1280 px with the drawer open closes it', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await open(page, '/');
  const main = page.locator('main#main');
  await menuToggle(page).click();
  await expect(main).toHaveAttribute('inert', '');

  await page.setViewportSize({ width: 1280, height: 720 });
  await expect(main).not.toHaveAttribute('inert');
  await expect(nav(page)).toBeVisible();
  await expect(menuToggle(page)).toBeHidden();
  await expectNoSidewaysScroll(page, '/ widened to 1280');
});

test.describe('signed out', () => {
  test.use({ storageState: SIGNED_OUT_STATE });

  test('/login has no sidebar and no sideways scroll at 320 px', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 320, height: 640 });
    await open(page, '/login');
    await expect(nav(page)).toHaveCount(0);
    await expectNoSidewaysScroll(page, '/login at 320');
  });
});
