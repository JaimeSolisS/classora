import type { FieldError } from '@classora/api-client';
import { ApiError } from '@/app/api';
import type { ErrorMessageKey } from '@/app/errors';
import { localDateTimeToInstant, toLocalDate, toLocalTime } from '@/lib/time';
import { FIELD_ERROR_CODES, type ScheduleValues } from '../create/rules';
import type { ClassSeriesDetail } from '../session/api';
import type {
  ClassSeriesCancel,
  ClassSeriesCreate,
  ClassSeriesEdit,
  ClassSessionUpdate,
  SeriesEditMode,
  SeriesEditSummary,
  SeriesOccurrence,
} from './api';
import {
  durationMinutes,
  endTime,
  recurrenceBody,
  recurrenceServerErrors,
  recurrenceValuesOf,
  validateRecurrence,
  type RecurrenceErrors,
  type RecurrenceValues,
} from './recurrence';

// The rules of the series page and its dialogs (#54b B3, C1-C3), without
// React: which actions show, the pre-filled values, the client checks, the
// request bodies (local dates and times in the center zone; instants via
// lib/time for one occurrence) and where the server's 422 errors go.

// --- The create (A2) -----------------------------------------------------------

/**
 * The POST /class-series body: the form's local start date and time in the
 * center zone, the duration the client computes from the start and end,
 * and the structured rule. Never RRULE text (ADR-0005 D4).
 */
export function seriesCreateBody(
  values: ScheduleValues,
  recurrence: RecurrenceValues,
): ClassSeriesCreate {
  return {
    kind: 'individual',
    student_id: values.student?.id ?? '',
    subject_id: values.subjectId,
    tutor_user_id: values.tutorId || null,
    classroom_id: values.classroomId || null,
    starts_on: values.date,
    start_time: values.start,
    duration_minutes: durationMinutes(values.start, values.end) ?? 0,
    recurrence: recurrenceBody(recurrence),
  };
}

/** The states an occurrence can't be edited in (C1, #54 C1 step 5). */
const NOT_EDITABLE: ReadonlySet<SeriesOccurrence['state']> = new Set([
  'completed',
  'cancelled',
  'skipped_closure',
]);

/** The states that hold a slot (a class still to give). */
const HELD: ReadonlySet<SeriesOccurrence['state']> = new Set([
  'unassigned',
  'offered',
  'confirmed',
  'conflict',
]);

/**
 * Whether the series is over (B2 "Ended"): the server's `ended`, or a
 * finite series with nothing left to hold, because a cancellation ended it
 * before its next class (`last_occurrence_on` before `starts_on`, or
 * before every row still to come). Only server dates are compared; the web
 * never compares them with today (#54 A3).
 */
export function isSeriesOver(
  series: Pick<
    ClassSeriesDetail,
    'ended' | 'last_occurrence_on' | 'starts_on' | 'occurrences'
  >,
  timeZone: string,
): boolean {
  if (series.ended) {
    return true;
  }
  const last = series.last_occurrence_on;
  if (last === null) {
    return false;
  }
  if (series.occurrences.some((row) => HELD.has(row.state))) {
    return false;
  }
  return (
    last < series.starts_on ||
    series.occurrences.some(
      (row) => toLocalDate(row.original_start, timeZone) > last,
    )
  );
}

/** Whether a row offers "Edit this occurrence" (B3): not started, editable. */
export function canEditOccurrence(
  occurrence: Pick<SeriesOccurrence, 'state' | 'starts_at'>,
  now: number = Date.now(),
): boolean {
  return (
    Date.parse(occurrence.starts_at) > now &&
    !NOT_EDITABLE.has(occurrence.state)
  );
}

export interface SeriesActions {
  editSeries: boolean;
  cancel: boolean;
  /** Whether this row offers "Edit this occurrence". */
  editOccurrence: (occurrence: SeriesOccurrence) => boolean;
}

/** The B3 actions: schedule.manage holders, and never on an ended series. */
export function seriesActions(
  series: ClassSeriesDetail,
  canManage: boolean,
  timeZone: string,
  now: number = Date.now(),
): SeriesActions {
  const open = canManage && !isSeriesOver(series, timeZone);
  return {
    editSeries: open,
    cancel: open,
    editOccurrence: (occurrence) => open && canEditOccurrence(occurrence, now),
  };
}

/**
 * The scope's default date (C2): the clicked occurrence's local date, else
 * the next occurrence that hasn't started and still holds a class, else
 * `fallback` (the center's today).
 */
export function defaultFromDate(
  series: Pick<ClassSeriesDetail, 'occurrences'>,
  timeZone: string,
  fallback: string,
  now: number = Date.now(),
  clicked?: SeriesOccurrence,
): string {
  const row =
    clicked ??
    series.occurrences.find(
      (o) => Date.parse(o.starts_at) > now && HELD.has(o.state),
    );
  return row ? toLocalDate(row.starts_at, timeZone) : fallback;
}

