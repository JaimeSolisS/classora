import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type {
  ClassType,
  PriceVersion,
  PricingModel,
  ServicePrice,
} from './api';

// The words of a price catalog item (#363 B2, D1): the service (class type ·
// pricing model), the subject, the level, the duration, the scope and a
// version's amount, shared by the list, the detail header and the dialogs.

export function usePriceLabels() {
  const { t } = useTranslation();
  const { formatMoney, formatLocalDate } = useFormatters();

  const classType = (value: ClassType) =>
    t(`billing.prices.classTypes.${value}`);
  const pricingModel = (classType: ClassType, model: PricingModel) =>
    // A group's full_course is a flat fee per enrollment; a course's is
    // the course fee.
    model === 'full_course'
      ? classType === 'group'
        ? t('billing.prices.pricingModels.flat_fee')
        : t('billing.prices.pricingModels.course_fee')
      : t('billing.prices.pricingModels.per_session');
  const amount = (version: PriceVersion) =>
    version.amount === null
      ? t('billing.prices.notOffered')
      : formatMoney(version.amount);

  return {
    classType,
    pricingModel,
    service: (item: Pick<ServicePrice, 'class_type' | 'pricing_model'>) =>
      t('billing.prices.service', {
        classType: classType(item.class_type),
        pricingModel: pricingModel(item.class_type, item.pricing_model),
      }),
    subject: (item: Pick<ServicePrice, 'subject'>) =>
      t('billing.prices.subjectOfLevel', {
        level: item.subject.education_level_name,
        subject: item.subject.name,
      }),
    level: (item: Pick<ServicePrice, 'education_level'>) =>
      item.education_level?.name ?? t('billing.prices.any'),
    duration: (item: Pick<ServicePrice, 'duration_minutes'>) =>
      item.duration_minutes === null
        ? t('billing.prices.any')
        : t('billing.prices.minutes', { minutes: item.duration_minutes }),
    scope: (item: Pick<ServicePrice, 'cohort' | 'group'>) =>
      item.cohort?.name ?? item.group?.name ?? t('billing.prices.none'),
    amount,
    current: (item: Pick<ServicePrice, 'current'>) =>
      item.current ? amount(item.current) : t('billing.prices.noPriceYet'),
    next: (item: Pick<ServicePrice, 'next'>) =>
      item.next
        ? t('billing.prices.nextChange', {
            date: formatLocalDate(item.next.effective_from),
            amount: amount(item.next),
          })
        : t('billing.prices.none'),
    date: (version: PriceVersion) => formatLocalDate(version.effective_from),
  };
}
