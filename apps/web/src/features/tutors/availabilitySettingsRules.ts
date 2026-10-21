import type { AvailabilitySettingsUpdate, TutorAvailability } from './api';

// Pure helpers of the teaching limits and preferred classrooms form (#197
// C1): the values the form edits, their checks (as the API checks them: a
// daily maximum of 15 min to 24 h, a weekly maximum of 15 min to 168 h and
// not below the daily one, a break of 0 to 240 min, at most 10 rooms) and
// the PATCH body, which holds only what changed. UX only: the API
// validates every request.

/** The API's limits (staff/models.py). */
export const DAILY_LIMIT_MIN = 15;
export const DAILY_LIMIT_MAX = 1440;
export const WEEKLY_LIMIT_MIN = 15;
export const WEEKLY_LIMIT_MAX = 10080;
export const BREAK_MAX = 240;
export const PREFERRED_CLASSROOMS_MAX = 10;

export type LimitName = 'daily' | 'weekly';

export interface LimitValue {
  /** "No limit". */
  none: boolean;
  /** Whole hours and minutes as typed (digits), or empty. */
  hours: string;
  minutes: string;
}

export interface SettingsFormValues {
  daily: LimitValue;
  weekly: LimitValue;
  /** "No break". */
  breakNone: boolean;
  breakMinutes: string;
  /** The chosen classroom ids. */
  rooms: string[];
}

export type SettingsField = 'daily' | 'weekly' | 'break' | 'rooms';

export type SettingsMessageKey =
  | 'tutors.availability.settings.validation.dailyRange'
  | 'tutors.availability.settings.validation.weeklyRange'
  | 'tutors.availability.settings.validation.weeklyBelowDaily'
  | 'tutors.availability.settings.validation.breakRange'
  | 'tutors.availability.settings.validation.tooManyRooms'
  | 'tutors.availability.settings.validation.roomUnavailable'
  | 'tutors.availability.settings.validation.invalid';

export type SettingsErrors = Partial<Record<SettingsField, SettingsMessageKey>>;

function limitValue(minutes: number | null): LimitValue {
  if (minutes === null) {
    return { none: true, hours: '', minutes: '' };
  }
  return {
    none: false,
    hours: String(Math.floor(minutes / 60)),
    minutes: String(minutes % 60),
  };
}

/** The form's values for the stored settings. */
export function settingsValues(
  availability: TutorAvailability,
): SettingsFormValues {
  const { limits } = availability;
  return {
    daily: limitValue(limits.daily_limit_minutes),
    weekly: limitValue(limits.weekly_limit_minutes),
    breakNone: limits.required_break_minutes === null,
    breakMinutes:
      limits.required_break_minutes === null
        ? ''
        : String(limits.required_break_minutes),
    rooms: availability.preferred_classrooms.map((room) => room.id),
  };
}

const DIGITS = /^\d{1,5}$/;

function wholeNumber(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === '') {
    return 0;
  }
  return DIGITS.test(trimmed) ? Number(trimmed) : null;
}

/** A limit in minutes: `null` for "No limit", `NaN` when not a valid
 * duration (both fields empty, not whole numbers, or minutes over 59). */
export function limitMinutes(value: LimitValue): number | null {
  if (value.none) {
    return null;
  }
  const hours = wholeNumber(value.hours);
  const minutes = wholeNumber(value.minutes);
  if (
    hours === null ||
    minutes === null ||
    minutes > 59 ||
    (value.hours.trim() === '' && value.minutes.trim() === '')
  ) {
    return Number.NaN;
  }
  return hours * 60 + minutes;
}

/** The break in minutes: `null` for "No break", `NaN` when not valid. */
export function breakMinutes(values: SettingsFormValues): number | null {
  if (values.breakNone) {
    return null;
  }
  const text = values.breakMinutes.trim();
  return DIGITS.test(text) ? Number(text) : Number.NaN;
}

function inRange(value: number, min: number, max: number): boolean {
  return Number.isInteger(value) && value >= min && value <= max;
}

/** The form's errors (empty when valid). */
export function validateSettings(values: SettingsFormValues): SettingsErrors {
  const errors: SettingsErrors = {};
  const daily = limitMinutes(values.daily);
  const weekly = limitMinutes(values.weekly);
  const pause = breakMinutes(values);
  if (daily !== null && !inRange(daily, DAILY_LIMIT_MIN, DAILY_LIMIT_MAX)) {
    errors.daily = 'tutors.availability.settings.validation.dailyRange';
  }
  if (weekly !== null && !inRange(weekly, WEEKLY_LIMIT_MIN, WEEKLY_LIMIT_MAX)) {
    errors.weekly = 'tutors.availability.settings.validation.weeklyRange';
  } else if (
    weekly !== null &&
    daily !== null &&
    !errors.daily &&
    weekly < daily
  ) {
    errors.weekly = 'tutors.availability.settings.validation.weeklyBelowDaily';
  }
  if (pause !== null && !inRange(pause, 0, BREAK_MAX)) {
    errors.break = 'tutors.availability.settings.validation.breakRange';
  }
  if (values.rooms.length > PREFERRED_CLASSROOMS_MAX) {
    errors.rooms = 'tutors.availability.settings.validation.tooManyRooms';
  }
  return errors;
}

function sameIds(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && [...a].sort().join() === [...b].sort().join();
}

/** The PATCH body of valid values: the loaded `version` and only the
 * fields that differ from the stored settings (`null` clears a limit), or
 * `null` when nothing changed. */
export function settingsBody(
  values: SettingsFormValues,
  availability: TutorAvailability,
): AvailabilitySettingsUpdate | null {
  const { limits } = availability;
  const body: AvailabilitySettingsUpdate = { version: availability.version };
  let changed = false;
  const daily = limitMinutes(values.daily);
  const weekly = limitMinutes(values.weekly);
  const pause = breakMinutes(values);
  if (daily !== limits.daily_limit_minutes) {
    body.daily_limit_minutes = daily;
    changed = true;
  }
  if (weekly !== limits.weekly_limit_minutes) {
    body.weekly_limit_minutes = weekly;
    changed = true;
  }
  if (pause !== limits.required_break_minutes) {
    body.required_break_minutes = pause;
    changed = true;
  }
  const stored = availability.preferred_classrooms.map((room) => room.id);
  if (!sameIds(values.rooms, stored)) {
    body.preferred_classroom_ids = values.rooms;
    changed = true;
  }
  return changed ? body : null;
}

const SERVER_FIELDS: Record<string, SettingsField> = {
  daily_limit_minutes: 'daily',
  weekly_limit_minutes: 'weekly',
  required_break_minutes: 'break',
  preferred_classroom_ids: 'rooms',
};

const SERVER_MESSAGES: Record<string, SettingsMessageKey> = {
  weekly_below_daily:
    'tutors.availability.settings.validation.weeklyBelowDaily',
  classroom_unavailable:
    'tutors.availability.settings.validation.roomUnavailable',
};

/** The form errors of a server 422's field errors (empty when none is
 * about a form field). */
export function serverSettingsErrors(
  fieldErrors: readonly { path: readonly (string | number)[]; code: string }[],
): SettingsErrors {
  const errors: SettingsErrors = {};
  for (const fieldError of fieldErrors) {
    const [where, name] = fieldError.path;
    const field = where === 'body' ? SERVER_FIELDS[String(name)] : undefined;
    if (field) {
      errors[field] =
        SERVER_MESSAGES[fieldError.code] ??
        'tutors.availability.settings.validation.invalid';
    }
  }
  return errors;
}
