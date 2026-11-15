import { ApiError } from '@/app/api';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import type { PayrollAdjustmentCreate, UnresolvedReason } from './api';
import {
  serverErrors,
  serverMessage,
  ZERO,
  type PayrollMessage,
} from './rules';

// The manual adjustment's form (M7-08b B1, B3), without React: a direction
// and a positive amount sent as one signed amount, a reason, and the
// reference fixed by where the dialog opened (E1). The API decides again.

const V = 'payroll.validation';

/** The API's limit, in code points once trimmed (M7-08c A1). */
export const ADJUSTMENT_REASON_MAX = 1000;

export type AdjustmentDirection = 'pay_more' | 'take_back';

export const ADJUSTMENT_DIRECTIONS: readonly AdjustmentDirection[] = [
  'pay_more',
  'take_back',
];

/** Where the dialog opened (E1): the reference it sends, read-only. */
export type AdjustmentReference =
  | {
      kind: 'line';
      lineId: string;
      /** The line's label, date and amount, as the page shows them. */
      summary: string;
    }
  | {
      kind: 'session';
      sessionId: string;
      serviceDate: string;
      reason: UnresolvedReason;
    }
  | { kind: 'free' };

export interface AdjustmentFormValues {
  direction: AdjustmentDirection | '';
  /** As typed, without a sign. */
  amount: string;
  reason: string;
}

export type AdjustmentField = keyof AdjustmentFormValues;
export type AdjustmentErrors = Partial<Record<AdjustmentField, PayrollMessage>>;

export const EMPTY_ADJUSTMENT_FORM: AdjustmentFormValues = {
  direction: '',
  amount: '',
  reason: '',
};

/** Code points, as the API counts. */
export function reasonLength(reason: string): number {
  return Array.from(reason.trim()).length;
}

export function validateAdjustmentForm(
  values: AdjustmentFormValues,
): AdjustmentErrors {
  const errors: AdjustmentErrors = {};
  if (!values.direction) {
    errors.direction = `${V}.adjustmentDirectionRequired`;
  }
  const amount = normalizeMoneyInput(values.amount);
  if (amount === '') {
    errors.amount = `${V}.amountRequired`;
  } else if (!isMoneyInput(amount)) {
    errors.amount = `${V}.amountInvalid`;
  } else if (ZERO.test(amount)) {
    errors.amount = `${V}.amountPositive`;
  }
  const length = reasonLength(values.reason);
  if (length === 0) {
    errors.reason = `${V}.reasonRequired`;
  } else if (length > ADJUSTMENT_REASON_MAX) {
    errors.reason = `${V}.reasonTooLong`;
  }
  return errors;
}

/** The POST body: the amount signed by the direction, one reference at most. */
export function adjustmentCreateBody(
  values: AdjustmentFormValues,
  reference: AdjustmentReference,
): PayrollAdjustmentCreate {
  const amount = normalizeMoneyInput(values.amount);
  return {
    amount: values.direction === 'take_back' ? `-${amount}` : amount,
    reason: values.reason.trim(),
    ...(reference.kind === 'line' ? { origin_line_id: reference.lineId } : {}),
    ...(reference.kind === 'session'
      ? { class_session_id: reference.sessionId }
      : {}),
  };
}

/** A 422 on the form's fields (B3), or null for the banner. */
export function adjustmentServerErrors(
  error: unknown,
): AdjustmentErrors | null {
  const errors = serverErrors<AdjustmentField>(error, {
    amount: 'amount',
    reason: 'reason',
  });
  if (errors) {
    return errors;
  }
  if (error instanceof ApiError && error.status === 422) {
    // A lone surrogate is refused before the body is read (M7-08c D1).
    if (
      error.code === 'text_invalid' ||
      error.fieldErrors.some((item) => item.code === 'text_invalid')
    ) {
      return { reason: serverMessage('text_invalid') };
    }
  }
  return null;
}

/** The refusals after which the lists on screen are out of date (B3, D2). */
export const REFRESH_AFTER_ADJUSTMENT: readonly string[] = [
  'payroll_line_not_finalized',
  'payroll_work_not_unresolved',
  'payroll_work_before_first_period',
  'payroll_work_settled',
  'payroll_no_period',
  'not_found',
];
