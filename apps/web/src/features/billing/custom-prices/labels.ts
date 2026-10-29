import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { ServicePrice } from '../pricing/api';
import { usePriceLabels } from '../pricing/labels';
import type { TargetOption } from '../targets';
import type { CustomPrice } from './api';

// The words of a custom price (#367 E2): the student or family, the
// service with the #363 labels (class type · model, "Level · Subject",
// then the level, duration and cohort or group when it has them), the
// catalog price today and the dates.

type CatalogItem = Pick<
  ServicePrice,
  | 'class_type'
  | 'pricing_model'
  | 'subject'
  | 'education_level'
  | 'duration_minutes'
  | 'cohort'
  | 'group'
> &
  // A per-period price's frequency is part of its service line (#374 B2).
  Partial<Pick<ServicePrice, 'period_frequency'>>;

export function useCustomPriceLabels() {
  const { t } = useTranslation();
  const prices = usePriceLabels();
  const { formatLocalDate } = useFormatters();

  const service = (item: CatalogItem) =>
    [
      prices.service(item),
      prices.subject(item),
      item.education_level?.name,
      item.duration_minutes === null ? undefined : prices.duration(item),
      item.cohort?.name ?? item.group?.name,
    ]
      .filter(Boolean)
      .join(', ');

  return {
    service,
    catalog: (item: Pick<ServicePrice, 'current'>) => prices.current(item),
    /** A catalog price option: the service and its price today. */
    option: (item: CatalogItem & Pick<ServicePrice, 'current'>) =>
      t('billing.customPrices.option', {
        service: service(item),
        amount: prices.current(item),
      }),
    dates: (item: Pick<CustomPrice, 'effective_from' | 'expires_on'>) =>
      t('billing.discounts.range', {
        from: formatLocalDate(item.effective_from),
        until:
          item.expires_on === null
            ? t('billing.discounts.noEnd')
            : formatLocalDate(item.expires_on),
      }),
    target: (item: Pick<CustomPrice, 'student' | 'family'>): TargetOption =>
      item.student
        ? { kind: 'student', ...item.student }
        : {
            kind: 'family',
            id: item.family?.id ?? '',
            name: item.family?.name ?? '',
          },
  };
}
