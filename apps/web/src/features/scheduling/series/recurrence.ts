import type { FieldError } from '@classora/api-client';
import type { ErrorMessageKey } from '@/app/errors';
import { isoWeekday } from '@/lib/time';
import type { SeriesRecurrence } from './api';

// The "Repeats weekly" controls (#54b A1, A2), without React: the values,
// the client checks (at least one weekday, the interval and the count in
// their bounds, the end date on or after the start), the structured
// `recurrence` member of the requests (never RRULE text, ADR-0005 D4), the
// duration from the start and end times, and where the server's 422
// `recurrence.*` errors go. Every other rule is the server's.

/** How the series ends: never, on a date, or after a number of sessions. */
export type EndsKind = 'never' | 'until' | 'count';

export interface RecurrenceValues {
  /** ISO weekdays, 1 = Monday … 7 = Sunday. */
  weekdays: number[];
  /** Every N weeks, 1-4. */
  interval: number;
  ends: EndsKind;
  /** YYYY-MM-DD (with `ends = 'until'`). */
  until: string;
  /** The number of occurrences as typed (with `ends = 'count'`). */
  count: string;
}

export type RecurrenceField =
  'weekdays' | 'interval' | 'ends' | 'until' | 'count';

export type RecurrenceMessage =
  | 'scheduling.series.validation.weekdaysRequired'
  | 'scheduling.series.validation.intervalRange'
  | 'scheduling.series.validation.untilRequired'
  | 'scheduling.series.validation.untilBeforeStart'
  | 'scheduling.series.validation.countRange'
  | `errors:${ErrorMessageKey}`;

export type RecurrenceErrors = Partial<
  Record<RecurrenceField, RecurrenceMessage>
>;

export const ISO_WEEKDAYS = [1, 2, 3, 4, 5, 6, 7] as const;
export const INTERVALS = [1, 2, 3, 4] as const;
export const MAX_COUNT = 200;

/** The order of the controls (the first one with an error gets the focus). */
export const RECURRENCE_FIELD_ORDER: readonly RecurrenceField[] = [
  'weekdays',
  'interval',
  'ends',
  'until',
  'count',
];

export const EMPTY_RECURRENCE: RecurrenceValues = {
  weekdays: [],
  interval: 1,
  ends: 'never',
  until: '',
  count: '',
};

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const WHOLE = /^\d+$/;

/** The weekday a start date pre-checks (none for an incomplete date). */
export function weekdaysFor(startsOn: string): number[] {
  return DATE.test(startsOn) ? [isoWeekday(startsOn)] : [];
}

/**
 * The client checks (A2): a weekday, the interval 1-4, an end date on or
 * after `startsOn` (YYYY-MM-DD strings compare like the dates they hold),
 * a whole count of 1-200.
 */
export function validateRecurrence(
  values: RecurrenceValues,
  startsOn: string,
): RecurrenceErrors {
  const errors: RecurrenceErrors = {};
  if (values.weekdays.length === 0) {
    errors.weekdays = 'scheduling.series.validation.weekdaysRequired';
  }
  if (!(INTERVALS as readonly number[]).includes(values.interval)) {
    errors.interval = 'scheduling.series.validation.intervalRange';
  }
  if (values.ends === 'until') {
    if (!DATE.test(values.until)) {
      errors.until = 'scheduling.series.validation.untilRequired';
    } else if (DATE.test(startsOn) && values.until < startsOn) {
      errors.until = 'scheduling.series.validation.untilBeforeStart';
    }
  }
  if (values.ends === 'count') {
    const count = values.count.trim();
    const n = Number(count);
    if (!WHOLE.test(count) || n < 1 || n > MAX_COUNT) {
      errors.count = 'scheduling.series.validation.countRange';
    }
  }
  return errors;
}

