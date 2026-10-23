import { expect, type Page } from '@playwright/test';
import { api, csrfTokenOf } from './api';

// One-off class sessions through the real API (#57, #58) with the page's
// own session (the shared demo Owner, schedule.manage): no login. The page
// must be on the app origin first. The center zone is the demo's
// America/Mexico_City, which has no DST, so local times carry -06:00.

export const CENTER_ZONE = 'America/Mexico_City';
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
