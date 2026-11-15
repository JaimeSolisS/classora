import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { ChargeItem, LedgerEntry } from './api';
import { centsText, type Cents } from './cents';

// The words of a family account (#379 C, F): money from the API's strings
// (lib/money.ts), dates in the center zone (ADR-0004), a charge's kind, a
// ledger entry's type and kind, and a payment method's label (the code
// when the catalog has none, #96 M8). #386: an adjustment's kind.

const CHARGE_KINDS = new Set([
  'session',
  'group_enrollment',
  'cohort_enrollment',
  'tuition_period',
  'package_purchase',
  'manual',
]);
const ADJUSTMENT_KINDS = new Set([
  'duration_change',
  'cancellation_credit',
  'closure_credit',
  'enrollment_removal',
  'withdrawal',
  'manual',
  'package_cancellation',
]);
const PAYMENT_METHODS = new Set(['cash', 'transfer', 'other']);
const MINUS = '−';

export function useAccountLabels() {
  const { t } = useTranslation();
  const { formatMoney, formatLocalDate, formatInstant } = useFormatters();

  const method = (code: string) =>
    PAYMENT_METHODS.has(code)
      ? t(`billing.paymentMethods.${code as 'cash'}`)
      : code;
  const adjustmentKind = (kind: string) =>
    ADJUSTMENT_KINDS.has(kind)
      ? t(`billing.accounts.adjustmentKinds.${kind as 'manual'}`)
      : kind;
  const chargeKind = (kind: string) =>
    CHARGE_KINDS.has(kind)
      ? t(`billing.accounts.chargeKinds.${kind as 'session'}`)
      : kind;

  return {
    money: (value: string) => formatMoney(value),
    cents: (value: Cents) => formatMoney(centsText(value)),
    /** A balance effect with its sign: "+$100.00" / "−$100.00". */
    signed: (value: string) => {
      const negative = value.startsWith('-');
      const absolute = negative ? value.slice(1) : value;
      const zero = /^0+(\.0+)?$/.test(absolute);
      const sign = zero ? '' : negative ? MINUS : '+';
      return `${sign}${formatMoney(absolute)}`;
    },
    date: (value: string) => formatLocalDate(value),
    instantDate: (value: string) => formatInstant(value, 'date'),
    method,
    chargeKind,
    adjustmentKind,
    /** A charge as a line: its description, or its kind. */
    chargeLabel: (charge: Pick<ChargeItem, 'description' | 'kind'>) =>
      charge.description && charge.description.trim() !== ''
        ? charge.description
        : chargeKind(charge.kind),
    entryType: (entry: Pick<LedgerEntry, 'entry_type'>) =>
      t(`billing.accounts.ledger.types.${entry.entry_type}`),
    /** A ledger entry's kind: the charge or adjustment kind, or the method. */
    entryKind: (entry: Pick<LedgerEntry, 'entry_type' | 'kind'>) => {
      const kind = entry.kind;
      if (!kind) return null;
      if (entry.entry_type === 'charge') return chargeKind(kind);
      if (entry.entry_type === 'adjustment') return adjustmentKind(kind);
      if (entry.entry_type === 'payment' || entry.entry_type === 'refund') {
        return method(kind);
      }
      return null;
    },
  };
}

export type AccountLabels = ReturnType<typeof useAccountLabels>;
