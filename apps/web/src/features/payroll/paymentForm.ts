import {
  apiCents,
  centsOf,
  centsText,
  type Cents,
} from '@/features/billing/accounts/cents';
import { canAccess } from '@/features/auth/access';
import type { SessionUser } from '@/features/auth/session';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import type { LocalDate } from '@/lib/time';
import type { PayrollPaymentCreate, PayrollPeriodDetail } from './api';
import { detailString, isCode, isUnknownResult, isUuid } from './periodRules';
import { isRealDay, serverErrors, type PayrollMessage } from './rules';

// A payroll payment's form and the payments section's rules (M7-09b), without
// React: the dialog's fields and their client checks, which mirror the API
// (#112 B2, E4); the API stays the authority. What's left to pay is read from
// the period's strings as whole cents, never through a float.

const V = 'payroll.validation';

/** The API's limits, in code points once trimmed (#112 B2, C3). */
export const PAYMENT_REFERENCE_MAX = 100;
export const PAYMENT_NOTE_MAX = 1000;
export const REVERSAL_REASON_MAX = 1000;

export interface PaymentFormValues {
  paidOn: string;
  /** As typed. */
  amount: string;
  methodCode: string;
  reference: string;
  note: string;
}

export type PaymentField = keyof PaymentFormValues;
export type PaymentErrors = Partial<Record<PaymentField, PayrollMessage>>;

export const PAYMENT_FIELD_ORDER: readonly PaymentField[] = [
  'paidOn',
  'amount',
  'methodCode',
  'reference',
  'note',
];

/** Code points, as the API counts. */
export function textLength(value: string): number {
  return Array.from(value.trim()).length;
}

/** What's left to pay of a finalized period, as the API's decimal string;
 * null when the period isn't finalized. */
export function remainingOf(
  period: Pick<PayrollPeriodDetail, 'total_amount' | 'paid_total'>,
): string | null {
  if (period.total_amount === null) return null;
  const left =
    apiCents(period.total_amount) - apiCents(period.paid_total ?? '0.00');
  return centsText(Math.max(left, 0) as Cents);
}

/** The dialog's first values: the center's today and all that's left. */
export function emptyPaymentForm(
  today: LocalDate,
  remaining: string,
): PaymentFormValues {
  return {
    paidOn: today,
    amount: remaining,
    methodCode: '',
    reference: '',
    note: '',
  };
}

export function validatePaymentForm(
  values: PaymentFormValues,
  limits: {
    startsOn: string;
    today: LocalDate;
    /** What's left; null skips the cap (the resend of a lost attempt,
     * QA #474 D1: the refetch already counts the payment it may have
     * stored, and the server replays the same key). */
    remaining: string | null;
  },
): PaymentErrors {
  const errors: PaymentErrors = {};
  if (!isRealDay(values.paidOn)) {
    errors.paidOn = `${V}.dateRequired`;
  } else if (values.paidOn > limits.today) {
    errors.paidOn = `${V}.paidOnFuture`;
  } else if (values.paidOn < limits.startsOn) {
    errors.paidOn = `${V}.paidOnBeforePeriod`;
  }
  const amount = normalizeMoneyInput(values.amount);
  const cents = isMoneyInput(amount) ? centsOf(amount) : null;
  if (amount === '') {
    errors.amount = `${V}.amountRequired`;
  } else if (cents === null) {
    errors.amount = `${V}.amountInvalid`;
  } else if (cents <= 0) {
    errors.amount = `${V}.amountPositive`;
  } else if (limits.remaining !== null && cents > apiCents(limits.remaining)) {
    errors.amount = `${V}.amountAboveRemaining`;
  }
  if (values.methodCode === '') {
    errors.methodCode = `${V}.methodRequired`;
  }
  const reference = textLength(values.reference);
  if (reference === 0) {
    errors.reference = `${V}.referenceRequired`;
  } else if (reference > PAYMENT_REFERENCE_MAX) {
    errors.reference = `${V}.referenceTooLong`;
  }
  if (textLength(values.note) > PAYMENT_NOTE_MAX) {
    errors.note = `${V}.reasonTooLong`;
  }
  return errors;
}

