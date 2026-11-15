import { useTranslation } from 'react-i18next';
import { useAccountLabels } from '@/features/billing/accounts/labels';
import { usePackageLabels } from '@/features/billing/packages/labels';
import { trimPercent } from '@/features/billing/discounts/rules';
import type {
  FamilyChargeDetail,
  FamilyPackage,
  FamilyPackageDetail,
} from './api';

// The portal's words (#388 D4, F1, G1): the staff account and package
// labels (#379, #377), with a family-facing fallback for a kind or status
// the client doesn't know yet ("Adjustment", "Other"), never a code.

const ADJUSTMENT_KINDS = new Set([
  'duration_change',
  'cancellation_credit',
  'closure_credit',
  'enrollment_removal',
  'withdrawal',
  'manual',
  'package_cancellation',
]);
const PACKAGE_STATUSES = new Set([
  'active',
  'exhausted',
  'expired',
  'cancelled',
  'withdrawn',
]);
const TRANSACTION_KINDS = new Set([
  'grant',
  'consume',
  'expire',
  'reversal',
  'refund',
]);
const DISCOUNT_KINDS = new Set([
  'discount',
  'scholarship',
  'sibling',
  'promotion',
]);

type Transaction = FamilyPackageDetail['transactions'][number];
type DiscountLine = FamilyChargeDetail['discounts'][number];

export function usePortalLabels() {
  const { t } = useTranslation();
  const account = useAccountLabels();
  const packages = usePackageLabels();

  const adjustmentKind = (kind: string) =>
    ADJUSTMENT_KINDS.has(kind)
      ? account.adjustmentKind(kind)
      : t('familyBilling.adjustmentFallback');

  return {
    ...account,
    adjustmentKind,
    /** A ledger entry's kind, with the adjustment fallback. */
    entryKind: (entry: { entry_type: string; kind: string | null }) =>
      entry.entry_type === 'adjustment' && entry.kind
        ? adjustmentKind(entry.kind)
        : account.entryKind(entry as Parameters<typeof account.entryKind>[0]),
    packageStatus: (status: string) =>
      PACKAGE_STATUSES.has(status)
        ? t(`billing.packages.status.${status as 'active'}`)
        : t('familyBilling.packages.statusOther'),
    /** "8 / 10 sessions". */
    packageBalance: (pkg: FamilyPackage) => packages.balance(pkg),
    packageSubject: (pkg: FamilyPackage) => packages.subject(pkg),
    signedUnits: (pkg: FamilyPackage, movement: Transaction) =>
      packages.signed(pkg, movement),
    transactionKind: (movement: Transaction) => {
      if (!TRANSACTION_KINDS.has(movement.kind)) {
        return t('familyBilling.packages.movementOther');
      }
      if (movement.kind === 'consume') {
        return t('billing.packages.history.kinds.consume', {
          date: movement.session_starts_at
            ? account.instantDate(movement.session_starts_at)
            : '—',
        });
      }
      return t(`billing.packages.history.kinds.${movement.kind as 'grant'}`);
    },
    discountName: (line: DiscountLine) =>
      line.name.trim() !== ''
        ? line.name
        : DISCOUNT_KINDS.has(line.kind)
          ? t(`billing.discounts.kinds.${line.kind}`)
          : t('familyBilling.chargeDetail.discount'),
    /** "10 %" or "$50.00". */
    discountValue: (line: DiscountLine) =>
      line.percent !== null
        ? t('billing.discounts.percent', { value: trimPercent(line.percent) })
        : line.fixed_amount !== null
          ? account.money(line.fixed_amount)
          : '—',
  };
}

export type PortalLabels = ReturnType<typeof usePortalLabels>;
