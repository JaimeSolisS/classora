import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Locator, type Page } from '@playwright/test';
import { cspViolations, recordCspViolations } from '../support/auth';
import { common } from '../support/catalog';
import {
  centerInstant,
  createClassSessionViaApi,
  firstWeekdayAhead,
  readClassSessionViaApi,
  sundayOfWeek,
} from '../support/scheduling';
import { createStudentViaApi } from '../support/students';
import { createSubjectViaApi } from '../support/tutors';

// Drag-and-drop rescheduling (#58 G9). The demo Owner (the shared state;
// schedule.manage and schedule.override) creates a student, a subject and
// two unassigned sessions through the API on the first weekday D at least
// 14 days ahead: A 10:00–11:00 and B 14:00–15:00. The week of D, filtered by
// the new student (other specs' sessions stay out), then:
// 1. A is dragged to 11:00 → "Moved…", and the API has A at 11:00 with its
//    version + 1;
// 2. B is dragged onto 11:00 → "Move not saved" lists student_overlap as
//    "Not allowed" with no override button (axe with the dialog open);
//    after closing, B is back at 14:00 and the API still has 14:00;
// 3. A is dragged to the Sunday of that week at 10:00 → outside_operating_
//    hours "Can be overridden" → "Override and move" with a reason → A is
//    on Sunday with the "Overridden" marker;
// 4. keyboard: B → Enter → event dialog → Reschedule → 16:00–17:00 → Save.
//
// Drags use page.mouse with intermediate steps on stable attributes of the
// rendered FullCalendar v7 DOM: the event's `data-session-id`, the day
// lanes' `data-date` and the slot rows' `data-time`. No fixed sleeps.
//
// Budgets (#27, ADR-0008; see support/auth.ts): no login, no redemption and
// no registration (four API creates with the shared Owner session). It never
// changes the zone, the opening hours, the scheduling policy or closures; it
// relies on Sunday being closed in the demo hours at or after 09:00
// (_docs/testing-guidelines.md, E2E data rules).

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const TEST_TIMEOUT_MS = 90_000;
const REASON = 'La familia pidió el domingo; el centro abre por esta clase.';

// Chromium logs every 4xx resource as a console error: the expected 409s.
const EXPECTED_RESOURCE_ERROR =
  /Failed to load resource: the server responded with a status of 409/;

test.use({ viewport: { width: 1280, height: 900 } });

