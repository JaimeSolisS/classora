import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { Discount } from './api';
import { CHARGE_KINDS, CRITERIA_CLASS_TYPES, trimPercent } from './rules';

// The words of a discount (#367 B2), shared by the list and the detail
// page: its kind, value ("50 %" or an amount), who gets it, the criteria
// summary ("Any" when there are none) and the validity.

export function useDiscountLabels() {
  const { t } = useTranslation();
  const { formatMoney, formatLocalDate } = useFormatters();

  const value = (item: Pick<Discount, 'percent' | 'amount'>) =>
    item.percent !== null
      ? t('billing.discounts.percent', { value: trimPercent(item.percent) })
      : formatMoney(item.amount ?? '0');

  const who = (item: Pick<Discount, 'audience' | 'assignment_count'>) =>
    item.audience === 'all'
      ? t('billing.discounts.audience.all')
      : t('billing.discounts.assignedCount', {
          count: item.assignment_count,
        });

  const criteria = (item: Pick<Discount, 'eligibility'>) => {
    const { eligibility } = item;
    const parts: string[] = [];
    if (eligibility.charge_kinds?.length) {
      parts.push(
        CHARGE_KINDS.filter((kind) => eligibility.charge_kinds?.includes(kind))
          .map((kind) => t(`billing.discounts.chargeKinds.${kind}`))
          .join(', '),
      );
    }
    if (eligibility.class_types?.length) {
      parts.push(
        CRITERIA_CLASS_TYPES.filter((type) =>
          eligibility.class_types?.includes(type),
        )
          .map((type) => t(`billing.prices.classTypes.${type}`))
          .join(', '),
      );
    }
    if (eligibility.subjects?.length) {
      parts.push(
        eligibility.subjects.map((subject) => subject.name).join(', '),
      );
    }
    if (eligibility.education_levels?.length) {
      parts.push(
        eligibility.education_levels.map((level) => level.name).join(', '),
      );
    }
    if (eligibility.min_family_students !== null) {
      parts.push(
        t('billing.discounts.minFamily', {
          count: eligibility.min_family_students,
        }),
      );
    }
    return parts.length > 0 ? parts.join('; ') : t('billing.prices.any');
  };

  const validity = (item: Pick<Discount, 'starts_on' | 'expires_on'>) => {
    if (item.starts_on === null && item.expires_on === null) {
      return t('billing.prices.none');
    }
    if (item.starts_on === null) {
      return t('billing.discounts.until', {
        until: formatLocalDate(item.expires_on!),
      });
    }
    return t('billing.discounts.range', {
      from: formatLocalDate(item.starts_on),
      until:
        item.expires_on === null
          ? t('billing.discounts.noEnd')
          : formatLocalDate(item.expires_on),
    });
  };

  return {
    kind: (item: Pick<Discount, 'kind'>) =>
      t(`billing.discounts.kinds.${item.kind}`),
    value,
    who,
    criteria,
    validity,
    status: (item: Pick<Discount, 'status'>) =>
      t(`billing.discounts.status.${item.status}`),
  };
}
