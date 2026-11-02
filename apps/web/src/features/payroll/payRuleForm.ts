import { ApiError } from '@/app/api';
import { codeMessageKey } from '@/app/errors';
import { isMoneyInput, isRateInput, normalizeMoneyInput } from '@/lib/money';
import type { LocalDate } from '@/lib/time';
import type {
  PayRule,
  PayRuleCreate,
  PayRuleEvent,
  PayRuleEventOverview,
  PayRuleMode,
} from './api';
import { isRealDay, type PayrollMessage } from './rules';

// The pay rules' client rules (#444 B), without React. The percent and the
// amount stay the strings the API sent or the user typed; nothing here does
// arithmetic on them. The server stays authoritative: these only stop a
// request that would surely fail.

export const PAY_RULE_EVENTS: readonly PayRuleEvent[] = [
  'late_cancellation',
  'no_show',
  'makeup',
];
export const PAY_RULE_MODES: readonly PayRuleMode[] = [
  'none',
  'percent',
  'fixed',
];

const V = 'payroll.payRules.validation';

/** The version a new one must be based on: the last upcoming, else current. */
export function latestPayRule(overview: PayRuleEventOverview): PayRule {
  return overview.upcoming.at(-1) ?? overview.current;
}

/** "100.0000" as "100", "12.5000" as "12.5": a percent as people write it. */
export function percentText(percent: string): string {
  return percent.includes('.') ? percent.replace(/\.?0+$/, '') : percent;
}

export interface PayRuleFormValues {
  mode: PayRuleMode;
  /** As typed; sent only with the percent mode. */
  percent: string;
  /** As typed; sent only with the fixed mode. */
  amount: string;
  effectiveFrom: string;
}

export type PayRuleField = keyof PayRuleFormValues;
export type PayRuleErrors = Partial<Record<PayRuleField, PayrollMessage>>;

/** Prefilled from the latest version; the date is always a new choice. */
export function initialPayRuleForm(latest: PayRule): PayRuleFormValues {
  return {
    mode: latest.mode,
    percent: latest.percent === null ? '' : percentText(latest.percent),
    amount: latest.amount ?? '',
    effectiveFrom: '',
  };
}

/** Compares digits, not numbers: `1000.0001` is out of range (#441 D2). */
function percentInRange(value: string): boolean {
  const [whole = '', decimals = ''] = value.split('.');
  const digits = whole.replace(/^0+(?=\d)/, '');
  return digits.length < 4 || (digits === '1000' && /^0*$/.test(decimals));
}

export function validatePayRuleForm(
  values: PayRuleFormValues,
  today: LocalDate,
): PayRuleErrors {
  const errors: PayRuleErrors = {};
  if (!isRealDay(values.effectiveFrom)) {
    errors.effectiveFrom = 'payroll.validation.dateRequired';
  } else if (values.effectiveFrom < today) {
    errors.effectiveFrom = `${V}.datePast`;
  }
  if (values.mode === 'percent') {
    const percent = normalizeMoneyInput(values.percent);
    if (percent === '') {
      errors.percent = `${V}.percentRequired`;
    } else if (!isRateInput(percent)) {
      errors.percent = 'errors:percent_invalid';
    } else if (!percentInRange(percent)) {
      errors.percent = `${V}.percentRange`;
    }
  }
  if (values.mode === 'fixed') {
    const amount = normalizeMoneyInput(values.amount);
    if (amount === '') {
      errors.amount = `${V}.amountRequired`;
    } else if (!isMoneyInput(amount)) {
      errors.amount = 'errors:money_invalid';
    }
  }
  return errors;
}

/** The POST body: every field, `null` written out, the values as strings. */
export function payRuleCreateBody(
  values: PayRuleFormValues,
  base: PayRule,
): PayRuleCreate {
  return {
    based_on_id: base.id,
    event: base.event,
    effective_from: values.effectiveFrom,
    mode: values.mode,
    percent:
      values.mode === 'percent' ? normalizeMoneyInput(values.percent) : null,
    amount: values.mode === 'fixed' ? normalizeMoneyInput(values.amount) : null,
  };
}

const SERVER_FIELDS: Readonly<Record<string, PayRuleField>> = {
  mode: 'mode',
  percent: 'percent',
  amount: 'amount',
  effective_from: 'effectiveFrom',
};

/**
 * `percent_out_of_range` also has a shared text for coupons, "up to one
 * hundred", which is wrong here. The `rate_*` texts name a rate, not a percent.
 */
const PERCENT_MESSAGES: Readonly<Record<string, PayrollMessage>> = {
  percent_out_of_range: `${V}.percentRange`,
  rate_out_of_range: `${V}.percentRange`,
  rate_invalid: 'errors:percent_invalid',
  rate_precision: 'errors:percent_precision',
};

function fieldMessage(field: PayRuleField, code: string): PayrollMessage {
  return (
    (field === 'percent' ? PERCENT_MESSAGES[code] : undefined) ??
    `errors:${codeMessageKey(code) ?? 'validation_error'}`
  );
}

function fieldShown(field: PayRuleField, mode: PayRuleMode): boolean {
  return (
    (field !== 'percent' || mode === 'percent') &&
    (field !== 'amount' || mode === 'fixed')
  );
}

/**
 * A 422's messages on the fields the form shows for `mode`; null when none
 * is, so the caller shows an alert and the refusal never goes unseen. Two
 * `pay_rule_values_mismatch` can arrive at once, one per field.
 */
export function payRuleServerErrors(
  error: unknown,
  mode: PayRuleMode,
): PayRuleErrors | null {
  if (!(error instanceof ApiError) || error.status !== 422) {
    return null;
  }
  if (error.code === 'invalid_effective_from') {
    return { effectiveFrom: `${V}.dateLater` };
  }
  const found: PayRuleErrors = {};
  for (const { path, code } of error.fieldErrors) {
    const field =
      path[0] === 'body' ? SERVER_FIELDS[String(path[1])] : undefined;
    if (field && fieldShown(field, mode) && !found[field]) {
      found[field] = fieldMessage(field, code);
    }
  }
  return Object.keys(found).length > 0 ? found : null;
}
