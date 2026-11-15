import { canAccess } from '@/features/auth/access';
import type { SessionUser } from '@/features/auth/session';
import {
  apiCents,
  centsOf,
  centsText,
  type Cents,
} from '@/features/billing/accounts/cents';
import { detailString, isCode, isUuid } from '@/features/payroll/periodRules';
import { isRealDay, serverErrors } from '@/features/payroll/rules';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import type { LocalDate } from '@/lib/time';
import type {
  ExpenseAdjustmentCreate,
  ExpenseCreate,
  ExpenseDetail,
  ExpenseListParams,
  ExpensePaymentCreate,
  ExpenseSort,
  ExpenseStatus,
} from '../api';

// The expense pages' rules (M7-12b), without React: the list's URL, who may
// do what, and the four dialogs' fields, client checks and bodies. The checks
// mirror the API (#115 B2, D2, F2); the API stays the authority. Money is read
// from the API's strings as whole cents, never through a float.

const V = 'finance.expenses.validation';

/** A message: a `common` key, or `errors:<key>`. */
export type ExpenseMessage = string;

/** The API's limits, in code points once trimmed (#115 B2, D2, E2, F2). */
export const DESCRIPTION_MAX = 500;
export const RECEIPT_REFERENCE_MAX = 100;
export const NOTE_MAX = 1000;
export const PAYMENT_REFERENCE_MAX = 100;
export const REASON_MAX = 1000;
export const SEARCH_MAX = 100;

/** Code points, as the API counts, once trimmed. */
export function textLength(value: string): number {
  return Array.from(value.trim()).length;
}

/** One line: no control character (the API's `text_invalid`). */
function isOneLine(value: string): boolean {
  // eslint-disable-next-line no-control-regex
  return !/[\u0000-\u001f\u007f]/.test(value);
}

// ------------------------------------------------------------- the list

export const EXPENSES_PATH = '/expenses/records';
export const expensePath = (id: string) => `${EXPENSES_PATH}/${id}`;

export const EXPENSE_STATUSES: readonly ExpenseStatus[] = [
  'unpaid',
  'partially_paid',
  'overdue',
  'paid',
  'void',
];
export const EXPENSE_SORTS: readonly ExpenseSort[] = ['-incurred_on', 'due_on'];
export const DEFAULT_SORT: ExpenseSort = '-incurred_on';

/** The list's URL (B2): `status` (repeatable), `category`, `vendor`,
 * `from`, `to`, `q`, `sort` and `page`. Anything invalid is ignored. */
