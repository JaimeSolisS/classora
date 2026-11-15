import { describe, expect, it } from 'vitest';
import { DEFAULT_HOURS } from '@/features/settings/fixtures';
import type { LocalDate } from '@/lib/time';
import { ZONE } from './fixtures';
import {
  businessHoursOf,
  formatHour,
  FULL_DAY,
  visibleHours,
  type TimedItem,
} from './hours';

// HF-08: the hours a time grid shows, from the business settings' opening
// hours (the migration's defaults: Monday–Friday 09:00–20:00, Saturday
// 09:00–14:00, Sunday closed) and the loaded sessions, in the center zone
// (America/Mexico_City, UTC−6 in November).

const MONDAY = '2026-11-09' as LocalDate;
const SATURDAY = '2026-11-14' as LocalDate;
const SUNDAY = '2026-11-15' as LocalDate;

/** A session on a local date and times in Mexico City (UTC−6). */
function at(date: string, from: string, to: string, toDate = date): TimedItem {
  return {
    starts_at: new Date(`${date}T${from}:00-06:00`).toISOString(),
    ends_at: new Date(`${toDate}T${to}:00-06:00`).toISOString(),
  };
}

const gridOf = (
  view: 'day' | 'week' | 'month' | 'list',
  date: LocalDate,
  items: TimedItem[] = [],
  hours = DEFAULT_HOURS,
) => visibleHours(hours, view, date, items, ZONE);

describe('the time grid window (HF-08)', () => {
  it('shows the earliest opening to the latest closing in Week', () => {
    expect(gridOf('week', MONDAY)).toEqual({
      slotMinTime: '09:00:00',
      slotMaxTime: '20:00:00',
      scrollTime: '09:00:00',
      hours: { from: '09:00', to: '20:00', outside: false },
    });
  });

  it("shows that day's hours in Day, the week's on a closed day", () => {
    expect(gridOf('day', SATURDAY)).toMatchObject({
      slotMinTime: '09:00:00',
      slotMaxTime: '14:00:00',
      hours: { from: '09:00', to: '14:00' },
    });
    expect(gridOf('day', MONDAY)).toMatchObject({
      slotMinTime: '09:00:00',
      slotMaxTime: '20:00:00',
    });
    expect(gridOf('day', SUNDAY)).toMatchObject({
      slotMinTime: '09:00:00',
      slotMaxTime: '20:00:00',
      hours: { from: '09:00', to: '20:00' },
    });
  });

  it('rounds outward to whole hours', () => {
    const hours = [{ weekday: 2, opens_at: '09:30', closes_at: '20:15' }];
    expect(gridOf('week', MONDAY, [], hours)).toMatchObject({
      slotMinTime: '09:00:00',
      slotMaxTime: '21:00:00',
      scrollTime: '09:00:00',
      hours: { from: '09:30', to: '20:15', outside: false },
    });
  });

  it('grows to show a class before or after the hours', () => {
    const early = gridOf('week', MONDAY, [at('2026-11-11', '07:00', '08:00')]);
    expect(early).toMatchObject({
      slotMinTime: '07:00:00',
      slotMaxTime: '20:00:00',
      scrollTime: '07:00:00',
      hours: { outside: true },
    });
    const late = gridOf('week', MONDAY, [at('2026-11-11', '20:30', '21:30')]);
    expect(late).toMatchObject({
      slotMinTime: '09:00:00',
      slotMaxTime: '22:00:00',
      hours: { outside: true },
    });
  });

  it('keeps a class inside the window as it is', () => {
    expect(
      gridOf('week', MONDAY, [at('2026-11-11', '10:00', '11:00')]),
    ).toEqual(gridOf('week', MONDAY));
    // Saturday 15:00 is inside the Week window (09:00–20:00).
    expect(
      gridOf('week', MONDAY, [at('2026-11-14', '15:00', '16:00')]).hours,
    ).toMatchObject({ outside: false });
  });

  it('shows a class on a closed day', () => {
    expect(
      gridOf('day', SUNDAY, [at('2026-11-15', '07:30', '08:30')]),
    ).toMatchObject({ slotMinTime: '07:00:00', slotMaxTime: '20:00:00' });
    expect(
      gridOf('week', MONDAY, [at('2026-11-15', '21:00', '22:15')]),
    ).toMatchObject({ slotMinTime: '09:00:00', slotMaxTime: '23:00:00' });
  });

  it('in Day, grows only for the shown date', () => {
    expect(
      gridOf('day', SATURDAY, [at('2026-11-13', '07:00', '08:00')]),
    ).toMatchObject({ slotMinTime: '09:00:00', slotMaxTime: '14:00:00' });
  });

  it('clamps to the day: a class across midnight ends the window at 24:00', () => {
    const crossing = at('2026-11-11', '23:00', '01:00', '2026-11-12');
    expect(gridOf('day', '2026-11-11' as LocalDate, [crossing])).toMatchObject({
      slotMinTime: '09:00:00',
      slotMaxTime: '24:00:00',
    });
    expect(gridOf('day', '2026-11-12' as LocalDate, [crossing])).toMatchObject({
      slotMinTime: '00:00:00',
      slotMaxTime: '20:00:00',
    });
    expect(gridOf('week', MONDAY, [crossing])).toMatchObject({
      slotMinTime: '00:00:00',
      slotMaxTime: '24:00:00',
    });
    const toMidnight = at('2026-11-11', '22:00', '00:00', '2026-11-12');
    expect(
      gridOf('day', '2026-11-12' as LocalDate, [toMidnight]),
    ).toMatchObject({ slotMinTime: '09:00:00' });
  });

  it('falls back to the whole day with no open weekday or no settings', () => {
    expect(gridOf('week', MONDAY, [], [])).toEqual(FULL_DAY);
    expect(visibleHours(null, 'week', MONDAY, [], ZONE)).toEqual(FULL_DAY);
    expect(FULL_DAY).toEqual({
      slotMinTime: '00:00:00',
      slotMaxTime: '24:00:00',
      scrollTime: '07:00:00',
      hours: null,
    });
  });
});

describe('the closed-time shading (HF-08)', () => {
  it('has one entry per open weekday, Sunday = 0', () => {
    expect(
      businessHoursOf([
        ...DEFAULT_HOURS,
        { weekday: 7, opens_at: '07:00', closes_at: '08:00' },
      ]),
    ).toEqual([
      ...[1, 2, 3, 4, 5].map((day) => ({
        daysOfWeek: [day],
        startTime: '09:00',
        endTime: '20:00',
      })),
      { daysOfWeek: [6], startTime: '09:00', endTime: '14:00' },
      { daysOfWeek: [0], startTime: '07:00', endTime: '08:00' },
    ]);
  });

  it('shades nothing without hours', () => {
    expect(businessHoursOf([])).toBe(false);
    expect(businessHoursOf(null)).toBe(false);
  });
});

describe('the hours caption format (HF-08)', () => {
  it('uses the slot labels: 24-hour in Spanish, 12-hour in English', () => {
    expect(formatHour('09:00', 'es')).toBe('9:00');
    expect(formatHour('20:15', 'es')).toBe('20:15');
    expect(formatHour('09:00', 'en')).toMatch(/^9:00\sAM$/);
    expect(formatHour('20:15', 'en')).toMatch(/^8:15\sPM$/);
  });
});
