import { expect, test } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common, fill } from '../support/catalog';
import { FIRST_RENDER_TIMEOUT_MS } from '../support/timeouts';

// CSV import of students (M8-05b E4), on the demo Owner's shared session:
// 0 logins, 0 redemptions, 0 registrations (LOGIN_ATTEMPTS_PER_RUN stays
// 50/50, registration.ip 10/10). From the nav, the Owner picks "Students",
// chooses a 2-row CSV built in memory (no file is stored anywhere), checks
// the preview, commits it, waits for the worker to finish (the page polls
// every 3 s) and opens one of the created students. The names carry a
// unique per-test suffix, so reruns and parallel specs never collide.
// Vitest covers tutors, the refusals, discard and the conflict.

const TEST_TIMEOUT_MS = 120_000;
const COMMIT_TIMEOUT_MS = 60_000;
const labels = common.imports;

test('the Owner imports two students from a CSV and opens one', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  await recordCspViolations(page);
  const sfx = `e2e${testInfo.workerIndex}${Date.now().toString(36)}`;
  const fileName = `e2e-import-${sfx}.csv`;
  const familyNames = `Importada ${sfx}`;
  const csv = [
    'given_names,family_names,birthdate,contact_name,contact_relationship,contact_phone',
    `Ana,${familyNames},2014-03-15,Contacto E2E,Madre,55 0000 0001`,
    `Luis,${familyNames},15/04/2013,Contacto E2E,Padre,55 0000 0002`,
  ].join('\r\n');
  const main = page.getByRole('main');

  // 1. From the nav to the imports page.
  await page.goto('/');
  await page
    .getByRole('banner')
    .getByRole('link', { name: common.nav.imports, exact: true })
    .click({ timeout: FIRST_RENDER_TIMEOUT_MS });
  await expect(page).toHaveURL('/imports');

  // 2. Students, the CSV, "Check file": the report opens.
  await main
    .getByLabel(labels.form.kind, { exact: true })
    .selectOption('students');
  await main.getByLabel(labels.form.file, { exact: true }).setInputFiles({
    name: fileName,
    mimeType: 'text/csv',
    buffer: Buffer.from(csv, 'utf8'),
  });
  await main
    .getByRole('button', { name: labels.form.submit, exact: true })
    .click();
  await expect(page).toHaveURL(/\/imports\/[0-9a-f-]{36}$/);
  await expect(
    main.getByRole('heading', { level: 1, name: fileName }),
  ).toBeVisible();
  const rows = main.locator('tbody > tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText(labels.rowStatus.ready);
  await expect(rows.nth(1)).toContainText(labels.rowStatus.ready);
  await expect(rows.nth(0)).toContainText(familyNames);

  // 3. "Import 2 rows", confirmed; the page follows the commit.
  await main
    .getByRole('button', {
      name: fill(labels.detail.commit_other, { count: 2 }),
      exact: true,
    })
    .click();
  const confirm = page.getByRole('alertdialog', {
    name: fill(labels.detail.commitTitle_other, { count: 2 }),
  });
  await confirm
    .getByRole('button', { name: labels.detail.commitConfirm, exact: true })
    .click();
  await expect(main.locator('[data-slot="import-status-badge"]')).toHaveText(
    labels.status.committed,
    { timeout: COMMIT_TIMEOUT_MS },
  );
  await expect(main.locator('p[aria-live="polite"]')).toHaveText(
    fill(labels.detail.doneAnnouncement, { created: 2, skipped: 0, failed: 0 }),
  );
  await expect(rows.nth(0)).toContainText(labels.rowStatus.created);

  // 4. The created student of line 2 opens.
  await main
    .getByRole('link', {
      name: `${labels.rows.openStudent} ${fill(labels.rows.lineNumber, { line: 2 })}`,
      exact: true,
    })
    .click();
  await expect(page).toHaveURL(/\/students\/[0-9a-f-]{36}$/);
  await expect(
    main.getByRole('heading', { level: 1, name: `Ana ${familyNames}` }),
  ).toBeVisible({ timeout: FIRST_RENDER_TIMEOUT_MS });

  expect(await cspViolations(page)).toEqual([]);
});
