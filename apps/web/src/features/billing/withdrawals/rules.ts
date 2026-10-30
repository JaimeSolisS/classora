import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import type { LocalDate } from '@/lib/time';
import { apiCents, centsOf, centsText, type Cents } from '../accounts/cents';
import { REASON_MAX } from '../accounts/corrections';
import { REFERENCE_MAX } from '../accounts/rules';
import type {
  PreviewTotals,
  WithdrawalCreditConfirm,
  WithdrawalLine,
  WithdrawalOutcome,
  WithdrawalPreview,
  WithdrawalRefundConfirm,
} from './api';
import { subjectParams } from './api';

// Pure rules of the withdrawal settlement panel (#387 A; #101 S1-S7): the
// form's values, which outcomes are offered, the client-side estimate of
// the totals with overridden lines, the checks, the confirm body, the 422
// mapping and the stale-preview merge. UX only: the server decides (every
// rule is checked again under the locks, and the totals it answers are the
// real ones).

/** A message key (`errors:` prefix for the errors namespace). */
export type SettlementMessage = string;

export interface SettlementValues {
  outcome: WithdrawalOutcome | '';
  reason: string;
  /** Typed credits by charge id (only the lines with a charge). */
  overrides: Readonly<Record<string, string>>;
  refundedOn: string;
  method: string;
  reference: string;
}

export type SettlementField =
  'outcome' | 'reason' | 'refundedOn' | 'method' | 'reference';

export type SettlementErrors = Partial<
  Record<SettlementField, SettlementMessage>
> & { overrides?: Readonly<Record<string, SettlementMessage>> };

/** Focus order of the fields when a check fails. */
export const SETTLEMENT_FIELD_ORDER: readonly SettlementField[] = [
  'outcome',
  'refundedOn',
  'method',
  'reference',
  'reason',
];

export const TOTAL_KEYS = [
  'credit',
  'reduces_debt',
  'refundable_cash',
  'automatic_credits',
  'account_credit_after_credit',
] as const satisfies readonly (keyof PreviewTotals)[];
export type TotalKey = (typeof TOTAL_KEYS)[number];