// --- Edit this occurrence (C1) ------------------------------------------------

export interface OccurrenceValues {
  /** YYYY-MM-DD in the center zone. */
  date: string;
  start: string;
  end: string;
  /** '' = "No classroom". */
  classroomId: string;
}

export type SlotField = 'date' | 'start' | 'end' | 'classroom';

export type SlotMessage =
  | 'scheduling.validation.dateRequired'
  | 'scheduling.validation.startRequired'
  | 'scheduling.validation.endRequired'
  | 'scheduling.validation.endAfterStart'
  | `errors:${ErrorMessageKey}`;

export type SlotErrors = Partial<Record<SlotField, SlotMessage>>;

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

/** The row's local date, times and the session's room. */
export function occurrenceValues(
  occurrence: Pick<SeriesOccurrence, 'starts_at' | 'ends_at'>,
  classroomId: string | null,
  timeZone: string,
): OccurrenceValues {
  return {
    date: toLocalDate(occurrence.starts_at, timeZone),
    start: toLocalTime(occurrence.starts_at, timeZone),
    end: toLocalTime(occurrence.ends_at, timeZone),
    classroomId: classroomId ?? '',
  };
}

function validateTimes(start: string, end: string): SlotErrors {
  const errors: SlotErrors = {};
  if (!TIME.test(start)) {
    errors.start = 'scheduling.validation.startRequired';
  }
  if (!TIME.test(end)) {
    errors.end = 'scheduling.validation.endRequired';
  } else if (TIME.test(start) && end <= start) {
    errors.end = 'scheduling.validation.endAfterStart';
  }
  return errors;
}

export function validateOccurrence(values: OccurrenceValues): SlotErrors {
  const errors = validateTimes(values.start, values.end);
  if (!DATE.test(values.date)) {
    errors.date = 'scheduling.validation.dateRequired';
  }
  return errors;
}

/** The PATCH body: the instants from the center zone, the room, the version. */
export function occurrenceBody(
  values: OccurrenceValues,
  version: number,
  timeZone: string,
): ClassSessionUpdate {
  return {
    version,
    starts_at: localDateTimeToInstant(values.date, values.start, timeZone),
    ends_at: localDateTimeToInstant(values.date, values.end, timeZone),
    classroom_id: values.classroomId || null,
  };
}

const SLOT_FIELDS: Record<string, SlotField> = {
  starts_at: 'start',
  ends_at: 'end',
  start_time: 'start',
  duration_minutes: 'end',
  classroom_id: 'classroom',
};

/** The 422s of the PATCH or the series edit on the date, times and room. */
export function slotServerErrors(
  fieldErrors: readonly FieldError[],
): SlotErrors {
  const errors: SlotErrors = {};
  for (const { path, code } of fieldErrors) {
    if (path.includes('recurrence')) {
      continue;
    }
    const field = SLOT_FIELDS[String(path[path.length - 1])];
    if (!field || errors[field]) {
      continue;
    }
    errors[field] =
      FIELD_ERROR_CODES.has(code) || code === 'time_format'
        ? (`errors:${code}` as `errors:${ErrorMessageKey}`)
        : 'errors:validation_error';
  }
  return errors;
}

/**
 * A 422 on the whole body (`session_unchanged`, `series_unchanged`), as an
 * `errors` key, or null.
 */
export function bodyError(
  fieldErrors: readonly FieldError[],
): 'errors:session_unchanged' | 'errors:series_unchanged' | null {
  for (const { path, code } of fieldErrors) {
    if (path.length === 1 && path[0] === 'body') {
      if (code === 'session_unchanged' || code === 'series_unchanged') {
        return `errors:${code}`;
      }
    }
  }
  return null;
}

// --- The scope of an edit or a cancellation (C2, C3) --------------------------

export type ScopeMessage =
  'scheduling.series.validation.fromDateRequired' | `errors:${ErrorMessageKey}`;

/** The 422 on `from_date` (from_date_in_past, from_date_required, …). */
export function fromDateServerError(
  fieldErrors: readonly FieldError[],
): ScopeMessage | undefined {
  const found = fieldErrors.find(
    ({ path }) => path[path.length - 1] === 'from_date',
  );
  if (!found) {
    return undefined;
  }
  return found.code === 'from_date_in_past' ||
    found.code === 'from_date_required'
    ? `errors:${found.code}`
    : 'errors:validation_error';
}

// --- Edit series (C2) ---------------------------------------------------------

export interface SeriesEditValues {
  mode: SeriesEditMode;
  /** YYYY-MM-DD; used with `following`. */
  fromDate: string;
  start: string;
  end: string;
  classroomId: string;
  recurrence: RecurrenceValues;
}

export interface SeriesEditErrors {
  fromDate?: ScopeMessage;
  slot: SlotErrors;
  recurrence: RecurrenceErrors;
}

