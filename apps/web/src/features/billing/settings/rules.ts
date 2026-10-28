import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { parseLocalDate, type LocalDate } from '@/lib/time';
import type { BillingSettings, BillingSettingsUpdate } from './api';

// The "Change billing" dialog's rules (#366 B2, B3), without React: the
// form starts from the current date (or today while billing is off), "Turn
// billing off" sends null, the date must be a real day from 2000-01-01 to
// 2099-12-31 (the API's range, #91), a date before today is only a
// warning, and a 422 on billing_starts_on lands on the date.

export type BillingMode = 'on' | 'off';

export interface BillingFormValues {
  mode: BillingMode;
  /** `YYYY-MM-DD`, or empty. */
  startsOn: string;
}

/** A `billing.settings.validation` key, or a server code (`errors:` key). */
export type BillingDateError =
  | 'billing.settings.validation.dateRequired'
  | 'billing.settings.validation.dateRange'
  | `errors:${string}`;

export const MIN_START_DATE = '2000-01-01';
export const MAX_START_DATE = '2099-12-31';

export function initialBillingForm(
  settings: Pick<BillingSettings, 'billing_starts_on'>,
  today: LocalDate,
): BillingFormValues {
  return { mode: 'on', startsOn: settings.billing_starts_on ?? today };
}

function isRealDay(value: string): boolean {
  try {
    parseLocalDate(value);
    return true;
  } catch {
    return false;
  }
}

/** The date's error, or undefined (turning billing off needs no date). */
export function validateBillingForm(
  values: BillingFormValues,
): BillingDateError | undefined {
  if (values.mode === 'off') {
    return undefined;
  }
  if (values.startsOn === '') {
    return 'billing.settings.validation.dateRequired';
  }
  if (
    !isRealDay(values.startsOn) ||
    values.startsOn < MIN_START_DATE ||
    values.startsOn > MAX_START_DATE
  ) {
    return 'billing.settings.validation.dateRange';
  }
  return undefined;
}

/**
 * Whether to warn that past sessions will be charged (#366 B2): billing on
 * from a valid date before the center's today.
 */
export function startsInThePast(
  values: BillingFormValues,
  today: LocalDate,
): boolean {
  return (
    values.mode === 'on' &&
    validateBillingForm(values) === undefined &&
    values.startsOn < today
  );
}

/** The PATCH body: both keys, `null` turns billing off. */
export function billingUpdateBody(
  values: BillingFormValues,
  version: number,
): BillingSettingsUpdate {
  return {
    version,
    billing_starts_on: values.mode === 'off' ? null : values.startsOn,
  };
}

/** A 422's message for the date, or null when it isn't about the date. */
export function billingServerError(error: unknown): BillingDateError | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  const onDate = error.fieldErrors.find(
    (fieldError) =>
      fieldError.path[0] === 'body' &&
      fieldError.path[1] === 'billing_starts_on',
  );
  const code =
    onDate?.code ??
    (error.code === 'date_out_of_range' ? error.code : undefined);
  if (code === undefined) {
    return null;
  }
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

export function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'version_conflict';
}
