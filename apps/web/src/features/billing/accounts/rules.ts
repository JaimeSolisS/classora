import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import type { LocalDate } from '@/lib/time';
import {
  CHARGE_STATUSES,
  type AllocationLine,
  type ChargeFilters,
  type ChargeItem,
  type ChargeStatus,
  type CreditAllocationCreate,
  type PaymentCreate,
} from './api';
import {
  apiCents,
  centsOf,
  centsText,
  minCents,
  sumCents,
  type Cents,
} from './cents';

// Pure helpers of the account page and its dialogs (#379): the URL state
// (the shared student filter, the charge statuses and pages), the oldest-
// first order and fill of the open charges, the record-payment and
// allocate-credit forms' checks, bodies and 422 mapping. UX only: the API
// decides (#96: the allocation is checked again under the account lock).

/** The API's text limits (apps/api/app/modules/billing/models.py). */
export const REFERENCE_MAX = 100;
export const PAYER_NAME_MAX = 200;
export const NOTE_MAX = 500;
/** The earliest `received_on` the API takes. */
export const RECEIVED_ON_MIN = '2000-01-01';

// --------------------------------------------------------- URL state

export interface AccountView extends ChargeFilters {
  /** The payments list's page (`?payments_page=`). */
  paymentsPage: number;
}

function pageOf(value: string | null): number {
  const page = Number.parseInt(value ?? '', 10);
  return Number.isInteger(page) && page >= 1 ? page : 1;
}

/** The account page's state in `search`; anything invalid is ignored. */
export function accountView(search: URLSearchParams): AccountView {
  const statuses = search.getAll('status');
  const studentId = search.get('student_id');
  return {
    studentId: studentId && studentId.length > 0 ? studentId : null,
    statuses: CHARGE_STATUSES.filter((status) => statuses.includes(status)),
    page: pageOf(search.get('page')),
    paymentsPage: pageOf(search.get('payments_page')),
  };
}

/** The search parameters of `view` (defaults left out). */
export function accountViewParams(view: AccountView): URLSearchParams {
  const params = new URLSearchParams();
  if (view.studentId) params.set('student_id', view.studentId);
  for (const status of view.statuses) params.append('status', status);
  if (view.page > 1) params.set('page', String(view.page));
  if (view.paymentsPage > 1) {
    params.set('payments_page', String(view.paymentsPage));
  }
  return params;
}

/** Toggles one charge status in the filter, in the filter's order. */
export function toggleStatus(
  statuses: readonly ChargeStatus[],
  status: ChargeStatus,
): ChargeStatus[] {
  const has = statuses.includes(status);
  return CHARGE_STATUSES.filter((item) =>
    item === status ? !has : statuses.includes(item),
  );
}

// ------------------------------------------------- the open charges

/**
 * The open charges oldest first, as the server allocates them (#96 M2):
 * effective due date, then service date, posting time and id.
 */
export function oldestFirst(charges: readonly ChargeItem[]): ChargeItem[] {
  return charges
    .filter((charge) => apiCents(charge.open) > 0)
    .toSorted(
      (a, b) =>
        a.due_date.localeCompare(b.due_date) ||
        a.service_date.localeCompare(b.service_date) ||
        a.posted_at.localeCompare(b.posted_at) ||
        a.id.localeCompare(b.id),
    );
}

export interface PlannedLine {
  charge: ChargeItem;
  amount: Cents;
}

export interface Plan {
  lines: PlannedLine[];
  allocated: Cents;
  /** What is left of `available` (account credit for a payment). */
  remainder: Cents;
}

/** Fills `charges` (already oldest first) up to `available`. */
export function fillOldestFirst(
  charges: readonly ChargeItem[],
  available: Cents,
): Plan {
  const lines: PlannedLine[] = [];
  let left = available;
  for (const charge of charges) {
    if (left <= 0) break;
    const amount = minCents(apiCents(charge.open), left);
    if (amount > 0) {
      lines.push({ charge, amount });
      left = (left - amount) as Cents;
    }
  }
  return {
    lines,
    allocated: sumCents(lines.map((line) => line.amount)),
    remainder: left,
  };
}

/** The editor's typed amounts for a fill (empty rows left out). */
export function linesOfPlan(plan: Plan): Record<string, string> {
  return Object.fromEntries(
    plan.lines.map((line) => [line.charge.id, centsText(line.amount)]),
  );
}

// ------------------------------------------------------ messages

/** A message: a `common` key, or `errors:<key>` for an errors-namespace key. */
export type AccountMessage = string;

function serverMessage(code: string): AccountMessage {
  return `errors:${codeMessageKey(code) ?? 'validation_error'}`;
}

/** The typed amount's problem, if any (empty is checked by the caller). */
function amountProblem(text: string): AccountMessage | undefined {
  if (!isMoneyInput(text)) return 'errors:money_invalid';
  if (centsOf(text) === 0) return 'errors:amount_not_positive';
  return undefined;
}

// --------------------------------------------- the allocation editor

export type LineErrors = Record<string, AccountMessage>;

