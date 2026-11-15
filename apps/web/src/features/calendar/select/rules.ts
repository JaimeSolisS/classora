import { toLocalDate, toLocalTime, type LocalDate } from '@/lib/time';
import type { CalendarFilters } from '../urlState';

// Scheduling from the calendar (HF-02), without React: which selected range
// is allowed, what a drag or a click pre-fills, and the "Schedule a session"
// URL that carries it. FullCalendar gives every edge as a real instant (a
// Date; its `timeZone` is the center zone), read here in the center zone
// with lib/time, never the browser's. The server stays the authority on
// every scheduling rule (operating hours, closures, overlaps, notice): only
// the shape of the range is checked here.

/** A click (no drag) lasts one hour (E2). */
export const CLICK_MINUTES = 60;
/** The API's longest session (`duration_out_of_range`). */
export const MAX_MINUTES = 480;
/** The calendar's snap (`snapDuration`): one step of a selection. */
export const SNAP_MINUTES = 15;
/** The latest end a click fills: the form's times stop at 23:59 (B2). */
export const LAST_END = '23:45';

/** The views that select (E6): Day and Week. */
export const SELECTABLE_VIEWS: ReadonlySet<string> = new Set([
  'timeGridDay',
  'timeGridWeek',
]);

const MINUTE_MS = 60_000;

/** What the form starts with: the center's local date and times. */
export interface SlotPrefill {
  date: LocalDate;
  /** HH:MM. */
  start: string;
  /** HH:MM; null leaves the end for the form to ask (a click at 23:45+). */
  end: string | null;
}

function minutesOf(time: string): number {
  const [hours, minutes] = time.split(':').map(Number) as [number, number];
  return hours * 60 + minutes;
}

function timeOf(total: number): string {
  const hours = String(Math.floor(total / 60)).padStart(2, '0');
  const minutes = String(total % 60).padStart(2, '0');
  return `${hours}:${minutes}`;
}

/**
 * Whether a selected range can open the form (B3, FullCalendar
 * `selectAllow`): not started (`start >= now`), 15 to 480 minutes, and one
 * local day in the center zone. An end at 24:00 is the next day, so it is
 * refused too (the form's times stop at 23:59).
 */
export function isSelectionAllowed(
  span: { start: Date; end: Date },
  now: number,
  timeZone: string,
): boolean {
  const start = span.start.getTime();
  const elapsed = span.end.getTime() - start;
  if (start < now || elapsed <= 0 || elapsed > MAX_MINUTES * MINUTE_MS) {
    return false;
  }
  return (
    toLocalDate(span.start.toISOString(), timeZone) ===
    toLocalDate(span.end.toISOString(), timeZone)
  );
}

/**
 * Whether a selection is one snap: the press and the release on the same
 * 15-minute mark. That is a click, which `dateClick` handles (B2), so the
 * selection itself opens nothing: one gesture, one navigation.
 */
export function isSingleSnap(span: { start: Date; end: Date }): boolean {
  return span.end.getTime() - span.start.getTime() <= SNAP_MINUTES * MINUTE_MS;
}

/** A dragged range's date and times in the center zone (B1, B6). */
export function selectionPrefill(
  span: { start: Date; end: Date },
  timeZone: string,
): SlotPrefill {
  return {
    date: toLocalDate(span.start.toISOString(), timeZone),
    start: toLocalTime(span.start.toISOString(), timeZone),
    end: toLocalTime(span.end.toISOString(), timeZone),
  };
}

/**
 * A click's pre-fill (B2): the 15-minute mark under the pointer for one hour,
 * the end capped at 23:45; from 23:45 on, the date and start only.
 */
export function clickPrefill(date: Date, timeZone: string): SlotPrefill {
  const start = toLocalTime(date.toISOString(), timeZone);
  const startMinutes = minutesOf(start);
  const last = minutesOf(LAST_END);
  return {
    date: toLocalDate(date.toISOString(), timeZone),
    start,
    end:
      startMinutes >= last
        ? null
        : timeOf(Math.min(startMinutes + CLICK_MINUTES, last)),
  };
}

/**
 * "Schedule a session" with the pre-fill (E1) and the calendar's tutor and
 * room filters (C2); the student, kind and cancelled filters stay behind.
 */
export function newSessionPath(
  prefill: SlotPrefill,
  filters: Pick<CalendarFilters, 'tutor' | 'room'>,
): string {
  // Every value is a date, an HH:MM time or a validated UUID (urlState), so
  // the query reads as written (`start=10:00`, not `10%3A00`).
  const pairs: [string, string | null][] = [
    ['date', prefill.date],
    ['start', prefill.start],
    ['end', prefill.end],
    ['tutor', filters.tutor],
    ['room', filters.room],
  ];
  const query = pairs
    .filter((pair): pair is [string, string] => Boolean(pair[1]))
    .map(([key, value]) => `${key}=${value}`)
    .join('&');
  return `/schedule/new?${query}`;
}
