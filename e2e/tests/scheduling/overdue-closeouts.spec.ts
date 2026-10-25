import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';
import { FIRST_RENDER_TIMEOUT_MS } from '../support/timeouts';

// Overdue closeouts and closeout on behalf (#301b H3). `run.sh` inserts,
// through tests.factories.e2e_attendance, a started confirmed individual
// session of `demo.tutor` with one student and no attendance (the subject
// "E2E Overdue", before the "E2E Closeout" session). On the demo Owner's
// shared session:
// 1. Agenda → "Cierres pendientes (n)" → the overdue page lists "E2E
//    Overdue" (axe).
// 2. At 390×844 the page never scrolls sideways and the table's region is
//    visible (axe).
// 3. "Abrir clase" → the session page; the Closeout section reads "0 de 1".
// 4. Attendance: the student's "Corregir" → Presente with a reason.
// 5. "Cerrar clase en nombre de Demo Tutor": "1 de 1", 55 minutes, the
//    required summary fields and a reason → submit.
// 6. The section shows "Enviado en nombre de Demo Tutor por Demo Owner, …"
//    and the reason, and no "Solicitar cambios" (axe).
// 7. The approval inbox row of "E2E Overdue" says "En nombre del tutor";
//    its "Revisar" link opens the session.
// 8. The Owner approves (the Owner may review their own on-behalf
//    submission, #301 E5): "Aprobado · 55 min".
// 9. The overdue page no longer lists "E2E Overdue".
// Rows are always found by the subject, never by count or position: the
// other fixture sessions ("E2E Attendance", "E2E Closeout") may be listed
// too while offers.spec.ts runs in parallel.
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (the shared Owner session). It leaves no pending item in
// the shared inbox. It never changes the zone, the policies or the
// closures.

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 120_000;
const SUBJECT = 'E2E Overdue';
const TUTOR_NAME = 'Demo Tutor';
const OWNER_NAME = 'Demo Owner';
const MINUTES = 55;
const REASON = 'El tutor no pudo enviar el cierre.';
const overdue = common.scheduling.overdue;
const onBehalf = common.closeout.onBehalf;

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const textBefore = (template: string) => template.split('{{')[0]!;

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