async function axeViolations(page: Page): Promise<string[]> {
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  return results.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`,
  );
}

/** The text before the first interpolation of a catalog message. */
function prefix(message: string): string {
  return message.split('{{')[0]!.trim();
}

/** The top and left of the dragged copy of a session (FullCalendar's
 * mirror: the second element with its id), or null before the drag starts. */
async function mirrorBox(page: Page, sessionId: string) {
  return page.evaluate((id) => {
    const found = document.querySelectorAll(`[data-session-id="${id}"]`);
    if (found.length < 2) {
      return null;
    }
    const box = found[found.length - 1]!.getBoundingClientRect();
    return { top: box.top, left: box.left, right: box.right };
  }, sessionId);
}

/**
 * Drags the event to `toDate` at `toTime` of the week's time grid. The
 * pointer goes to the target day's lane, then moves in 3-pixel steps until
 * FullCalendar's dragged copy starts exactly at the target slot (its
 * `data-time` row), so the drop doesn't depend on where the event was
 * grabbed or on the snap rounding.
 */
async function dragEvent(
  page: Page,
  calendar: Locator,
  sessionId: string,
  to: { date: string; time: string },
) {
  const event = calendar.locator(`[data-session-id="${sessionId}"]`);
  // Centred, so the drag never reaches an edge (FullCalendar would scroll
  // the page while dragging).
  await event.evaluate((element) =>
    element.scrollIntoView({ block: 'center', inline: 'nearest' }),
  );
  const lane = calendar.locator(`[role="cell"][data-date="${to.date}"]`).last();
  const slot = calendar.locator(`[data-time="${to.time}:00"]`).last();
  await expect(lane).toBeVisible();
  const laneBox = (await lane.boundingBox())!;
  const slotBox = (await slot.boundingBox())!;
  const box = (await event.boundingBox())!;
  let x = box.x + box.width / 2;
  let y = box.y + 6;
  await page.mouse.move(x, y);
  await page.mouse.down();
  // Past FullCalendar's minimum drag distance, then to the target day.
  await page.mouse.move(x, y + 6, { steps: 3 });
  y += 6;
  x = laneBox.x + laneBox.width / 2;
  await page.mouse.move(x, y, { steps: 10 });
  for (let step = 0; step < 300; step += 1) {
    const mirror = await mirrorBox(page, sessionId);
    if (mirror && Math.abs(mirror.top - slotBox.y) < 1) {
      expect(mirror.left).toBeGreaterThanOrEqual(laneBox.x - 1);
      expect(mirror.right).toBeLessThanOrEqual(laneBox.x + laneBox.width + 1);
      break;
    }
    const below = !mirror || mirror.top < slotBox.y;
    y += below ? 3 : -3;
    await page.mouse.move(x, y);
  }
  const placed = await mirrorBox(page, sessionId);
  expect(Math.abs(placed!.top - slotBox.y)).toBeLessThan(1);
  await page.mouse.up();
}

test('an Owner reschedules by dragging, with an override and with the keyboard', async ({
  page,
}, testInfo) => {
  test.setTimeout(TEST_TIMEOUT_MS);
  const suffix = `${testInfo.workerIndex}${Date.now().toString(36)}`;
  const familyNames = `Mover ${suffix}`;
  const subjectName = `Materia Mover ${suffix}`;
  await recordCspViolations(page);
  const consoleErrors: string[] = [];
  page.on('pageerror', (error) => consoleErrors.push(error.message));
  page.on('console', (message) => {
    if (
      message.type() === 'error' &&
      !EXPECTED_RESOURCE_ERROR.test(message.text())
    ) {
      consoleErrors.push(message.text());
    }
  });
  const main = page.getByRole('main');
  const calendar = main.getByRole('region', {
    name: common.scheduling.calendar.regionLabel,
  });
  const loaded = () => expect(calendar).toHaveAttribute('aria-busy', 'false');
  const move = common.scheduling.calendar.move;

  // The app origin first, so the API calls carry the session.
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const studentId = await createStudentViaApi(page, {
    givenNames: 'Lía',
    familyNames,
  });
  const subjectId = await createSubjectViaApi(page, 'Primaria', subjectName);
  const day = firstWeekdayAhead();
  const sunday = sundayOfWeek(day);
  const sessionA = await createClassSessionViaApi(page, {
    studentId,
    subjectId,
    date: day,
    start: '10:00',
    end: '11:00',
  });
  const sessionB = await createClassSessionViaApi(page, {
    studentId,
    subjectId,
    date: day,
    start: '14:00',
    end: '15:00',
  });
  const eventA = calendar.locator(`[data-session-id="${sessionA}"]`);
  const eventB = calendar.locator(`[data-session-id="${sessionB}"]`);

  await page.goto(`/schedule?view=week&date=${day}&student=${studentId}`);
  await loaded();
  await expect(eventA).toHaveCount(1);
  await expect(eventB).toHaveCount(1);

  // 1. Drag A to 11:00 on D: moved, announced, saved with version + 1.
  await dragEvent(page, calendar, sessionA, { date: day, time: '11:00' });
  await expect(
    main.getByRole('status').filter({ hasText: prefix(move.moved) }),
  ).toBeVisible();
  await expect(eventA).toContainText('11:00–12:00');
  await expect
    .poll(async () => {
      const stored = await readClassSessionViaApi(page, sessionA);
      return [Date.parse(stored.starts_at), stored.version];
    })
    .toEqual([Date.parse(centerInstant(day, '11:00')), 2]);
  await loaded();

  // 2. Drag B onto 11:00: student_overlap, "Not allowed", no override; B
  // stays at 14:00.
  await dragEvent(page, calendar, sessionB, { date: day, time: '11:00' });
  const notSaved = page.getByRole('dialog', { name: move.notSaved });
  await expect(notSaved).toBeVisible();
  await expect(notSaved).toContainText(common.scheduling.severity.hard);
  await expect(notSaved).toContainText(
    common.scheduling.conflicts.student_overlap,
  );
  await expect(
    notSaved.getByRole('button', { name: move.overrideAction }),
  ).toHaveCount(0);
  expect(await axeViolations(page)).toEqual([]);
  await notSaved.getByRole('button', { name: move.close, exact: true }).click();
  await expect(notSaved).toBeHidden();
  await expect(eventB).toContainText('14:00–15:00');
  const storedB = await readClassSessionViaApi(page, sessionB);
  expect(Date.parse(storedB.starts_at)).toBe(
    Date.parse(centerInstant(day, '14:00')),
  );

  // 3. Drag A to Sunday 10:00: outside the opening hours, overridden with a
  // reason; A is on Sunday with the "Overridden" marker.
  await dragEvent(page, calendar, sessionA, { date: sunday, time: '10:00' });
  await expect(notSaved).toBeVisible();
  await expect(notSaved).toContainText(common.scheduling.severity.soft);
  await notSaved.getByRole('button', { name: move.overrideAction }).click();
  const overrideDialog = page.getByRole('dialog', {
    name: move.overrideAction,
  });
  await expect(overrideDialog).toBeVisible();
  await overrideDialog
    .getByLabel(common.scheduling.override.reason)
    .fill(REASON);
  await overrideDialog
    .getByRole('button', { name: move.overrideAction })
    .click();
  await expect(overrideDialog).toBeHidden();
  await expect(notSaved).toBeHidden();
  await expect(
    main.getByRole('status').filter({ hasText: move.movedWithOverride }),
  ).toBeVisible();
  await loaded();
  await expect(eventA).toContainText(
    common.scheduling.markers['overridden']!.label,
  );
  await expect
    .poll(async () =>
      Date.parse((await readClassSessionViaApi(page, sessionA)).starts_at),
    )
    .toBe(Date.parse(centerInstant(sunday, '10:00')));

  // 4. Keyboard: B → Enter → Reschedule → 16:00–17:00 → Save.
  await eventB.focus();
  await page.keyboard.press('Enter');
  const details = page.getByRole('dialog', { name: subjectName });
  await expect(details).toBeVisible();
  await details
    .getByRole('button', { name: common.scheduling.calendar.reschedule.action })
    .press('Enter');
  const reschedule = page.getByRole('dialog', {
    name: common.scheduling.calendar.reschedule.title,
  });
  await expect(reschedule).toBeVisible();
  await reschedule
    .getByLabel(common.scheduling.fields.start, { exact: true })
    .fill('16:00');
  await reschedule
    .getByLabel(common.scheduling.fields.end, { exact: true })
    .fill('17:00');
  await reschedule
    .getByRole('button', { name: common.scheduling.calendar.reschedule.submit })
    .press('Enter');
  await expect(reschedule).toBeHidden();
  await expect(eventB).toContainText('16:00–17:00');
  await expect(eventB).toBeFocused();
  await expect
    .poll(async () =>
      Date.parse((await readClassSessionViaApi(page, sessionB)).starts_at),
    )
    .toBe(Date.parse(centerInstant(day, '16:00')));

  expect(await cspViolations(page)).toEqual([]);
  expect(consoleErrors).toEqual([]);
});