export function expenseListParams(search: URLSearchParams): ExpenseListParams {
  const wanted = new Set(search.getAll('status'));
  const id = (name: string) => {
    const value = search.get(name) ?? '';
    return isUuid(value) ? value.toLowerCase() : '';
  };
  const date = (name: string) => {
    const value = search.get(name) ?? '';
    return isRealDay(value) ? value : '';
  };
  const sort = search.get('sort') ?? '';
  const page = Number.parseInt(search.get('page') ?? '', 10);
  return {
    statuses: EXPENSE_STATUSES.filter((status) => wanted.has(status)),
    categoryId: id('category'),
    vendorId: id('vendor'),
    from: date('from'),
    to: date('to'),
    q: (search.get('q') ?? '').trim().slice(0, SEARCH_MAX),
    sort: EXPENSE_SORTS.find((value) => value === sort) ?? DEFAULT_SORT,
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

/** The URL's search string for `params` (defaults left out). */
export function expenseListSearch(params: ExpenseListParams): string {
  const next = new URLSearchParams();
  for (const status of params.statuses) next.append('status', status);
  if (params.categoryId) next.set('category', params.categoryId);
  if (params.vendorId) next.set('vendor', params.vendorId);
  if (params.from) next.set('from', params.from);
  if (params.to) next.set('to', params.to);
  if (params.q) next.set('q', params.q);
  if (params.sort !== DEFAULT_SORT) next.set('sort', params.sort);
  if (params.page > 1) next.set('page', String(params.page));
  return next.toString();
}

/** A filter applies: an empty page is "no match". */
export function isFiltered(params: ExpenseListParams): boolean {
  return (
    params.statuses.length > 0 ||
    params.categoryId !== '' ||
    params.vendorId !== '' ||
    params.from !== '' ||
    params.to !== '' ||
    params.q !== ''
  );
}

/** `to` before `from`: the API's `range_invalid`, checked before asking. */
export function isRangeInvalid(params: ExpenseListParams): boolean {
  return params.from !== '' && params.to !== '' && params.to < params.from;
}

// ------------------------------------------------------------- who may

export interface ExpenseCapabilities {
  /** "Nuevo gasto", "Registrar pago", "Ajustar", "Anular" (A3). */
  manage: boolean;
  /** "Revertir": finance.refund.approve and expense.view (#115 E6). */
  reverse: boolean;
}

export function expenseCapabilities(
  user: SessionUser | null,
): ExpenseCapabilities {
  return {
    manage: canAccess(user, { permission: 'expense.manage' }),
    reverse:
      canAccess(user, { permission: 'finance.refund.approve' }) &&
      canAccess(user, { permission: 'expense.view' }),
  };
}

/** A payment that counts: not reversed. */
export function hasValidPayment(expense: Pick<ExpenseDetail, 'payments'>) {
  return expense.payments.some((payment) => payment.reversal === null);
}

/** What the detail page offers on this expense (A3, E3, edge cases). */
export function expenseActions(
  can: ExpenseCapabilities,
  expense: Pick<ExpenseDetail, 'status' | 'open_amount' | 'payments'>,
) {
  const live = expense.status !== 'void';
  return {
    record: can.manage && live && apiCents(expense.open_amount) > 0,
    adjust: can.manage && live,
    /** Shown to managers on a live expense; enabled only without payments. */
    void: can.manage && live,
    voidBlocked: hasValidPayment(expense),
    reverse: can.reverse && live,
  };
}

// ------------------------------------------------------------- amounts

/** A typed amount above zero as cents, or null. */
function typedCents(value: string): Cents | null {
  const amount = normalizeMoneyInput(value);
  if (!isMoneyInput(amount)) return null;
  const cents = centsOf(amount);
  return cents !== null && cents > 0 ? cents : null;
}

function amountError(value: string): ExpenseMessage | undefined {
  const amount = normalizeMoneyInput(value);
  if (amount === '') return `${V}.amountRequired`;
  if (!isMoneyInput(amount)) return `${V}.amountInvalid`;
  if (typedCents(amount) === null) return `${V}.amountPositive`;
  return undefined;
}

/** The API string of minus `amount` ("1250.00" → "-1250.00"). */
export function negated(amount: string): string {
  return centsText((0 - apiCents(amount)) as Cents);
}

// ------------------------------------------------------------- new expense

export interface ExpenseFormValues {
  categoryId: string;
  vendorId: string;
  description: string;
  amount: string;
  incurredOn: string;
  dueOn: string;
  receiptReference: string;
  note: string;
}

export type ExpenseField = keyof ExpenseFormValues;
export type ExpenseErrors = Partial<Record<ExpenseField, ExpenseMessage>>;

export const EXPENSE_FIELD_ORDER: readonly ExpenseField[] = [
  'categoryId',
  'vendorId',
  'description',
  'amount',
  'incurredOn',
  'dueOn',
  'receiptReference',
  'note',
];

export function emptyExpenseForm(today: LocalDate): ExpenseFormValues {
  return {
    categoryId: '',
    vendorId: '',
    description: '',
    amount: '',
    incurredOn: today,
    dueOn: today,
    receiptReference: '',
    note: '',
  };
}

export function validateExpenseForm(
  values: ExpenseFormValues,
  today: LocalDate,
): ExpenseErrors {
  const errors: ExpenseErrors = {};
  if (values.categoryId === '') errors.categoryId = `${V}.categoryRequired`;
  const description = textLength(values.description);
  if (description === 0) {
    errors.description = `${V}.descriptionRequired`;
  } else if (description > DESCRIPTION_MAX) {
    errors.description = `${V}.descriptionTooLong`;
  } else if (!isOneLine(values.description.trim())) {
    errors.description = 'errors:text_invalid';
  }
  const amount = amountError(values.amount);
  if (amount) errors.amount = amount;
  if (!isRealDay(values.incurredOn)) {
    errors.incurredOn = `${V}.dateRequired`;
  } else if (values.incurredOn > today) {
    errors.incurredOn = 'errors:incurred_on_in_future';
  }
  if (!isRealDay(values.dueOn)) {
    errors.dueOn = `${V}.dateRequired`;
  } else if (isRealDay(values.incurredOn) && values.dueOn < values.incurredOn) {
    errors.dueOn = 'errors:due_on_before_incurred_on';
  }
  const receipt = textLength(values.receiptReference);
  if (receipt > RECEIPT_REFERENCE_MAX) {
    errors.receiptReference = `${V}.receiptTooLong`;
  } else if (!isOneLine(values.receiptReference.trim())) {
    errors.receiptReference = 'errors:text_invalid';
  }
  if (textLength(values.note) > NOTE_MAX) errors.note = `${V}.noteTooLong`;
  return errors;
}

const orNull = (value: string) => {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
};

export function expenseCreateBody(values: ExpenseFormValues): ExpenseCreate {
  return {
    category_id: values.categoryId,
    vendor_id: values.vendorId === '' ? null : values.vendorId,
    description: values.description.trim(),
    amount: normalizeMoneyInput(values.amount),
    incurred_on: values.incurredOn,
    due_on: values.dueOn,
    receipt_reference: orNull(values.receiptReference),
    note: orNull(values.note),
  };
}

/** A refusal on the form's fields (D1, E2), or null for the alert. */
export function expenseServerErrors(error: unknown): ExpenseErrors | null {
  if (isCode(error, 'expense_category_unknown', 'expense_category_inactive')) {
    return { categoryId: `errors:${(error as { code: string }).code}` };
  }
  if (isCode(error, 'vendor_unknown', 'vendor_inactive')) {
    return { vendorId: `errors:${(error as { code: string }).code}` };
  }
  const errors = serverErrors<ExpenseField>(error, {
    category_id: 'categoryId',
    vendor_id: 'vendorId',
    description: 'description',
    amount: 'amount',
    incurred_on: 'incurredOn',
    due_on: 'dueOn',
    receipt_reference: 'receiptReference',
    note: 'note',
  });
  if (errors) return errors;
  if (isCode(error, 'text_invalid')) {
    // A lone surrogate is refused before the body is read.
    return { description: 'errors:text_invalid' };
  }
  return null;
}

/** The refusals after which the category or vendor pickers reload (D1). */
export const RELOAD_PICKERS_AFTER: readonly string[] = [
  'expense_category_unknown',
  'expense_category_inactive',
  'vendor_unknown',
  'vendor_inactive',
];

// ------------------------------------------------------------- payment

export interface PaymentFormValues {
  paidOn: string;
  amount: string;
  methodCode: string;
  reference: string;
  note: string;
}

export type PaymentField = keyof PaymentFormValues;
export type PaymentErrors = Partial<Record<PaymentField, ExpenseMessage>>;

export const PAYMENT_FIELD_ORDER: readonly PaymentField[] = [
  'paidOn',
  'amount',
  'methodCode',
  'reference',
  'note',
];

/** The center's today and all that's open (D2). */
export function emptyPaymentForm(
  today: LocalDate,
  open: string,
): PaymentFormValues {
  return {
    paidOn: today,
    amount: open,
    methodCode: '',
    reference: '',
    note: '',
  };
}

export function validatePaymentForm(
  values: PaymentFormValues,
  limits: { today: LocalDate; open: string },
): PaymentErrors {
  const errors: PaymentErrors = {};
  if (!isRealDay(values.paidOn)) {
    errors.paidOn = `${V}.dateRequired`;
  } else if (values.paidOn > limits.today) {
    errors.paidOn = 'errors:paid_on_in_future';
  }
  const amount = amountError(values.amount);
  if (amount) {
    errors.amount = amount;
  } else if (typedCents(values.amount)! > apiCents(limits.open)) {
    errors.amount = `${V}.amountAboveOpen`;
  }
  if (values.methodCode === '') errors.methodCode = `${V}.methodRequired`;
  const reference = textLength(values.reference);
  if (reference > PAYMENT_REFERENCE_MAX) {
    errors.reference = `${V}.referenceTooLong`;
  } else if (!isOneLine(values.reference.trim())) {
    errors.reference = 'errors:text_invalid';
  }
  if (textLength(values.note) > NOTE_MAX) errors.note = `${V}.noteTooLong`;
  return errors;
}

export function paymentCreateBody(
  values: PaymentFormValues,
): ExpensePaymentCreate {
  return {
    paid_on: values.paidOn,
    amount: normalizeMoneyInput(values.amount),
    method_code: values.methodCode,
    reference: orNull(values.reference),
    note: orNull(values.note),
  };
}

export function paymentServerErrors(error: unknown): PaymentErrors | null {
  const errors = serverErrors<PaymentField>(error, {
    paid_on: 'paidOn',
    amount: 'amount',
    method_code: 'methodCode',
    reference: 'reference',
    note: 'note',
  });
  if (errors) return errors;
  if (isCode(error, 'payment_method_unavailable')) {
    return { methodCode: 'errors:payment_method_unavailable' };
  }
  if (isCode(error, 'text_invalid')) {
    return { reference: 'errors:text_invalid' };
  }
  return null;
}

/** `409 expense_payment_duplicate`'s `details.payment_id`, or null. */
export function duplicatePaymentId(error: unknown): string | null {
  if (!isCode(error, 'expense_payment_duplicate')) return null;
  const id = detailString(error, 'payment_id');
  return id !== null && isUuid(id) ? id.toLowerCase() : null;
}

/** `details.open` of the two exceeds-open refusals, or null. */
export function exceededOpen(error: unknown): string | null {
  if (
    !isCode(
      error,
      'expense_payment_exceeds_open',
      'expense_adjustment_exceeds_open',
    )
  ) {
    return null;
  }
  const open = detailString(error, 'open');
  return open !== null && centsOf(open) !== null ? open : null;
}

// ------------------------------------------------------------- adjustment

export type AdjustDirection = 'increase' | 'decrease';
export const ADJUST_DIRECTIONS: readonly AdjustDirection[] = [
  'increase',
  'decrease',
];

export interface AdjustmentFormValues {
  direction: AdjustDirection | '';
  /** Without a sign: the direction gives it (D3). */
  amount: string;
  reason: string;
}

export type AdjustmentField = keyof AdjustmentFormValues;
export type AdjustmentErrors = Partial<Record<AdjustmentField, ExpenseMessage>>;

export const ADJUSTMENT_FIELD_ORDER: readonly AdjustmentField[] = [
  'direction',
  'amount',
  'reason',
];

export function emptyAdjustmentForm(): AdjustmentFormValues {
  return { direction: '', amount: '', reason: '' };
}

function reasonError(reason: string): ExpenseMessage | undefined {
  const length = textLength(reason);
  if (length === 0) return `${V}.reasonRequired`;
  if (length > REASON_MAX) return `${V}.reasonTooLong`;
  return undefined;
}

/**
 * The adjustment's checks (D3): a reduction may go down to what is paid
 * (`expense_adjustment_exceeds_open` beyond), and a reduction of the whole
 * net is a void, which has its own action (E3).
 */
export function validateAdjustmentForm(
  values: AdjustmentFormValues,
  expense: Pick<ExpenseDetail, 'adjusted_amount' | 'open_amount' | 'payments'>,
): AdjustmentErrors {
  const errors: AdjustmentErrors = {};
  if (values.direction === '') errors.direction = `${V}.directionRequired`;
  const amount = amountError(values.amount);
  if (amount) {
    errors.amount = amount;
  } else if (values.direction === 'decrease') {
    const cents = typedCents(values.amount)!;
    if (cents > apiCents(expense.open_amount)) {
      errors.amount = `${V}.reductionAboveOpen`;
    } else if (
      cents === apiCents(expense.adjusted_amount) &&
      !hasValidPayment(expense)
    ) {
      errors.amount = `${V}.reductionIsVoid`;
    }
  }
  const reason = reasonError(values.reason);
  if (reason) errors.reason = reason;
  return errors;
}

export function adjustmentBody(
  values: AdjustmentFormValues,
): ExpenseAdjustmentCreate {
  const amount = normalizeMoneyInput(values.amount);
  const cents = centsOf(amount)!;
  return {
    amount: centsText(
      (values.direction === 'decrease' ? 0 - cents : cents) as Cents,
    ),
    reason: values.reason.trim(),
  };
}

/** The void (E3, D4): minus the net, and the reason. */
export function voidBody(
  expense: Pick<ExpenseDetail, 'adjusted_amount'>,
  reason: string,
): ExpenseAdjustmentCreate {
  return { amount: negated(expense.adjusted_amount), reason: reason.trim() };
}

export function validateReason(reason: string): ExpenseMessage | null {
  return reasonError(reason) ?? null;
}

export function adjustmentServerErrors(
  error: unknown,
): AdjustmentErrors | null {
  const errors = serverErrors<AdjustmentField>(error, {
    amount: 'amount',
    reason: 'reason',
  });
  if (errors) return errors;
  if (isCode(error, 'text_invalid')) return { reason: 'errors:text_invalid' };
  return null;
}

/** A reversal's 422 on the reason, or null for the alert. */
export function reasonServerError(error: unknown): ExpenseMessage | null {
  const errors = serverErrors<'reason'>(error, { reason: 'reason' });
  if (errors?.reason) return errors.reason;
  if (isCode(error, 'text_invalid')) return 'errors:text_invalid';
  return null;
}

/** The refusals after which the expense on screen moved: the dialog's page
 * refetches before it shows them (D2, D5, D7). */
export const REFETCH_AFTER: readonly string[] = [
  'expense_void',
  'expense_payment_duplicate',
  'expense_payment_exceeds_open',
  'expense_payment_already_reversed',
  'expense_adjustment_exceeds_open',
  'payment_method_unavailable',
  'not_found',
];
