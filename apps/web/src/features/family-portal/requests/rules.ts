import { ApiError } from '@/app/api';
import type { ErrorMessageKey } from '@/app/errors';
import type { Language } from '@/i18n/language';
import { addDays, formatInstant } from '@/lib/time';
import { NOTE_MAX_LENGTH, type SubjectChoice } from './api';

// The rules of the family request pages (#59 F2-F4), without React.

/** The dates one page of slots covers. */
export const WEEK_DAYS = 7;

/** How far ahead a family may search (the API's `range_too_far`). */
export const MAX_DAYS_AHEAD = 60;

/** The dates `[from, to]` of the week starting on `start`, never after
 * today + 60 days. */
export function weekRange(
  start: string,
  today: string,
): { from: string; to: string } {
  const last = addDays(start, WEEK_DAYS - 1);
  const limit = addDays(today, MAX_DAYS_AHEAD);
  return { from: start, to: last > limit ? limit : last };
}

/** "Previous week" goes no earlier than today. */
export function canGoBack(start: string, today: string): boolean {
  return start > today;
}

/** "Next week" goes no later than today + 60 days. */
export function canGoForward(start: string, today: string): boolean {
  return addDays(start, WEEK_DAYS) <= addDays(today, MAX_DAYS_AHEAD);
}

/** The start of the previous week, never before today. */
export function previousWeek(start: string, today: string): string {
  const previous = addDays(start, -WEEK_DAYS);
  return previous < today ? today : previous;
}

/** The local start and end of a slot in the center zone. */
export function slotTimes(
  slot: { starts_at: string; ends_at: string },
  timeZone: string,
  language: Language,
): { date: string; start: string; end: string } {
  return {
    date: formatInstant(slot.starts_at, { timeZone, language, style: 'date' }),
    start: formatInstant(slot.starts_at, {
      timeZone,
      language,
      style: 'time',
    }),
    end: formatInstant(slot.ends_at, { timeZone, language, style: 'time' }),
  };
}

/** The note's length once trimmed (the counter). */
export function noteLength(note: string): number {
  return note.trim().length;
}

/** Whether the note is too long. */
export function noteTooLong(note: string): boolean {
  return noteLength(note) > NOTE_MAX_LENGTH;
}

/** Active subjects grouped by level name (levels and subjects by name). */
export function subjectsByLevel(
  subjects: readonly SubjectChoice[],
): [string, SubjectChoice[]][] {
  const groups = new Map<string, SubjectChoice[]>();
  for (const subject of subjects) {
    const list = groups.get(subject.levelName) ?? [];
    list.push(subject);
    groups.set(subject.levelName, list);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([level, list]) => [
      level,
      [...list].sort((a, b) => a.name.localeCompare(b.name)),
    ]);
}

/** Whole minutes to wait after a `429` (at least 1), or null. */
export function retryMinutes(error: unknown): number | null {
  if (!(error instanceof ApiError) || error.status !== 429) {
    return null;
  }
  return Math.max(1, Math.ceil((error.retryAfter ?? 60) / 60));
}

/** The codes of a refused request that have their own message (F3). */
export type RequestRefusal =
  | 'slot_unavailable'
  | 'session_request_overlap'
  | 'session_request_limit_reached'
  | 'student_consent_required';

/** The F3 outcome of a refused request or slot search, or null for an error
 * the shared `ErrorAlert` shows. */
export function requestRefusal(error: unknown): RequestRefusal | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  switch (error.code) {
    case 'slot_unavailable':
    case 'session_request_overlap':
    case 'session_request_limit_reached':
    case 'student_consent_required':
      return error.code;
    default:
      return null;
  }
}

/** A withdrawal that can no longer happen (the list is stale): its message
 * key, so the card shows it and the list is refreshed. Null otherwise. */
export function staleWithdrawal(error: unknown): ErrorMessageKey | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  if (
    error.code === 'version_conflict' ||
    error.code === 'session_request_not_pending'
  ) {
    return error.code === 'version_conflict'
      ? 'session_request_changed'
      : 'session_request_not_pending';
  }
  return null;
}
