import { expect, type Locator, type Page } from '@playwright/test';
import { api, csrfTokenOf } from './api';

// One-off class sessions through the real API (#57, #58) with the page's
// own session (the shared demo Owner, schedule.manage): no login. The page
// must be on the app origin first. The center zone is the demo's
// America/Mexico_City, which has no DST, so local times carry -06:00.

export const CENTER_ZONE = 'America/Mexico_City';

/**
 * Types a time into a type-or-pick time field (HF-13, #598; the scheduling
 * forms and dialogs, the respond-by times, #600) and leaves it with Tab,
 * which commits the time and closes its list (an open list would cover the
 * next controls and match `getByLabel` through `aria-labelledby`).
 */
export async function fillTime(field: Locator, time: string): Promise<void> {
  await field.fill(time);
  await field.press('Tab');
}
const OFFSET = '-06:00';

/** Today in the center zone, plus `days`, as YYYY-MM-DD. */
function dayAhead(days: number): Date {
  const format = new Intl.DateTimeFormat('en-CA', { timeZone: CENTER_ZONE });
  const [year, month, day] = format
    .format(new Date())
    .split('-')
    .map(Number) as [number, number, number];
  return new Date(Date.UTC(year, month - 1, day + days, 12));
}

/** The first Monday-Friday at least 14 days after today (center zone). */
export function firstWeekdayAhead(): string {
  const date = dayAhead(14);
  while (date.getUTCDay() === 0 || date.getUTCDay() === 6) {
    date.setUTCDate(date.getUTCDate() + 1);
  }
  return date.toISOString().slice(0, 10);
}

/** The Sunday that ends the Monday-first week of `date` (YYYY-MM-DD). */
export function sundayOfWeek(date: string): string {
  const day = new Date(`${date}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + ((7 - day.getUTCDay()) % 7));
  return day.toISOString().slice(0, 10);
}

/** The UTC instant of a local date and time in the center zone. */
export function centerInstant(date: string, time: string): string {
  return new Date(`${date}T${time}:00${OFFSET}`).toISOString();
}

/**
 * One unassigned session (no tutor, no room) through the API, from `start`
 * to `end` (HH:MM, center zone) on `date`. Returns its id.
 */
export async function createClassSessionViaApi(
  page: Page,
  {
    studentId,
    subjectId,
    date,
    start,
    end,
  }: {
    studentId: string;
    subjectId: string;
    date: string;
    start: string;
    end: string;
  },
): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const created = await api(page, '/api/v1/class-sessions', {
    method: 'POST',
    json: {
      kind: 'individual',
      student_id: studentId,
      subject_id: subjectId,
      starts_at: `${date}T${start}:00${OFFSET}`,
      ends_at: `${date}T${end}:00${OFFSET}`,
    },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}

/** The session as the API has it now. */
export async function readClassSessionViaApi(
  page: Page,
  id: string,
): Promise<{ starts_at: string; ends_at: string; version: number }> {
  const read = await api(page, `/api/v1/class-sessions/${id}`);
  expect(read.status).toBe(200);
  return read.body as { starts_at: string; ends_at: string; version: number };
}

/** Cancels the session as the center: a makeup stays allowed (#436 D3). */
export async function cancelClassSessionViaApi(
  page: Page,
  id: string,
  reason: string,
): Promise<void> {
  const { version } = await readClassSessionViaApi(page, id);
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  const cancelled = await api(page, `/api/v1/class-sessions/${id}/cancel`, {
    method: 'POST',
    json: { version, reason },
    headers: { 'X-CSRF-Token': csrfTokenOf(me) },
  });
  expect(cancelled.status).toBe(200);
}

// --- Groups and cohorts (HF-11b, #593) ----------------------------------------

/** The ISO weekday (1 = Monday) of a YYYY-MM-DD date. */
export function isoWeekday(date: string): number {
  return new Date(`${date}T12:00:00Z`).getUTCDay() || 7;
}

/** `date` plus `days`, as YYYY-MM-DD. */
export function addDays(date: string, days: number): string {
  const day = new Date(`${date}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + days);
  return day.toISOString().slice(0, 10);
}

async function csrf(page: Page): Promise<string> {
  const me = await api(page, '/api/v1/auth/me');
  expect(me.status).toBe(200);
  return csrfTokenOf(me);
}

/**
 * A group through the API: no tutor, no room, weekly on `startsOn`'s
 * weekday at `startTime` for 60 minutes, 4 sessions, with `studentIds`
 * enrolled. Returns its id.
 */
export async function createGroupViaApi(
  page: Page,
  {
    name,
    subjectId,
    startsOn,
    startTime,
    studentIds,
  }: {
    name: string;
    subjectId: string;
    startsOn: string;
    startTime: string;
    studentIds: string[];
  },
): Promise<string> {
  const created = await api(page, '/api/v1/groups', {
    method: 'POST',
    headers: { 'X-CSRF-Token': await csrf(page) },
    json: {
      name,
      subject_id: subjectId,
      max_students: 10,
      starts_on: startsOn,
      start_time: startTime,
      duration_minutes: 60,
      recurrence: { weekdays: [isoWeekday(startsOn)], interval: 1, count: 4 },
      student_ids: studentIds,
    },
  });
  expect(created.status).toBe(201);
  return (created.body as { id: string }).id;
}

/**
 * A course template (4 sessions of 90 minutes) and a cohort of it through
 * the API: no tutor, no room, weekly on `startsOn`'s weekday at
 * `startTime`, with `studentIds` enrolled. Returns the cohort id.
 */
export async function createCohortViaApi(
  page: Page,
  {
    name,
    subjectId,
    startsOn,
    startTime,
    studentIds,
  }: {
    name: string;
    subjectId: string;
    startsOn: string;
    startTime: string;
    studentIds: string[];
  },
): Promise<string> {
  const template = await api(page, '/api/v1/course-templates', {
    method: 'POST',
    headers: { 'X-CSRF-Token': await csrf(page) },
    json: {
      name: `Plantilla ${name}`,
      subject_ids: [subjectId],
      session_count: 4,
      session_minutes: 90,
      modules: [{ title: 'Introducción', is_required: true }],
    },
  });
  expect(template.status).toBe(201);
  const created = await api(page, '/api/v1/cohorts', {
    method: 'POST',
    headers: { 'X-CSRF-Token': await csrf(page) },
    json: {
      name,
      template_id: (template.body as { id: string }).id,
      subject_id: subjectId,
      max_students: 10,
      starts_on: startsOn,
      start_time: startTime,
      recurrence: { weekdays: [isoWeekday(startsOn)], interval: 1 },
    },
  });
  expect(created.status).toBe(201);
  const cohortId = (created.body as { id: string }).id;
  for (const studentId of studentIds) {
    const enrolled = await api(
      page,
      `/api/v1/cohorts/${cohortId}/enrollments`,
      {
        method: 'POST',
        headers: { 'X-CSRF-Token': await csrf(page) },
        json: { student_id: studentId },
      },
    );
    expect(enrolled.status).toBe(201);
  }
  return cohortId;
}