/** The dialog pre-filled from the series (a COUNT rule ends "On date"). */
export function seriesEditValues(
  series: ClassSeriesDetail,
  fromDate: string,
): SeriesEditValues {
  return {
    mode: 'following',
    fromDate,
    start: series.start_time,
    end: endTime(series.start_time, series.duration_minutes),
    classroomId: series.classroom?.id ?? '',
    recurrence: recurrenceValuesOf(
      series.recurrence,
      series.last_occurrence_on,
    ),
  };
}

export function hasErrors(errors: SeriesEditErrors): boolean {
  return (
    errors.fromDate !== undefined ||
    Object.keys(errors.slot).length > 0 ||
    Object.keys(errors.recurrence).length > 0
  );
}

/**
 * The client checks: the date for `following`, the times, the rule (its end
 * date against the change's start date, or `defaultFrom` for `all`).
 */
export function validateSeriesEdit(
  values: SeriesEditValues,
  defaultFrom: string,
): SeriesEditErrors {
  const errors: SeriesEditErrors = {
    slot: validateTimes(values.start, values.end),
    recurrence: validateRecurrence(
      values.recurrence,
      values.mode === 'following' ? values.fromDate : defaultFrom,
    ),
  };
  if (values.mode === 'following' && !DATE.test(values.fromDate)) {
    errors.fromDate = 'scheduling.series.validation.fromDateRequired';
  }
  return errors;
}

/** The edit body: the full new rule as structured fields (never RRULE). */
export function seriesEditBody(
  values: SeriesEditValues,
  version: number,
): ClassSeriesEdit {
  return {
    mode: values.mode,
    from_date: values.mode === 'following' ? values.fromDate : null,
    version,
    start_time: values.start,
    duration_minutes: durationMinutes(values.start, values.end) ?? 0,
    classroom_id: values.classroomId || null,
    recurrence: recurrenceBody(values.recurrence),
  };
}

export function seriesEditServerErrors(
  fieldErrors: readonly FieldError[],
): SeriesEditErrors {
  return {
    fromDate: fromDateServerError(fieldErrors),
    slot: slotServerErrors(fieldErrors),
    recurrence: recurrenceServerErrors(fieldErrors),
  };
}

// --- Cancel future occurrences (C3) -------------------------------------------

export const CANCEL_REASON_MAX_LENGTH = 1000;

export interface CancelValues {
  mode: SeriesEditMode;
  fromDate: string;
  reason: string;
}

export type ReasonMessage =
  | 'scheduling.series.validation.reasonRequired'
  | 'scheduling.series.validation.reasonTooLong'
  | 'errors:validation_error';

export interface CancelErrors {
  fromDate?: ScopeMessage;
  reason?: ReasonMessage;
}

export function validateCancel(values: CancelValues): CancelErrors {
  const errors: CancelErrors = {};
  if (values.mode === 'following' && !DATE.test(values.fromDate)) {
    errors.fromDate = 'scheduling.series.validation.fromDateRequired';
  }
  const reason = values.reason.trim();
  if (!reason) {
    errors.reason = 'scheduling.series.validation.reasonRequired';
  } else if (reason.length > CANCEL_REASON_MAX_LENGTH) {
    errors.reason = 'scheduling.series.validation.reasonTooLong';
  }
  return errors;
}

export function cancelBody(
  values: CancelValues,
  version: number,
): ClassSeriesCancel {
  return {
    mode: values.mode,
    from_date: values.mode === 'following' ? values.fromDate : null,
    reason: values.reason.trim(),
    version,
  };
}

export function cancelServerErrors(
  fieldErrors: readonly FieldError[],
): CancelErrors {
  const errors: CancelErrors = { fromDate: fromDateServerError(fieldErrors) };
  const reason = fieldErrors.find(
    ({ path }) => path[path.length - 1] === 'reason',
  );
  if (reason) {
    errors.reason =
      reason.code === 'reason_required'
        ? 'scheduling.series.validation.reasonRequired'
        : reason.code === 'reason_too_long'
          ? 'scheduling.series.validation.reasonTooLong'
          : 'errors:validation_error';
  }
  return errors;
}

// --- The edit's summary (C2) --------------------------------------------------

/** The non-zero counts of an edit, in the sentence's order. */
export function summaryParts(
  summary: SeriesEditSummary,
): { key: keyof SeriesEditSummary; count: number }[] {
  const order: (keyof SeriesEditSummary)[] = [
    'moved',
    'cancelled',
    'created',
    'skipped_closure',
    'exceptions_reset',
  ];
  return order
    .map((key) => ({ key, count: summary[key] }))
    .filter((part) => part.count > 0);
}

/** The series page of `id` (B1). */
export function seriesPath(id: string): string {
  return `/schedule/series/${encodeURIComponent(id)}`;
}

export function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'version_conflict';
}