/** Minutes as hours, `h:mm` ("90" → "1:30"). */
export function minutesAsHours(minutes: number): string {
  const whole = Math.max(0, Math.trunc(minutes));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

export interface OutcomeAvailability {
  /** billing.manage: "Account credit". */
  credit: boolean;
  /** finance.refund.approve: "Refund". */
  refund: boolean;
  /** Shown but disabled: nothing paid is freed (`refundable_cash = 0`). */
  refundDisabled: boolean;
}

export function outcomeAvailability(
  permissions: { canCredit: boolean; canRefund: boolean },
  preview: Pick<WithdrawalPreview, 'totals'>,
): OutcomeAvailability {
  return {
    credit: permissions.canCredit,
    refund: permissions.canRefund,
    refundDisabled: apiCents(preview.totals.refundable_cash) <= 0,
  };
}

/** Neither outcome: the panel is read-only (A3). */
export function isReadOnly(availability: OutcomeAvailability): boolean {
  return !availability.credit && !availability.refund;
}

export function isBlocked(preview: Pick<WithdrawalPreview, 'blocking'>) {
  return preview.blocking.length > 0;
}

/**
 * The blocking codes that mean "there is nothing to settle" (#387 QA D2):
 * the enrollment can still be withdrawn the M4 way, without a settlement.
 * The others need staff action first (or concern packages only).
 */
export const NOTHING_TO_SETTLE_CODES = ['student_has_no_family'] as const;

/**
 * #387 QA D2: nothing to settle, so a roster withdrawal offers the plain
 * M4 withdrawal instead of a settlement: a blocking state of
 * NOTHING_TO_SETTLE_CODES only, or no blocking state and an empty
 * settlement (no line, no automatic credit, every total 0.00; for example
 * billing off).
 */
export function nothingToSettle(
  preview: Pick<
    WithdrawalPreview,
    'blocking' | 'lines' | 'automatic_credits' | 'totals'
  >,
): boolean {
  if (preview.blocking.length > 0) {
    return preview.blocking.every((item) =>
      (NOTHING_TO_SETTLE_CODES as readonly string[]).includes(item.code),
    );
  }
  return (
    preview.lines.length === 0 &&
    preview.automatic_credits.length === 0 &&
    TOTAL_KEYS.every((key) => apiCents(preview.totals[key]) === 0)
  );
}

/** The first outcome that can be chosen, or none. */
export function defaultOutcome(
  availability: OutcomeAvailability,
): WithdrawalOutcome | '' {
  if (availability.credit) return 'account_credit';
  if (availability.refund && !availability.refundDisabled) return 'refund';
  return '';
}

export function emptySettlement(
  availability: OutcomeAvailability,
  today: LocalDate,
  reason = '',
): SettlementValues {
  return {
    outcome: defaultOutcome(availability),
    reason,
    overrides: {},
    refundedOn: today,
    method: '',
    reference: '',
  };
}

/** A line's typed credit, if it was typed and differs from the computed. */
function typedCents(
  line: WithdrawalLine,
  overrides: Readonly<Record<string, string>>,
): Cents | null {
  if (line.charge_id === null) return null;
  const typed = overrides[line.charge_id];
  if (typed === undefined) return null;
  const text = normalizeMoneyInput(typed);
  if (!isMoneyInput(text)) return null;
  const cents = centsOf(text)!;
  return cents === apiCents(line.computed_credit) ? null : cents;
}

/** The credit applied to a line: the typed one when valid, else computed. */
export function appliedCents(
  line: WithdrawalLine,
  overrides: Readonly<Record<string, string>>,
): Cents {
  return typedCents(line, overrides) ?? apiCents(line.computed_credit);
}

/** Paid money a credit frees on a posted line (#101 B2, estimated). */
function freedCents(line: WithdrawalLine, credit: Cents): Cents {
  if (line.charge_id === null) return 0 as Cents;
  const allocated = apiCents(line.allocated);
  const newNet = apiCents(line.net) - credit;
  return Math.min(allocated, Math.max(0, allocated - newNet)) as Cents;
}

export interface SettlementEstimate {
  credit: Cents;
  reducesDebt: Cents;
  refundableCash: Cents;
  accountCreditAfter: Cents;
  /** Some line is overridden: the totals are the client's estimate. */
  estimated: boolean;
}

/**
 * The totals with the typed credits (A4): the server's totals moved by
 * what each overridden line changes. "Estimate, the server decides."
 */
export function estimateTotals(
  preview: Pick<WithdrawalPreview, 'lines' | 'totals'>,
  overrides: Readonly<Record<string, string>>,
): SettlementEstimate {
  let credit = 0;
  let freedLines = 0;
  let freedShift = 0;
  let estimated = false;
  for (const line of preview.lines) {
    const computed = apiCents(line.computed_credit);
    const applied = appliedCents(line, overrides);
    if (applied !== computed) estimated = true;
    credit += applied;
    const freed = freedCents(line, applied);
    freedLines += freed;
    freedShift += freed - freedCents(line, computed);
  }
  const refundable = apiCents(preview.totals.refundable_cash) + freedShift;
  return {
    credit: credit as Cents,
    reducesDebt: (credit - freedLines) as Cents,
    refundableCash: refundable as Cents,
    accountCreditAfter: (apiCents(preview.totals.account_credit_after_credit) +
      freedShift) as Cents,
    estimated,
  };
}

function reasonProblem(reason: string): SettlementMessage | undefined {
  const trimmed = reason.trim();
  if (trimmed === '') return 'billing.withdrawals.validation.reasonRequired';
  if (trimmed.length > REASON_MAX) return 'errors:too_long';
  return undefined;
}

export function validateSettlement(
  values: SettlementValues,
  preview: Pick<WithdrawalPreview, 'lines'>,
  today: LocalDate,
): SettlementErrors {
  const errors: SettlementErrors = {};
  if (values.outcome === '') {
    errors.outcome = 'billing.withdrawals.validation.outcomeRequired';
  }
  const overrides: Record<string, SettlementMessage> = {};
  for (const line of preview.lines) {
    if (line.charge_id === null) continue;
    const typed = values.overrides[line.charge_id];
    if (typed === undefined) continue;
    const text = normalizeMoneyInput(typed);
    if (text === '') {
      overrides[line.charge_id] =
        'billing.withdrawals.validation.creditRequired';
    } else if (!isMoneyInput(text)) {
      overrides[line.charge_id] = 'errors:money_invalid';
    } else if (centsOf(text)! > apiCents(line.net)) {
      overrides[line.charge_id] = 'billing.withdrawals.validation.aboveNet';
    }
  }
  if (Object.keys(overrides).length > 0) errors.overrides = overrides;
  if (values.outcome === 'refund') {
    if (values.refundedOn === '') {
      errors.refundedOn = 'billing.accounts.validation.dateRequired';
    } else if (values.refundedOn > today) {
      errors.refundedOn = 'errors:refunded_on_in_future';
    } else if (values.refundedOn < '2000-01-01') {
      errors.refundedOn = 'errors:date_out_of_range';
    }
    if (values.method === '') {
      errors.method = 'billing.accounts.validation.methodRequired';
    }
    if (values.reference.trim().length > REFERENCE_MAX) {
      errors.reference = 'errors:too_long';
    }
  }
  const reason = reasonProblem(values.reason);
  if (reason) errors.reason = reason;
  return errors;
}

export function hasErrors(errors: SettlementErrors): boolean {
  return Object.entries(errors).some(([key, value]) =>
    key === 'overrides'
      ? Object.keys(value as object).length > 0
      : value !== undefined,
  );
}

/**
 * The confirm body (A5): the subject, the enrollment's version, the
 * trimmed reason, the preview's fingerprint, only the changed lines as
 * overrides, and for a refund its date, method and reference.
 */
export function settlementBody(
  values: SettlementValues,
  preview: Pick<
    WithdrawalPreview,
    'subject' | 'enrollment_version' | 'fingerprint' | 'lines'
  >,
): WithdrawalCreditConfirm | WithdrawalRefundConfirm {
  const credit_overrides = preview.lines.flatMap((line) => {
    const typed = typedCents(line, values.overrides);
    return typed === null || line.charge_id === null
      ? []
      : [{ charge_id: line.charge_id, amount: centsText(typed) }];
  });
  const base: WithdrawalCreditConfirm = {
    subject: subjectParams(preview.subject),
    ...(preview.enrollment_version !== null
      ? { version: preview.enrollment_version }
      : {}),
    reason: values.reason.trim(),
    fingerprint: preview.fingerprint,
    credit_overrides,
  };
  if (values.outcome !== 'refund') return base;
  const reference = values.reference.trim();
  return {
    ...base,
    refund: {
      refunded_on: values.refundedOn,
      payment_method_code: values.method,
      reference: reference === '' ? null : reference,
    },
  };
}

/** A 422's field errors on the panel's fields (null: none of them). */
export function settlementServerErrors(
  error: unknown,
): SettlementErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) return null;
  const fields: Record<string, SettlementField> = {
    reason: 'reason',
    refunded_on: 'refundedOn',
    payment_method_code: 'method',
    reference: 'reference',
  };
  const errors: SettlementErrors = {};
  for (const fieldError of error.fieldErrors) {
    const last = String(fieldError.path[fieldError.path.length - 1] ?? '');
    const field = fieldError.path[0] === 'body' ? fields[last] : undefined;
    if (field && !errors[field]) {
      errors[field] =
        `errors:${codeMessageKey(fieldError.code) ?? 'validation_error'}`;
    }
  }
  return Object.keys(errors).length > 0 ? errors : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The fresh preview of a `409 withdrawal_preview_stale` (A6), or null. */
