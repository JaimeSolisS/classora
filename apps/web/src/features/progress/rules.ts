import { ApiError } from '@/app/api';
import { uuidParam } from '@/features/academics/history/urlState';
import type { Language } from '@/i18n/language';
import { addMonths, type LocalDate } from '@/lib/time';
import type { ProgressSubject, StudentProgress } from './api';

// The progress pages' pure rules (#322 B2, B3, D1): the period presets and
// their range in the center zone, the controls in the URL (`months`,
// `subject`; the family page's `student` is kept), the subject options, the
// month labels, and the range and subject refusals. Pure.

export const MONTH_PRESETS = [3, 6, 12, 24] as const;
export type MonthPreset = (typeof MONTH_PRESETS)[number];
export const DEFAULT_MONTHS: MonthPreset = 12;

export interface ProgressControls {
  months: MonthPreset;
  /** A subject id, or null for every subject. */
  subject: string | null;
}

export const DEFAULT_CONTROLS: ProgressControls = {
  months: DEFAULT_MONTHS,
  subject: null,
};

/**
 * The range of a preset: `to` is the center's today; `from` is the first
 * day of the month `months - 1` months before `to`'s month. 12 is #84 E6's
 * default range, and 24 its maximum. `today` must be the center-local date
 * (`todayInZone(centerTimeZone)`), never the device's or UTC.
 */
export function rangeFor(
  months: MonthPreset,
  today: LocalDate,
): { from: LocalDate; to: LocalDate } {
  const start = addMonths(today, -(months - 1));
  return { from: `${start.slice(0, 8)}01` as LocalDate, to: today };
}

/** The controls of the URL; anything invalid falls back to the default. */
export function parseProgressControls(
  params: URLSearchParams,
): ProgressControls {
  const months = Number(params.get('months'));
  return {
    months: MONTH_PRESETS.find((value) => value === months) ?? DEFAULT_MONTHS,
    subject: uuidParam(params.get('subject')),
  };
}

/** The params with these controls (only what differs from the default);
 * other params (`student`) are kept. */
export function withProgressControls(
  params: URLSearchParams,
  controls: ProgressControls,
): URLSearchParams {
  const next = new URLSearchParams(params);
  next.delete('months');
  next.delete('subject');
  if (controls.months !== DEFAULT_MONTHS) {
    next.set('months', String(controls.months));
  }
  if (controls.subject) {
    next.set('subject', controls.subject);
  }
  return next;
}

/** "Clear filters" shows while the subject or the preset isn't the default. */
export function hasProgressFilters(controls: ProgressControls): boolean {
  return controls.months !== DEFAULT_MONTHS || controls.subject !== null;
}

/**
 * The subject filter's options: the subjects of the unfiltered range (AM1),
 * plus the filtered subject when it isn't among them (an id from the URL
 * the API accepted), so the select always shows the current choice.
 */
export function subjectOptions(
  progress: StudentProgress | undefined,
): ProgressSubject[] {
  if (!progress) {
    return [];
  }
  const options = [...progress.subjects];
  if (
    progress.subject &&
    !options.some((subject) => subject.id === progress.subject!.id)
  ) {
    options.push(progress.subject);
  }
  return options;
}

const MONTH = /^(\d{4})-(\d{2})$/;

/**
 * A center-local month ("2026-03") as "Mar 2026" / "mar 2026". The year and
 * month are formatted as they are (UTC on both sides), never through a
 * `Date` in the device's zone. An unexpected value is shown as sent.
 */
export function monthLabel(month: string, language: Language): string {
  const match = MONTH.exec(month);
  if (!match) {
    return month;
  }
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
  return new Intl.DateTimeFormat(language, {
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date);
}

/** The range and subject refusals (#84: `errors[].code` of a
 * `validation_error`, or a top-level code). */
export const RANGE_ERROR_CODES = [
  'range_invalid',
  'range_in_future',
  'range_too_long',
  'subject_unknown',
] as const;
export type RangeErrorCode = (typeof RANGE_ERROR_CODES)[number];

/** The `errors` catalog key of each refusal. range_too_long has its own
 * key here: the catalog's `range_too_long` is the calendar's 45-day text. */
export const RANGE_ERROR_KEYS = {
  range_invalid: 'range_invalid',
  range_in_future: 'range_in_future',
  range_too_long: 'progress_range_too_long',
  subject_unknown: 'subject_unknown',
} as const satisfies Record<RangeErrorCode, string>;

/** The refusal of a failed read, if it is a range or subject one. */
export function rangeErrorOf(error: unknown): RangeErrorCode | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const codes = [error.code, ...error.fieldErrors.map((item) => item.code)];
  return RANGE_ERROR_CODES.find((code) => codes.includes(code)) ?? null;
}

/** A failed read that is a 404 (not available, or a former tutor). */
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** A failed read refused for the adult student's consent. */
export function isConsentRequired(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'student_consent_required';
}
