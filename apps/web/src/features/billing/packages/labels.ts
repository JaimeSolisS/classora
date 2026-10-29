import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import {
  formatPackageBalance,
  formatPackageUnits,
  formatSignedUnits,
} from '@/lib/packages';
import type { PackageProduct, PackageTransaction, SoldPackage } from './api';

// The words of a package product and a sold package (#377 B1, C1, E): the
// units ("10 sessions", "20 h"), the validity, the subject or "Any
// subject", the price or "Not offered", the balance and a movement.

export function usePackageLabels() {
  const { t } = useTranslation();
  const { formatMoney, formatLocalDate, formatInstant } = useFormatters();

  return {
    /** "10 sessions" / "20 h". */
    units: (item: Pick<PackageProduct, 'unit' | 'quantity'>) =>
      formatPackageUnits(item.unit, item.quantity, t),
    validity: (item: Pick<PackageProduct, 'validity_days'>) =>
      t('billing.packages.validityDays', { count: item.validity_days }),
    subject: (item: Pick<PackageProduct, 'subject'>) =>
      item.subject?.name ?? t('billing.packages.anySubject'),
    price: (item: Pick<PackageProduct, 'current_amount'>) =>
      item.current_amount === null
        ? t('billing.prices.notOffered')
        : formatMoney(item.current_amount),
    /** "8 / 10 sessions". */
    balance: (
      item: Pick<SoldPackage, 'unit' | 'remaining_units' | 'units_granted'>,
    ) =>
      formatPackageBalance(
        item.unit,
        item.remaining_units,
        item.units_granted,
        t,
      ),
    remaining: (item: Pick<SoldPackage, 'unit' | 'remaining_units'>) =>
      formatPackageUnits(item.unit, item.remaining_units, t),
    granted: (item: Pick<SoldPackage, 'unit' | 'units_granted'>) =>
      formatPackageUnits(item.unit, item.units_granted, t),
    signed: (
      item: Pick<SoldPackage, 'unit'>,
      movement: Pick<PackageTransaction, 'units'>,
    ) => formatSignedUnits(item.unit, movement.units, t),
    date: (value: string) => formatLocalDate(value),
    instantDate: (value: string) => formatInstant(value, 'date'),
    money: (value: string) => formatMoney(value),
    /** A product as a sale choice: "Álgebra 10 · 10 sessions · 90 days — $2,000.00". */
    productOption: (item: PackageProduct) =>
      t('billing.packages.sell.productOption', {
        name: item.name,
        units: formatPackageUnits(item.unit, item.quantity, t),
        validity: t('billing.packages.validityDays', {
          count: item.validity_days,
        }),
        price:
          item.current_amount === null
            ? t('billing.prices.notOffered')
            : formatMoney(item.current_amount),
      }),
  };
}
