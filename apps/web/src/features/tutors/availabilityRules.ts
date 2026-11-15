import type { AvailabilityExceptionCreate, ExceptionKind } from './api';

// Pure helpers of the Availability section (#45 F1): the exception dialog's
// checks (as the API checks them: dates in order and at most 366 days
// apart, both times or none and the end after the start, extra
// availability always with hours, a note of at most 200 characters) and
// the request body. UX only: the API validates every request.

/** The API's limits (staff/models.py). */
export const AVAILABILITY_WINDOWS_MAX = 42;
export const EXCEPTION_MAX_SPAN_DAYS = 366;
export const EXCEPTION_NOTE_MAX_LENGTH = 200;

export interface ExceptionFormValues {
  kind: ExceptionKind;
  /** `YYYY-MM-DD`, or empty. */
  startsOn: string;
  endsOn: string;
  /** Forced off for extra availability. */
  allDay: boolean;
  /** `HH:MM`, or empty; ignored for a whole day. */
  startsAt: string;
  endsAt: string;
  note: string;
}

export type ExceptionField =
  'startsOn' | 'endsOn' | 'startsAt' | 'endsAt' | 'note';

export type ExceptionMessageKey =
  | 'tutors.availability.validation.dateRequired'
  | 'tutors.availability.validation.endsOnAfterStart'
  | 'tutors.availability.validation.rangeTooLong'
  | 'tutors.availability.validation.timeRequired'
  | 'tutors.availability.validation.endsAfterStart'
  | 'tutors.availability.validation.noteTooLong'
  | 'tutors.availability.validation.invalid';

export type ExceptionErrors = Partial<
  Record<ExceptionField, ExceptionMessageKey>
>;

export const EMPTY_EXCEPTION: ExceptionFormValues = {
  kind: 'unavailable',
  startsOn: '',
  endsOn: '',
  allDay: true,
  startsAt: '',
  endsAt: '',
  note: '',
};

const LOCAL_DATE = /^\d{4}-\d{2}-\d{2}$/;
const LOCAL_TIME = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const DAY_MS = 24 * 60 * 60 * 1000;

/** Whether the hours are used: extra availability always has them. */
export function usesHours(values: ExceptionFormValues): boolean {
  return values.kind === 'available' || !values.allDay;
}

function daysBetween(start: string, end: string): number {
  return Math.round((Date.parse(end) - Date.parse(start)) / DAY_MS);
}

/** The errors of the dialog's fields (empty when valid). */
export function validateException(
  values: ExceptionFormValues,
): ExceptionErrors {
  const errors: ExceptionErrors = {};
  const startOk = LOCAL_DATE.test(values.startsOn);
  const endOk = LOCAL_DATE.test(values.endsOn);
  if (!startOk) {
    errors.startsOn = 'tutors.availability.validation.dateRequired';
  }
  if (!endOk) {
    errors.endsOn = 'tutors.availability.validation.dateRequired';
  }
  if (startOk && endOk) {
    // YYYY-MM-DD strings compare like the dates they hold.
    if (values.endsOn < values.startsOn) {
      errors.endsOn = 'tutors.availability.validation.endsOnAfterStart';
    } else if (
      daysBetween(values.startsOn, values.endsOn) > EXCEPTION_MAX_SPAN_DAYS
    ) {
      errors.endsOn = 'tutors.availability.validation.rangeTooLong';
    }
  }
  if (usesHours(values)) {
    const startTimeOk = LOCAL_TIME.test(values.startsAt);
    const endTimeOk = LOCAL_TIME.test(values.endsAt);
    if (!startTimeOk) {
      errors.startsAt = 'tutors.availability.validation.timeRequired';
    }
    if (!endTimeOk) {
      errors.endsAt = 'tutors.availability.validation.timeRequired';
    }
    if (startTimeOk && endTimeOk && values.endsAt <= values.startsAt) {
      errors.endsAt = 'tutors.availability.validation.endsAfterStart';
    }
  }
  if (values.note.trim().length > EXCEPTION_NOTE_MAX_LENGTH) {
    errors.note = 'tutors.availability.validation.noteTooLong';
  }
  return errors;
}

/** The POST body of valid values (no times for a whole day). */
export function exceptionBody(
  values: ExceptionFormValues,
): AvailabilityExceptionCreate {
  const note = values.note.trim();
  return {
    kind: values.kind,
    starts_on: values.startsOn,
    ends_on: values.endsOn,
    ...(usesHours(values)
      ? { starts_at: values.startsAt, ends_at: values.endsAt }
      : {}),
    ...(note ? { note } : {}),
  };
}

const SERVER_FIELDS: Record<string, ExceptionField> = {
  starts_on: 'startsOn',
  ends_on: 'endsOn',
  starts_at: 'startsAt',
  ends_at: 'endsAt',
  note: 'note',
};

/** The dialog field of a server field error's body path, if any. */
export function exceptionFieldOf(
  path: readonly (string | number)[],
): ExceptionField | null {
  return path[0] === 'body' ? (SERVER_FIELDS[String(path[1])] ?? null) : null;
}
