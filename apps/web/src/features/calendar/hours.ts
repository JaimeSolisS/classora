import type { OperatingHoursItem } from '@/features/settings/api';
import {
  isoWeekday,
  toLocalDate,
  toLocalTime,
  type Instant,
  type LocalDate,
} from '@/lib/time';
import type { CalendarView } from './views';

// The hours a time grid shows (HF-08), without React. Day and Week show the
// center's opening hours (the business settings' `operating_hours`), not
// 00:00–24:00:
// - Week: the earliest opening to the latest closing of the open weekdays.
// - Day: that weekday's hours, or the Week window when it is closed.
// The window is rounded outward to whole hours and grows (whole hours) to
// show every loaded session outside it, read in the center zone: a class is
// never hidden. FullCalendar's slot times are per view, not per day, so the
// closed parts of each day are shaded (`businessHours`). With no open
// weekday (or no settings), the grid is the full day, as before HF-08.
// Month and List have no time axis: they ignore the window.

/** A loaded session's instants (CalendarItem, ScheduleEntry.item). */
export interface TimedItem {
  starts_at: Instant;
  ends_at: Instant;
}

/** The hours the caption names, as stored (HH:MM). */
export interface CenterHours {
  from: string;
  to: string;
  /** A loaded session lies outside them. */
  outside: boolean;
}

export interface HoursWindow {
  /** FullCalendar durations, "HH:MM:00"; the end may be "24:00:00". */
  slotMinTime: string;
  slotMaxTime: string;
  scrollTime: string;
  /** null: the full-day fallback (no caption). */
  hours: CenterHours | null;
}

/** FullCalendar's `businessHours`: one entry per open weekday. */
export interface BusinessHoursEntry {
  daysOfWeek: number[];
  startTime: string;
  endTime: string;
}

const DAY_MINUTES = 24 * 60;

/** Before HF-08 (and its fallback): the whole day, scrolled to 07:00. */
export const FULL_DAY: HoursWindow = {
  slotMinTime: '00:00:00',
  slotMaxTime: '24:00:00',
  scrollTime: '07:00:00',
  hours: null,
};

function minutesOf(time: string): number {
  const [hours, minutes] = time.split(':').map(Number) as [number, number];
  return hours * 60 + minutes;
}

function duration(minutes: number): string {
  const hours = String(Math.floor(minutes / 60)).padStart(2, '0');
  const rest = String(minutes % 60).padStart(2, '0');
  return `${hours}:${rest}:00`;
}

const floorHour = (minutes: number) => Math.floor(minutes / 60) * 60;
const ceilHour = (minutes: number) => Math.ceil(minutes / 60) * 60;

/** The open weekdays only (closes after opens, as the API keeps them). */
function openDays(
  hours: readonly OperatingHoursItem[] | null | undefined,
): OperatingHoursItem[] {
  return (hours ?? []).filter(
    (item) => minutesOf(item.closes_at) > minutesOf(item.opens_at),
  );
}

/** A session's time on each local date it touches: [start, end) minutes. */
function segments(
  item: TimedItem,
  timeZone: string,
): { date: LocalDate; start: number; end: number }[] {
  const startDate = toLocalDate(item.starts_at, timeZone);
  const endDate = toLocalDate(item.ends_at, timeZone);
  const start = minutesOf(toLocalTime(item.starts_at, timeZone));
  const end = minutesOf(toLocalTime(item.ends_at, timeZone));
  if (startDate === endDate) {
    return [{ date: startDate, start, end }];
  }
  // Across midnight: to 24:00 on its start day, from 00:00 on its end day.
  const parts = [{ date: startDate, start, end: DAY_MINUTES }];
  if (end > 0) {
    parts.push({ date: endDate, start: 0, end });
  }
  return parts;
}

/**
 * The window of a time grid: `view` and `date` are the calendar's; `items`
 * are the loaded sessions of its visible range.
 */
export function visibleHours(
  operatingHours: readonly OperatingHoursItem[] | null | undefined,
  view: CalendarView,
  date: LocalDate,
  items: readonly TimedItem[],
  timeZone: string,
): HoursWindow {
  const open = openDays(operatingHours);
  if (open.length === 0) {
    return FULL_DAY;
  }
  const today =
    view === 'day'
      ? open.find((item) => item.weekday === isoWeekday(date))
      : undefined;
  const days = today ? [today] : open;
  let from = Math.min(...days.map((item) => minutesOf(item.opens_at)));
  let to = Math.max(...days.map((item) => minutesOf(item.closes_at)));
  const hours = {
    from: duration(from).slice(0, 5),
    to: duration(to).slice(0, 5),
  };
  const opens = from;
  const closes = to;
  from = floorHour(from);
  to = ceilHour(to);

  let outside = false;
  for (const item of items) {
    for (const part of segments(item, timeZone)) {
      if (view === 'day' && part.date !== date) {
        continue;
      }
      if (part.start < opens || part.end > closes) {
        outside = true;
      }
      from = Math.min(from, floorHour(part.start));
      to = Math.max(to, ceilHour(part.end));
    }
  }
  from = Math.max(0, from);
  to = Math.min(DAY_MINUTES, to);
  return {
    slotMinTime: duration(from),
    slotMaxTime: duration(to),
    scrollTime: duration(from),
    hours: { ...hours, outside },
  };
}

/**
 * FullCalendar's `businessHours` for the stored hours (its weekdays are
 * 0 = Sunday … 6 = Saturday); `false` (no shading) with no open weekday.
 */
export function businessHoursOf(
  operatingHours: readonly OperatingHoursItem[] | null | undefined,
): BusinessHoursEntry[] | false {
  const open = openDays(operatingHours);
  if (open.length === 0) {
    return false;
  }
  return open.map((item) => ({
    daysOfWeek: [item.weekday % 7],
    startTime: item.opens_at,
    endTime: item.closes_at,
  }));
}

/** "9:00" (es) / "9:00 AM" (en): the slot labels' format. */
export function formatHour(time: string, language: 'es' | 'en'): string {
  const minutes = minutesOf(time);
  return new Intl.DateTimeFormat(language, {
    hour: 'numeric',
    minute: '2-digit',
    hour12: language === 'en',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(2000, 0, 1, 0, minutes)));
}