/** The structured rule of a request: sorted weekdays, one end or none. */
export function recurrenceBody(values: RecurrenceValues): SeriesRecurrence {
  return {
    weekdays: [...new Set(values.weekdays)].sort((a, b) => a - b),
    interval: values.interval,
    until: values.ends === 'until' ? values.until : null,
    count: values.ends === 'count' ? Number(values.count.trim()) : null,
  };
}

/** The minutes from `start` to `end` (HH:MM, same date); null if unknown. */
export function durationMinutes(start: string, end: string): number | null {
  if (!TIME.test(start) || !TIME.test(end)) {
    return null;
  }
  const minutes = (hhmm: string) => {
    const [h = 0, m = 0] = hhmm.split(':').map(Number);
    return h * 60 + m;
  };
  return minutes(end) - minutes(start);
}

/** The end time `HH:MM` of a start plus a duration (24:00 at the latest). */
export function endTime(start: string, duration: number): string {
  const [h = 0, m = 0] = start.split(':').map(Number);
  const total = Math.min(h * 60 + m + duration, 24 * 60);
  // An <input type="time"> can't hold 24:00: a midnight end is 23:59 + 1.
  const shown = total === 24 * 60 ? total - 1 : total;
  return `${String(Math.floor(shown / 60)).padStart(2, '0')}:${String(shown % 60).padStart(2, '0')}`;
}

/**
 * The controls pre-filled from a stored rule (C2): a `COUNT` series ends
 * "On date" = its `last_occurrence_on` (#54 C3: the end stays the same).
 */
export function recurrenceValuesOf(
  rule: {
    weekdays: readonly number[];
    interval: number;
    until: string | null;
    count: number | null;
  },
  lastOccurrenceOn: string | null,
): RecurrenceValues {
  let ends: EndsKind = 'never';
  let until = '';
  if (rule.until) {
    ends = 'until';
    until = rule.until;
  } else if (rule.count !== null) {
    ends = lastOccurrenceOn ? 'until' : 'count';
    until = lastOccurrenceOn ?? '';
  }
  return {
    weekdays: [...rule.weekdays],
    interval: rule.interval,
    ends,
    until,
    count: ends === 'count' && rule.count !== null ? String(rule.count) : '',
  };
}

/** The 422 codes of a rule with their own `errors` key (#53 E1). */
const RECURRENCE_CODES: ReadonlySet<string> = new Set([
  'weekdays_empty',
  'weekday_out_of_range',
  'weekdays_duplicate',
  'interval_out_of_range',
  'until_and_count',
  'count_out_of_range',
  'until_before_start',
  'no_occurrences',
]);

const RECURRENCE_FIELDS: Record<string, RecurrenceField> = {
  weekdays: 'weekdays',
  interval: 'interval',
  until: 'until',
  count: 'count',
};

/**
 * The server's 422 on `recurrence.<field>` (and `no_occurrences` on
 * `recurrence` itself, shown on "Ends"); others are left out.
 */
export function recurrenceServerErrors(
  fieldErrors: readonly FieldError[],
): RecurrenceErrors {
  const errors: RecurrenceErrors = {};
  for (const { path, code } of fieldErrors) {
    const at = path.indexOf('recurrence');
    if (at < 0) {
      continue;
    }
    const name = path[at + 1];
    const field = name === undefined ? 'ends' : RECURRENCE_FIELDS[String(name)];
    if (!field || errors[field]) {
      continue;
    }
    errors[field] = RECURRENCE_CODES.has(code)
      ? (`errors:${code}` as `errors:${ErrorMessageKey}`)
      : 'errors:validation_error';
  }
  return errors;
}

/** The id of a control (the focus target of its error). */
export function recurrenceFieldId(prefix: string, field: RecurrenceField) {
  return field === 'weekdays'
    ? `${prefix}-weekday-${ISO_WEEKDAYS[0]}`
    : field === 'ends'
      ? `${prefix}-ends-never`
      : `${prefix}-${field}`;
}