export function stalePreview(error: unknown): WithdrawalPreview | null {
  if (!(error instanceof ApiError) || error.code !== 'withdrawal_preview_stale')
    return null;
  const preview = error.problem?.details?.preview;
  if (!isRecord(preview) || typeof preview.fingerprint !== 'string') {
    return null;
  }
  return preview as unknown as WithdrawalPreview;
}

function lineSignature(line: WithdrawalLine): string {
  return [
    line.net,
    line.allocated,
    line.computed_credit,
    line.basis.consumed,
    line.basis.unused,
    line.basis.denominator,
    line.policy,
  ].join('|');
}

/**
 * A6: after a stale answer, keep the reason, the outcome and the refund
 * fields, drop the overrides of lines that changed or are gone, and say
 * which totals changed (highlighted).
 */
export function mergeStale(
  previous: Pick<WithdrawalPreview, 'lines' | 'totals'>,
  fresh: Pick<WithdrawalPreview, 'lines' | 'totals'>,
  values: SettlementValues,
): { values: SettlementValues; changed: TotalKey[] } {
  const before = new Map(
    previous.lines
      .filter((line) => line.charge_id !== null)
      .map((line) => [line.charge_id!, lineSignature(line)]),
  );
  const after = new Map(
    fresh.lines
      .filter((line) => line.charge_id !== null)
      .map((line) => [line.charge_id!, lineSignature(line)]),
  );
  const overrides = Object.fromEntries(
    Object.entries(values.overrides).filter(
      ([chargeId]) =>
        after.has(chargeId) && after.get(chargeId) === before.get(chargeId),
    ),
  );
  const changed = TOTAL_KEYS.filter(
    (key) => previous.totals[key] !== fresh.totals[key],
  );
  return { values: { ...values, overrides }, changed };
}

/** The codes after which the panel reads the preview again (A7). */
export function refetchesPreview(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 409 &&
    error.code !== 'withdrawal_preview_stale'
  );
}
