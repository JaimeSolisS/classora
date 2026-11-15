import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { parseLocalDate } from '@/lib/time';
import { OPEN_STATUSES, type ChargeItem } from '../accounts/api';
import { apiCents } from '../accounts/cents';

// Pure helpers of the due-date dialogs (#382 C, D): who may change a
// charge's due date, the offset and due-date forms' checks, and where a
// refusal goes. UX only: the API decides (#97: the charge and the account
// version are checked again under the account lock).

/** The API's limits (apps/api/app/modules/billing/schemas.py). */
export const DUE_OFFSET_MAX = 60;
export const REASON_MAX = 500;
export const DUE_DATE_MIN = '2000-01-01';
export const DUE_DATE_MAX = '2099-12-31';

/** A message: a `common` key, or `errors:<key>`. */
export type DueDateMessage = string;

function serverMessage(code: string): DueDateMessage {
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

/** D2: only a charge with something still open (pending, partially paid or overdue). */
export function canChangeDueDate(charge: Pick<ChargeItem, 'status' | 'open'>) {
  return OPEN_STATUSES.includes(charge.status) && apiCents(charge.open) > 0;
}

/** D1: the due date was moved from the posted one. */
export function dueDateChanged(
  charge: Pick<ChargeItem, 'due_date' | 'original_due_date'>,
): boolean {
  return charge.due_date !== charge.original_due_date;
}

// ------------------------------------------------------------ offset

/** The typed offset's problem, or undefined for a whole number 0–60. */
export function offsetError(text: string): DueDateMessage | undefined {
  const trimmed = text.trim();
  if (!/^\d{1,3}$/.test(trimmed) || Number(trimmed) > DUE_OFFSET_MAX) {
    return 'billing.dueDates.validation.offsetInvalid';
  }
  return undefined;
}

/** A 422 on `due_offset_days`, as the field's message; null otherwise. */
export function offsetServerError(error: unknown): DueDateMessage | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const onField = error.fieldErrors.find(
    (fieldError) =>
      fieldError.path[0] === 'body' && fieldError.path[1] === 'due_offset_days',
  );
  return onField ? serverMessage(onField.code) : null;
}

// ---------------------------------------------------------- due date

export interface DueDateFormValues {
  dueDate: string;
  reason: string;
}

export type DueDateField = keyof DueDateFormValues;
export type DueDateErrors = Partial<Record<DueDateField, DueDateMessage>>;

function isRealDay(value: string): boolean {
  try {
    parseLocalDate(value);
    return true;
  } catch {
    return false;
  }
}

export function validateDueDateForm(values: DueDateFormValues): DueDateErrors {
  const errors: DueDateErrors = {};
  if (!isRealDay(values.dueDate)) {
    errors.dueDate = 'billing.dueDates.validation.dateRequired';
  } else if (values.dueDate < DUE_DATE_MIN || values.dueDate > DUE_DATE_MAX) {
    errors.dueDate = 'errors:date_out_of_range';
  }
  const reason = values.reason.trim();
  if (reason === '') {
    errors.reason = 'billing.dueDates.validation.reasonRequired';
  } else if (reason.length > REASON_MAX) {
    errors.reason = 'billing.dueDates.validation.reasonTooLong';
  }
  return errors;
}

/** D4: the new date is before the center's today (allowed, with a warning). */
export function becomesOverdue(dueDate: string, today: string): boolean {
  return isRealDay(dueDate) && dueDate < today;
}

/**
 * Where a refusal of the due-date change goes (D3): field errors (a 422 on
 * the fields, `409 due_date_unchanged` on the date), or `settled`/`gone`
 * (`409 charge_settled`, `404`: an alert and a refetch), or an alert.
 */
export type DueDateRefusal =
  | { kind: 'fields'; errors: DueDateErrors }
  | { kind: 'settled' | 'gone' | 'other' };

export function dueDateRefusal(error: unknown): DueDateRefusal {
  if (!(error instanceof ApiError)) return { kind: 'other' };
  if (error.code === 'due_date_unchanged') {
    return {
      kind: 'fields',
      errors: { dueDate: 'errors:due_date_unchanged' },
    };
  }
  if (error.code === 'charge_settled') return { kind: 'settled' };
  if (error.status === 404) return { kind: 'gone' };
  if (error.status === 422) {
    const errors: DueDateErrors = {};
    for (const fieldError of error.fieldErrors) {
      const [where, key] = fieldError.path;
      if (where !== 'body') continue;
      if (key === 'due_date' && !errors.dueDate) {
        errors.dueDate = serverMessage(fieldError.code);
      } else if (key === 'reason' && !errors.reason) {
        errors.reason = serverMessage(fieldError.code);
      }
    }
    if (Object.keys(errors).length > 0) return { kind: 'fields', errors };
  }
  return { kind: 'other' };
}