export interface LinesCheck {
  /** The rows with a valid amount, in the charges' order. */
  lines: AllocationLine[];
  total: Cents;
  errors: LineErrors;
}

/**
 * The editor's rows: an empty row is not allocated; a filled one must be a
 * money amount above zero and at most the charge's open amount.
 */
export function checkLines(
  charges: readonly ChargeItem[],
  typed: Readonly<Record<string, string>>,
): LinesCheck {
  const lines: AllocationLine[] = [];
  const errors: LineErrors = {};
  const amounts: Cents[] = [];
  for (const charge of charges) {
    const text = normalizeMoneyInput(typed[charge.id] ?? '');
    if (text === '') continue;
    const problem = amountProblem(text);
    if (problem) {
      errors[charge.id] = problem;
      continue;
    }
    const amount = centsOf(text)!;
    if (amount > apiCents(charge.open)) {
      errors[charge.id] = 'billing.accounts.validation.lineAboveOpen';
      continue;
    }
    amounts.push(amount);
    lines.push({ charge_id: charge.id, amount: centsText(amount) });
  }
  return { lines, total: sumCents(amounts), errors };
}

/** The editor's running total of the valid rows (for the summary line). */
export function typedTotal(
  charges: readonly ChargeItem[],
  typed: Readonly<Record<string, string>>,
): Cents {
  return checkLines(charges, typed).total;
}

// ------------------------------------------------- record a payment

export type RecordMode = 'oldest_first' | 'explicit' | 'none';
export const RECORD_MODES: readonly RecordMode[] = [
  'oldest_first',
  'explicit',
  'none',
];

export interface RecordFormValues {
  amount: string;
  receivedOn: string;
  method: string;
  reference: string;
  payerName: string;
  note: string;
  mode: RecordMode;
  /** Charge id → typed amount (explicit mode). */
  lines: Record<string, string>;
}

export type RecordField =
  | 'amount'
  | 'receivedOn'
  | 'method'
  | 'reference'
  | 'payerName'
  | 'note'
  | 'allocation';

export type RecordErrors = Partial<Record<RecordField, AccountMessage>> & {
  lines?: LineErrors;
};

/** The fields in the form's order (the first error gets the focus). */
export const RECORD_FIELD_ORDER: readonly RecordField[] = [
  'amount',
  'receivedOn',
  'method',
  'reference',
  'payerName',
  'note',
  'allocation',
];

export function emptyRecordForm(
  today: LocalDate,
  method = '',
): RecordFormValues {
  return {
    amount: '',
    receivedOn: today,
    method,
    reference: '',
    payerName: '',
    note: '',
    mode: 'oldest_first',
    lines: {},
  };
}

/** The form's problems (no key means valid). */
export function validateRecordForm(
  values: RecordFormValues,
  today: LocalDate,
  charges: readonly ChargeItem[],
): RecordErrors {
  const errors: RecordErrors = {};
  const amountText = normalizeMoneyInput(values.amount);
  if (amountText === '') {
    errors.amount = 'billing.accounts.validation.amountRequired';
  } else {
    const problem = amountProblem(amountText);
    if (problem) errors.amount = problem;
  }
  if (values.receivedOn === '') {
    errors.receivedOn = 'billing.accounts.validation.dateRequired';
  } else if (values.receivedOn > today) {
    errors.receivedOn = 'errors:received_on_in_future';
  } else if (values.receivedOn < RECEIVED_ON_MIN) {
    errors.receivedOn = 'errors:date_out_of_range';
  }
  if (values.method === '') {
    errors.method = 'billing.accounts.validation.methodRequired';
  }
  if (values.reference.trim().length > REFERENCE_MAX) {
    errors.reference = 'errors:too_long';
  }
  if (values.payerName.trim().length > PAYER_NAME_MAX) {
    errors.payerName = 'errors:too_long';
  }
  if (values.note.trim().length > NOTE_MAX) {
    errors.note = 'errors:too_long';
  }
  if (values.mode === 'explicit') {
    const check = checkLines(charges, values.lines);
    if (Object.keys(check.errors).length > 0) {
      errors.lines = check.errors;
    } else if (check.lines.length === 0) {
      errors.allocation = 'billing.accounts.validation.linesRequired';
    } else if (!errors.amount && check.total > centsOf(amountText)!) {
      errors.allocation = 'billing.accounts.validation.linesAbovePayment';
    }
  }
  return errors;
}

export function hasErrors(errors: RecordErrors | CreditErrors): boolean {
  return Object.entries(errors).some(([key, value]) =>
    key === 'lines' ? Object.keys(value ?? {}).length > 0 : value !== undefined,
  );
}

const optional = (text: string) => {
  const trimmed = text.trim();
  return trimmed === '' ? null : trimmed;
};

/** The `POST …/payments` body of valid values. */
export function recordBody(
  values: RecordFormValues,
  charges: readonly ChargeItem[],
): PaymentCreate {
  return {
    amount: normalizeMoneyInput(values.amount),
    received_on: values.receivedOn,
    payment_method_code: values.method,
    reference: optional(values.reference),
    payer_name: optional(values.payerName),
    note: optional(values.note),
    allocation:
      values.mode === 'explicit'
        ? { mode: 'explicit', lines: checkLines(charges, values.lines).lines }
        : { mode: values.mode },
  };
}

