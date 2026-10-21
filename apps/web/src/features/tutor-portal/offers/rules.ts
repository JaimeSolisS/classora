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