/** The POST body: trimmed texts, a blank note as null, the amount a string. */
export function paymentCreateBody(
  values: PaymentFormValues,
): PayrollPaymentCreate {
  const note = values.note.trim();
  return {
    paid_on: values.paidOn,
    amount: normalizeMoneyInput(values.amount),
    method_code: values.methodCode,
    reference: values.reference.trim(),
    note: note === '' ? null : note,
  };
}

/** A 422 on the form's fields (B4), or null for the alert. */
export function paymentServerErrors(error: unknown): PaymentErrors | null {
  const errors = serverErrors<PaymentField>(error, {
    paid_on: 'paidOn',
    amount: 'amount',
    method_code: 'methodCode',
    reference: 'reference',
    note: 'note',
  });
  if (errors) return errors;
  if (isCode(error, 'text_invalid')) {
    // A lone surrogate is refused before the body is read (#112 B2).
    return { reference: `errors:text_invalid` };
  }
  return null;
}

/** `409 payroll_payment_exceeds_balance`'s `details.remaining`, or null. */
export function exceedsRemaining(error: unknown): string | null {
  if (!isCode(error, 'payroll_payment_exceeds_balance')) return null;
  const remaining = detailString(error, 'remaining');
  return remaining !== null && centsOf(remaining) !== null ? remaining : null;
}

/** `409 payroll_payment_duplicate`'s `details.payment_id`, or null. */
export function duplicatePaymentId(error: unknown): string | null {
  if (!isCode(error, 'payroll_payment_duplicate')) return null;
  const id = detailString(error, 'payment_id');
  return id !== null && isUuid(id) ? id.toLowerCase() : null;
}

/** The refusals after which the payments or the period on screen moved. */
export const REFETCH_AFTER_PAYMENT: readonly string[] = [
  'payroll_payment_exceeds_balance',
  'payroll_payment_duplicate',
  'payroll_period_not_finalized',
  'payroll_payment_already_reversed',
  'not_found',
];

export type PaymentDialogOutcome = 'saved' | 'cancelled';

/** The worst failure of a dialog's attempts: `unknown` after a lost
 * response (the row may be stored), `refused` after a settled refusal. */
export type AttemptFailure = 'none' | 'refused' | 'unknown';

export function worse(
  current: AttemptFailure,
  caught: unknown,
): AttemptFailure {
  return current === 'unknown' || isUnknownResult(caught)
    ? 'unknown'
    : 'refused';
}

export function validateReversalReason(reason: string): PayrollMessage | null {
  const length = textLength(reason);
  if (length === 0) return `${V}.reasonRequired`;
  if (length > REVERSAL_REASON_MAX) return `${V}.reasonTooLong`;
  return null;
}

/** A reversal's 422 on the reason, or null for the alert. */
export function reversalServerError(error: unknown): PayrollMessage | null {
  const errors = serverErrors<'reason'>(error, { reason: 'reason' });
  if (errors?.reason) return errors.reason;
  if (isCode(error, 'text_invalid')) return 'errors:text_invalid';
  return null;
}

/** What the user may do in the payments section (A1, A2). */
export interface PaymentCapabilities {
  /** payroll.payment.record, not a non-Owner's own pay, not paid yet. */
  record: boolean;
  /** finance.refund.approve, not a non-Owner's own pay. */
  reverse: boolean;
}

/** Who may log or reverse a payment of this period (A1, A2). */
export function paymentCapabilities(
  user: SessionUser | null,
  period: Pick<PayrollPeriodDetail, 'tutor_user_id' | 'status' | 'paid_state'>,
): PaymentCapabilities {
  const ownPay =
    user !== null &&
    period.tutor_user_id === user.id &&
    !user.roles.includes('owner');
  const finalized = period.status === 'finalized';
  return {
    record:
      finalized &&
      period.paid_state !== 'paid' &&
      !ownPay &&
      canAccess(user, { permission: 'payroll.payment.record' }),
    reverse:
      finalized &&
      !ownPay &&
      canAccess(user, { permission: 'finance.refund.approve' }),
  };
}