const RECORD_BODY_FIELDS: Record<string, RecordField> = {
  amount: 'amount',
  received_on: 'receivedOn',
  payment_method_code: 'method',
  reference: 'reference',
  payer_name: 'payerName',
  note: 'note',
};

/** The line errors and the allocation error of a 422's `body.allocation…` paths. */
function allocationErrors(
  error: ApiError,
  sent: readonly AllocationLine[],
): { allocation?: AccountMessage; lines: LineErrors } {
  const lines: LineErrors = {};
  let allocation: AccountMessage | undefined;
  for (const fieldError of error.fieldErrors) {
    const [where, key, sub, index] = fieldError.path;
    if (where !== 'body' || key !== 'allocation') continue;
    const line =
      sub === 'lines' && typeof index === 'number' ? sent[index] : undefined;
    if (line) {
      lines[line.charge_id] ??= serverMessage(fieldError.code);
    } else {
      allocation ??= serverMessage(fieldError.code);
    }
  }
  return { allocation, lines };
}

/**
 * A 422's field errors on the record form (the first per field, the line
 * errors on their row by the index of the line sent), or null when none is
 * about the form.
 */
export function recordServerErrors(
  error: unknown,
  sent: readonly AllocationLine[],
): RecordErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const errors: RecordErrors = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    const field =
      where === 'body' ? RECORD_BODY_FIELDS[String(key)] : undefined;
    if (field && !errors[field]) {
      errors[field] = serverMessage(fieldError.code);
    }
  }
  const { allocation, lines } = allocationErrors(error, sent);
  if (allocation) errors.allocation = allocation;
  if (Object.keys(lines).length > 0) errors.lines = lines;
  return hasErrors(errors) ? errors : null;
}

/** The charge of a 409 allocation_exceeds_open, if the problem names it. */
export function exceededChargeId(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.code !== 'allocation_exceeds_open')
    return null;
  const id: unknown = error.problem?.details?.charge_id;
  return typeof id === 'string' ? id : null;
}

// ------------------------------------------- allocate account credit

export type CreditMode = 'oldest_first' | 'explicit';
export const CREDIT_MODES: readonly CreditMode[] = ['oldest_first', 'explicit'];

export interface CreditFormValues {
  mode: CreditMode;
  /** Oldest first: empty = the whole credit. */
  amount: string;
  lines: Record<string, string>;
}

export type CreditField = 'amount' | 'allocation';
export type CreditErrors = Partial<Record<CreditField, AccountMessage>> & {
  lines?: LineErrors;
};

export const EMPTY_CREDIT_FORM: CreditFormValues = {
  mode: 'oldest_first',
  amount: '',
  lines: {},
};

export function validateCreditForm(
  values: CreditFormValues,
  credit: Cents,
  charges: readonly ChargeItem[],
): CreditErrors {
  const errors: CreditErrors = {};
  if (values.mode === 'oldest_first') {
    const text = normalizeMoneyInput(values.amount);
    if (text !== '') {
      const problem = amountProblem(text);
      if (problem) {
        errors.amount = problem;
      } else if (centsOf(text)! > credit) {
        errors.amount = 'billing.accounts.validation.amountAboveCredit';
      }
    }
    return errors;
  }
  const check = checkLines(charges, values.lines);
  if (Object.keys(check.errors).length > 0) {
    errors.lines = check.errors;
  } else if (check.lines.length === 0) {
    errors.allocation = 'billing.accounts.validation.linesRequired';
  } else if (check.total > credit) {
    errors.allocation = 'billing.accounts.validation.linesAboveCredit';
  }
  return errors;
}

/** The `POST …/allocations` body of valid values. */
export function creditBody(
  values: CreditFormValues,
  charges: readonly ChargeItem[],
): CreditAllocationCreate {
  if (values.mode === 'explicit') {
    return {
      allocation: {
        mode: 'explicit',
        lines: checkLines(charges, values.lines).lines,
      },
    };
  }
  const text = normalizeMoneyInput(values.amount);
  return {
    allocation: { mode: 'oldest_first' },
    ...(text !== '' ? { amount: text } : {}),
  };
}

/** A 422's field errors on the credit form, or null. */
export function creditServerErrors(
  error: unknown,
  sent: readonly AllocationLine[],
): CreditErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const errors: CreditErrors = {};
  for (const fieldError of error.fieldErrors) {
    const [where, key] = fieldError.path;
    if (where === 'body' && key === 'amount' && !errors.amount) {
      errors.amount = serverMessage(fieldError.code);
    }
  }
  const { allocation, lines } = allocationErrors(error, sent);
  if (allocation) errors.allocation = allocation;
  if (Object.keys(lines).length > 0) errors.lines = lines;
  return hasErrors(errors) ? errors : null;
}
