import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { api, csrfTokenOf } from '../support/api';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';

// The staff dashboard on the home page (M8-10 E8), on the demo Owner's
// shared session: 0 logins, 0 redemptions, 0 registrations
// (LOGIN_ATTEMPTS_PER_RUN stays 50/50, registration.ip 10/10). It reorders
// with dnd-kit's keyboard sensor and with the "Bajar" button, hides a
// widget, saves, and checks the layout after a reload. The Owner's layout
// is reset before and after (`finally`), so the other specs see the
// default. Role defaults are covered by the API integration tests and
// Vitest; no figure is asserted.

const dashboard = common.dashboard;
const dialogText = dashboard.dialog;
const kpis = common.reports.kpis;
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const LAYOUT = '/api/v1/reports/dashboard/layout';

const TITLES = {
  revenue_trend: kpis.revenueTrend.title,
  balances: kpis.balances.title,
  course_profitability: kpis.courseProfitability.title,
  room_utilization: kpis.rooms.title,
  student_retention: kpis.retention.title,
  tutor_performance: kpis.tutors.title,
} as const;
type Code = keyof typeof TITLES;
const DEFAULT: Code[] = [
  'revenue_trend',
  'balances',
  'course_profitability',
  'room_utilization',
  'student_retention',
  'tutor_performance',
];

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

/** Back to the role default (`DELETE`, 204 with or without a row). */
async function resetLayout(page: Page): Promise<void> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const reset = await api(page, LAYOUT, {
    method: 'DELETE',
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(reset.status).toBe(204);
}

async function expectWidgets(page: Page, codes: Code[]): Promise<void> {
  const main = page.getByRole('main');
  await expect(
    main.getByRole('heading', { level: 1, name: dashboard.title }),
  ).toBeVisible();
  // The widgets' own headings (HF-06: the "Hoy" panel above has its own).
  await expect(
    main.locator('[data-dashboard-widgets]').getByRole('heading', { level: 2 }),
  ).toHaveText(codes.map((code) => TITLES[code]));
  // Every widget has loaded (no skeleton, no error).
  await expect(main.locator('[data-kpi-section]')).toHaveCount(codes.length);
  await expect(
    main.locator('[data-kpi-section]:not([data-kpi-status="ready"])'),
  ).toHaveCount(0);
}

test('the Owner customizes the home dashboard and it persists', async ({
  page,
}) => {
  await recordCspViolations(page);
  await page.goto('/');
  await resetLayout(page);

  try {
    // 1. The home page is the dashboard with the six widgets, in order,
    // under the fixed "Hoy" panel (HF-06 D1), which the axe scan covers.
    await page.reload();
    await expectWidgets(page, DEFAULT);
    const today = page.locator('[data-home-today]');
    await expect(
      today.getByRole('heading', { level: 2, name: common.home.today.title }),
    ).toBeVisible();
    await expect(
      today.getByRole('heading', {
        level: 3,
        name: common.home.pending.title,
        exact: true,
      }),
    ).toBeVisible();
    await expect(today.locator('[aria-busy="true"]')).toHaveCount(0);
    expect(await axeViolations(page)).toEqual([]);

    // 2. Customize: dnd-kit's keyboard sensor moves "rooms" up one.
    await page
      .getByRole('main')
      .getByRole('button', { name: dashboard.customize, exact: true })
      .click();
    const dialog = page.getByRole('dialog', { name: dialogText.title });
    await expect(dialog).toBeVisible();
    const rows = dialog.locator('[data-dashboard-row]');
    const order = () =>
      rows.evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute('data-dashboard-row')),
      );
    await expect.poll(order).toEqual(DEFAULT);
    expect(await axeViolations(page)).toEqual([]);

    const handle = dialog.getByRole('button', {
      name: fill(dialogText.reorder, { title: TITLES.room_utilization }),
    });
    // dnd-kit's live region (localized) says when each step has landed:
    // drop only once it reports the new position (#127 QA D2).
    const liveRegion = dialog.locator('[id^="DndLiveRegion"]');
    const title = TITLES.room_utilization;
    await handle.focus();
    await page.keyboard.press('Space');
    await expect(handle).toHaveAttribute('aria-pressed', 'true');
    await expect(liveRegion).toHaveText(
      fill(dialogText.announcements.start, { title, position: 4, total: 6 }),
    );
    await page.keyboard.press('ArrowUp');
    await expect(liveRegion).toHaveText(
      fill(dialogText.announcements.over, { title, position: 3, total: 6 }),
    );
    await page.keyboard.press('Space');
    await expect(handle).not.toHaveAttribute('aria-pressed', 'true');
    await expect
      .poll(order)
      .toEqual([
        'revenue_trend',
        'balances',
        'room_utilization',
        'course_profitability',
        'student_retention',
        'tutor_performance',
      ]);
    // The dialog stays open after the drop's keystrokes.
    await expect(dialog).toBeVisible();

    // 3. "Bajar" on K1, hide the balances, then save.
    await dialog
      .getByRole('button', {
        name: fill(dialogText.moveDown, { title: TITLES.revenue_trend }),
      })
      .click();
    const saved: Code[] = [
      'balances',
      'revenue_trend',
      'room_utilization',
      'course_profitability',
      'student_retention',
      'tutor_performance',
    ];
    await expect.poll(order).toEqual(saved);
    await dialog
      .getByRole('checkbox', {
        name: fill(dialogText.show, { title: TITLES.balances }),
      })
      .click();
    await dialog
      .getByRole('button', { name: dialogText.save, exact: true })
      .click();
    await expect(dialog).toBeHidden();
    const shown = saved.filter((code) => code !== 'balances');
    await expectWidgets(page, shown);

    // 4. A reload keeps the order and the hidden widget.
    await page.reload();
    await expectWidgets(page, shown);
    expect(await axeViolations(page)).toEqual([]);
    expect(await cspViolations(page)).toEqual([]);
  } finally {
    // 5. Other specs see the Owner's default home.
    await resetLayout(page);
  }
});
