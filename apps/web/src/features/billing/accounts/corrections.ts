import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import { addDays, type LocalDate } from '@/lib/time';
import type {
  ManualAdjustmentCreate,
  ManualChargeCreate,
  PaymentReversalCreate,
  RefundCreate,
} from './api';
import { centsOf, type Cents } from './cents';
import { REFERENCE_MAX, type AccountMessage } from './rules';

// Pure helpers of the correction dialogs (#386; #100 C1-C4): each form's
// values, checks, body and 422 mapping, and which 409 codes reload what.
// UX only: the API decides (every rule is checked again under the account
// lock).

/** The API's limits (ManualChargeCreate, ManualAdjustmentCreate, …). */
export const DESCRIPTION_MAX = 200;
export const REASON_MAX = 500;
/** The earliest date the API takes, and how far ahead a manual charge goes. */
export const DATE_MIN = '2000-01-01';
export const MANUAL_CHARGE_HORIZON_DAYS = 366;

function serverMessage(code: string): AccountMessage {
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

/** A required positive money amount's problem, if any. */
function amountProblem(text: string): AccountMessage | undefined {
  if (text === '') return 'billing.accounts.validation.amountRequired';
  if (!isMoneyInput(text)) return 'errors:money_invalid';
  if (centsOf(text) === 0) return 'errors:amount_not_positive';
  return undefined;
}

function reasonProblem(reason: string): AccountMessage | undefined {
  const trimmed = reason.trim();
  if (trimmed === '') return 'billing.accounts.validation.reasonRequired';
  if (trimmed.length > REASON_MAX) return 'errors:too_long';
  return undefined;
}

/** The first field error of a 422 per form field (`BODY_FIELDS` maps them). */
function fieldErrors<F extends string>(
  error: unknown,
  bodyFields: Readonly<Record<string, F>>,
): Partial<Record<F, AccountMessage>> | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const errors: Partial<Record<F, AccountMessage>> = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    const field = where === 'body' ? bodyFields[String(key)] : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

export function anyError(errors: Readonly<Record<string, unknown>>): boolean {
  return Object.values(errors).some((value) => value !== undefined);
}

/** Whether `error` is one of these problem codes. */
export function isCode(error: unknown, ...codes: string[]): boolean {
  return error instanceof ApiError && codes.includes(error.code);
}

/**
 * The password dialog was cancelled (#26): apiCall rethrows the
 * `reauth_required`; the form stays open without an error.
 */
export function isCancelledReauth(error: unknown): boolean {
  return isCode(error, 'reauth_required');
}

// ------------------------------------------------- A1: a manual charge

export interface ManualChargeValues {
  studentId: string;
  description: string;
  amount: string;
  serviceDate: string;
  dueDate: string;
}

export type ManualChargeField = keyof ManualChargeValues;
export type ManualChargeErrors = Partial<
  Record<ManualChargeField, AccountMessage>
>;

export const MANUAL_CHARGE_FIELD_ORDER: readonly ManualChargeField[] = [
  'studentId',
  'description',
  'amount',
  'serviceDate',
  'dueDate',
];

/** Today as the service date, and the due date the service date (choice). */
export function emptyManualCharge(
  today: LocalDate,
  studentId = '',
): ManualChargeValues {
  return {
    studentId,
    description: '',
    amount: '',
    serviceDate: today,
    dueDate: today,
  };
}

/** The latest date a manual charge takes (center today + 366 days). */
export function manualChargeMaxDate(today: LocalDate): LocalDate {
  return addDays(today, MANUAL_CHARGE_HORIZON_DAYS);
}

function dateProblem(
  value: string,
  max: LocalDate,
): AccountMessage | undefined {
  if (value === '') return 'billing.accounts.validation.dateRequired';
  if (value < DATE_MIN || value > max) return 'errors:date_out_of_range';
  return undefined;
}

export function validateManualCharge(
  values: ManualChargeValues,
  today: LocalDate,
): ManualChargeErrors {
  const errors: ManualChargeErrors = {};
  if (values.studentId === '') {
    errors.studentId = 'billing.accounts.validation.studentRequired';
  }
  const description = values.description.trim();
  if (description === '') {
    errors.description = 'billing.accounts.validation.descriptionRequired';
  } else if (description.length > DESCRIPTION_MAX) {
    errors.description = 'errors:too_long';
  }
  const amount = amountProblem(normalizeMoneyInput(values.amount));
  if (amount) errors.amount = amount;
  const max = manualChargeMaxDate(today);
  const service = dateProblem(values.serviceDate, max);
  if (service) errors.serviceDate = service;
  const due = dateProblem(values.dueDate, max);
  if (due) errors.dueDate = due;
  return errors;
}

export function manualChargeBody(
  values: ManualChargeValues,
): ManualChargeCreate {
  return {
    student_id: values.studentId,
    description: values.description.trim(),
    amount: normalizeMoneyInput(values.amount),
    service_date: values.serviceDate,
    due_date: values.dueDate,
  };
}

export function manualChargeServerErrors(
  error: unknown,
): ManualChargeErrors | null {
  return fieldErrors<ManualChargeField>(error, {
    student_id: 'studentId',
    description: 'description',
    amount: 'amount',
    service_date: 'serviceDate',
    due_date: 'dueDate',
  });
}

// ----------------------------------------- A3: adjust a posted charge

export type AdjustmentDirection = 'credit' | 'debit';
export const ADJUSTMENT_DIRECTIONS: readonly AdjustmentDirection[] = [
  'credit',
  'debit',
];

export interface AdjustmentValues {
  direction: AdjustmentDirection;
  amount: string;
  reason: string;
}

export type AdjustmentField = 'amount' | 'reason';
export type AdjustmentErrors = Partial<Record<AdjustmentField, AccountMessage>>;

export const EMPTY_ADJUSTMENT: AdjustmentValues = {
  direction: 'credit',
  amount: '',
  reason: '',
};

/** A credit can't take the charge below 0.00 (I1; the server checks too). */
export function validateAdjustment(
  values: AdjustmentValues,
  net: Cents,
): AdjustmentErrors {
  const errors: AdjustmentErrors = {};
  const text = normalizeMoneyInput(values.amount);
  const amount = amountProblem(text);
  if (amount) {
    errors.amount = amount;
  } else if (values.direction === 'credit' && centsOf(text)! > net) {
    errors.amount = 'billing.accounts.adjust.aboveNet';
  }
  const reason = reasonProblem(values.reason);
  if (reason) errors.reason = reason;
  return errors;
}

/** The signed amount: a credit is negative, a debit positive (#100 R3). */
export function adjustmentBody(
  values: AdjustmentValues,
): ManualAdjustmentCreate {
  const text = normalizeMoneyInput(values.amount);
  return {
    amount: values.direction === 'credit' ? `-${text}` : text,
    reason: values.reason.trim(),
  };
}

export function adjustmentServerErrors(
  error: unknown,
): AdjustmentErrors | null {
  return fieldErrors<AdjustmentField>(error, {
    amount: 'amount',
    reason: 'reason',
  });
}

/** The 409s after which the charge (and the account) are read again. */
export const ADJUSTMENT_STALE_CODES = [
  'charge_net_negative',
  'package_purchase_not_adjustable',
] as const;

// ----------------------------------------------- B2: reverse a payment

export interface ReversalValues {
  reason: string;
}

export type ReversalErrors = Partial<Record<'reason', AccountMessage>>;

export function validateReversal(values: ReversalValues): ReversalErrors {
  const reason = reasonProblem(values.reason);
  return reason ? { reason } : {};
}

export function reversalBody(values: ReversalValues): PaymentReversalCreate {
  return { reason: values.reason.trim() };
}

export function reversalServerErrors(error: unknown): ReversalErrors | null {
  return fieldErrors<'reason'>(error, { reason: 'reason' });
}

export const REVERSAL_STALE_CODES = [
  'payment_already_reversed',
  'payment_has_refunds',
] as const;

// ----------------------------------------------- B3: refund a payment

export interface RefundValues {
  amount: string;
  refundedOn: string;
  method: string;
  reference: string;
  reason: string;
}

export type RefundField = keyof RefundValues;
export type RefundErrors = Partial<Record<RefundField, AccountMessage>>;

export const REFUND_FIELD_ORDER: readonly RefundField[] = [
  'amount',
  'refundedOn',
  'method',
  'reference',
  'reason',
];

/** The whole unallocated amount, refunded today; no method preselected. */
export function emptyRefund(
  today: LocalDate,
  unallocated: string,
): RefundValues {
  return {
    amount: unallocated,
    refundedOn: today,
    method: '',
    reference: '',
    reason: '',
  };
}

export function validateRefund(
  values: RefundValues,
  limits: { unallocated: Cents; receivedOn: string; today: LocalDate },
): RefundErrors {
  const errors: RefundErrors = {};
  const text = normalizeMoneyInput(values.amount);
  const amount = amountProblem(text);
  if (amount) {
    errors.amount = amount;
  } else if (centsOf(text)! > limits.unallocated) {
    errors.amount = 'billing.accounts.refund.aboveUnallocated';
  }
  if (values.refundedOn === '') {
    errors.refundedOn = 'billing.accounts.validation.dateRequired';
  } else if (values.refundedOn > limits.today) {
    errors.refundedOn = 'errors:refunded_on_in_future';
  } else if (values.refundedOn < limits.receivedOn) {
    errors.refundedOn = 'errors:refunded_on_before_payment';
  }
  if (values.method === '') {
    errors.method = 'billing.accounts.validation.methodRequired';
  }
  if (values.reference.trim().length > REFERENCE_MAX) {
    errors.reference = 'errors:too_long';
  }
  const reason = reasonProblem(values.reason);
  if (reason) errors.reason = reason;
  return errors;
}

export function refundBody(values: RefundValues): RefundCreate {
  const reference = values.reference.trim();
  return {
    amount: normalizeMoneyInput(values.amount),
    refunded_on: values.refundedOn,
    payment_method_code: values.method,
    reference: reference === '' ? null : reference,
    reason: values.reason.trim(),
  };
}

export function refundServerErrors(error: unknown): RefundErrors | null {
  return fieldErrors<RefundField>(error, {
    amount: 'amount',
    refunded_on: 'refundedOn',
    payment_method_code: 'method',
    reference: 'reference',
    reason: 'reason',
  });
}

export const REFUND_STALE_CODES = [
  'payment_reversed',
  'refund_exceeds_remainder',
] as const;

/** Why "Reverse payment" isn't offered, if it isn't (B2). */
export function reversalBlock(payment: {
  is_reversed: boolean;
  refunds: readonly unknown[];
}): 'reversed' | 'hasRefunds' | null {
  if (payment.is_reversed) return 'reversed';
  if (payment.refunds.length > 0) return 'hasRefunds';
  return null;
}

/** Whether "Refund" is offered (B3): not reversed, something unallocated. */
export function canRefund(payment: {
  is_reversed: boolean;
  unallocated_amount: string;
}): boolean {
  return !payment.is_reversed && (centsOf(payment.unallocated_amount) ?? 0) > 0;
}
