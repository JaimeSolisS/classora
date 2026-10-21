import { ApiError } from '@/app/api';
import type { ErrorMessageKey } from '@/app/errors';
import type { Language } from '@/i18n/language';
import { formatInstant } from '@/lib/time';

// The rules of "My offers" (#52 H1), without React.

/** The most characters of a decline note, once trimmed (API D3). */
export const DECLINE_NOTE_MAX_LENGTH = 500;

/** An offer is "due soon" when its deadline is less than this away. */
export const DUE_SOON_MS = 24 * 60 * 60 * 1000;

/** Whether the deadline is within 24 hours of `now` (and not yet passed). */
export function isDueSoon(respondBy: string, now: Date): boolean {
  const left = Date.parse(respondBy) - now.getTime();
  return left >= 0 && left < DUE_SOON_MS;
}

/** The decline note's length once trimmed (the counter). */
export function noteLength(note: string): number {
  return note.trim().length;
}

/** Whether the decline note is too long. */
export function noteTooLong(note: string): boolean {
  return noteLength(note) > DECLINE_NOTE_MAX_LENGTH;
}

/**
 * An answer refused because the offer can no longer be answered (`409
 * offer_not_pending` / `offer_expired`): its message key, so the card
 * shows it and the list is refreshed. Null for any other error.
 */
export function staleAnswer(error: unknown): ErrorMessageKey | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  return error.code === 'offer_not_pending' || error.code === 'offer_expired'
    ? error.code
    : null;
}

/** The local date and times of a session in the center zone (H1). */
export function sessionTimes(
  session: { starts_at: string; ends_at: string },
  timeZone: string,
  language: Language,
): { date: string; start: string; end: string } {
  return {
    date: formatInstant(session.starts_at, {
      timeZone,
      language,
      style: 'date',
    }),
    start: formatInstant(session.starts_at, {
      timeZone,
      language,
      style: 'time',
    }),
    end: formatInstant(session.ends_at, { timeZone, language, style: 'time' }),
  };
}

/** The deadline as a date and time in the center zone (H1). */
export function deadlineText(
  respondBy: string,
  timeZone: string,
  language: Language,
): string {
  return formatInstant(respondBy, { timeZone, language, style: 'dateTime' });
}

/** The series of a series offer (#233 F1). */
export interface OfferSeriesRule {
  weekdays: readonly number[];
  interval: number;
  start_time: string;
  duration_minutes: number;
  from_date: string;
  until: string | null;
  count: number | null;
}

/**
 * A local wall-clock time `HH:MM` plus `minutes`, as `HH:MM` (a series
 * occurrence never crosses the local date, so 24:00 is the latest end).
 */
export function addMinutesToTime(hhmm: string, minutes: number): string {
  const [hours = 0, mins = 0] = hhmm.split(':').map(Number);
  const total = hours * 60 + mins + minutes;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/** A local `HH:MM` in the language's clock style ("16:00" / "4:00 PM"). */
export function localTimeText(hhmm: string, language: Language): string {
  const [hours = 0, minutes = 0] = hhmm.split(':').map(Number);
  if (language === 'es') {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
  }
  const suffix = hours >= 12 && hours < 24 ? 'PM' : 'AM';
  const twelve = hours % 12 === 0 ? 12 : hours % 12;
  return `${twelve}:${String(minutes).padStart(2, '0')} ${suffix}`;
}

/** Weekday names joined as a list in the language ("Monday and Wednesday"). */
export function weekdayList(
  weekdays: readonly number[],
  name: (weekday: number) => string,
  language: Language,
): string {
  return new Intl.ListFormat(language, {
    style: 'long',
    type: 'conjunction',
  }).format([...weekdays].sort((a, b) => a - b).map(name));
}