test('overdue closeouts: close out on the tutor’s behalf and approve (#301b)', async ({
  page,
}) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });
  const main = page.getByRole('main');

  // 1. The Schedule page's link opens the overdue page.
  await page.goto('/schedule');
  const link = main.getByRole('link', {
    name: new RegExp(`^${escape(overdue.link)} \\(\\d+\\)$`),
  });
  await expect(link).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
  await link.click();
  await expect(page).toHaveURL(/\/schedule\/overdue-closeouts$/);
  await expect(
    main.getByRole('heading', { level: 1, name: overdue.title }),
  ).toBeVisible();
  const row = main.getByRole('row').filter({ hasText: SUBJECT });
  await expect(row).toHaveCount(1);
  expect(await axeViolations(page)).toEqual([]);

  // 2. On a phone the table scrolls in its region; the page never does.
  const desktop = page.viewportSize();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(main.getByRole('region', { name: overdue.title })).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth,
    ),
  ).toBe(true);
  expect(await axeViolations(page)).toEqual([]);
  if (desktop) {
    await page.setViewportSize(desktop);
  }

  // 3. "Abrir clase" → the session's Closeout section: nobody is marked.
  await row
    .getByRole('link', { name: new RegExp(`^${escape(overdue.open)}`) })
    .click();
  await expect(page).toHaveURL(/\/schedule\/sessions\/[0-9a-f-]+#closeout$/);
  const closeout = main.getByRole('region', {
    name: common.closeout.section.heading,
    exact: true,
  });
  await expect(closeout).toContainText(
    fill(onBehalf.attendance, { marked: 0, total: 1 }),
  );

  // 4. The Owner marks the student present through the #76 correction.
  const attendance = main.getByRole('region', {
    name: common.attendance.section.heading,
    exact: true,
  });
  await attendance
    .getByRole('button', {
      name: new RegExp(
        `^${escape(textBefore(common.attendance.section.correctFor))}`,
      ),
    })
    .click();
  const correction = page.getByRole('dialog', {
    name: common.attendance.correctDialog.title,
  });
  await correction
    .getByLabel(common.attendance.correctDialog.status, { exact: true })
    .selectOption('present');
  await correction
    .getByLabel(common.attendance.correctDialog.reason, { exact: true })
    .fill('Asistió; el tutor no registró la asistencia.');
  await correction
    .getByRole('button', {
      name: common.attendance.correctDialog.submit,
      exact: true,
    })
    .click();
  await expect(correction).toBeHidden();
  await expect(closeout).toContainText(
    fill(onBehalf.attendance, { marked: 1, total: 1 }),
  );

  // 5. Close out on the tutor's behalf.
  await closeout
    .getByRole('button', {
      name: fill(onBehalf.action, { tutor: TUTOR_NAME }),
      exact: true,
    })
    .click();
  const dialog = page.getByRole('dialog', {
    name: fill(onBehalf.action, { tutor: TUTOR_NAME }),
  });
  await expect(dialog).toContainText(
    fill(onBehalf.complete, { marked: 1, total: 1 }),
  );
  await dialog
    .getByLabel(common.closeout.actualMinutes, { exact: true })
    .fill(String(MINUTES));
  await dialog
    .getByLabel(common.closeout.fields.topics, { exact: false })
    .fill('Fracciones equivalentes');
  await dialog
    .getByLabel(common.closeout.fields.performance, { exact: false })
    .fill('Participó y resolvió los ejercicios.');
  await dialog
    .getByLabel(common.closeout.fields.next_steps, { exact: false })
    .fill('Suma de fracciones.');
  await dialog.getByLabel(onBehalf.reason, { exact: false }).fill(REASON);
  await dialog
    .getByRole('button', { name: onBehalf.submit, exact: true })
    .click();
  await expect(dialog).toBeHidden();

  // 6. The marker and the reason; no "Request changes" on it.
  // "Enviado en nombre de Demo Tutor por Demo Owner, " (then the time).
  await expect(closeout).toContainText(
    fill(onBehalf.marker.split('{{when}}')[0]!, {
      tutor: TUTOR_NAME,
      name: OWNER_NAME,
    }),
  );
  await expect(closeout).toContainText(
    fill(onBehalf.reasonLine, { reason: REASON }),
  );
  await expect(
    closeout.getByRole('button', {
      name: common.closeout.review.requestChanges,
      exact: true,
    }),
  ).toHaveCount(0);
  expect(await axeViolations(page)).toEqual([]);

  // 7. The inbox row says "On behalf" and links back to the session.
  const sessionUrl = page.url().replace(/#.*$/, '');
  await page.goto('/approvals?type=academics.session_closeout');
  const item = main.getByRole('row').filter({ hasText: SUBJECT });
  await expect(item).toContainText(common.approvals.closeout.onBehalf, {
    timeout: FIRST_RENDER_TIMEOUT_MS,
  });
  await item
    .getByRole('link', { name: common.approvals.closeout.review })
    .click();
  await expect(page).toHaveURL(`${sessionUrl}#closeout`);

  // 8. The Owner approves it.
  await closeout
    .getByRole('button', { name: common.closeout.review.approve, exact: true })
    .click();
  const approve = page.getByRole('dialog');
  await expect(approve).toContainText(
    fill(common.closeout.review.approveDialog.minutes, {
      actual: MINUTES,
      scheduled: 60,
    }),
  );
  await approve
    .getByRole('button', {
      name: common.closeout.review.approveDialog.submit,
      exact: true,
    })
    .click();
  await expect(approve).toBeHidden();
  await expect(closeout).toContainText(
    fill(common.closeout.review.approvedShort, { minutes: MINUTES }),
  );

  // 9. It is no longer overdue.
  await page.goto('/schedule/overdue-closeouts');
  await expect(
    main.getByRole('heading', { level: 1, name: overdue.title }),
  ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });
  await expect(main.locator('[aria-busy="true"]')).toHaveCount(0);
  await expect(main.getByRole('row').filter({ hasText: SUBJECT })).toHaveCount(
    0,
  );

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
