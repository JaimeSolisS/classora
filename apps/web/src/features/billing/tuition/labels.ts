import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { PeriodFrequency } from '@/lib/tuition';
import type { ServicePrice } from '../pricing/api';
import { usePriceLabels } from '../pricing/labels';
import type { TuitionPlan } from './api';

// The words of a tuition plan (#374 C1, E1): the plan (subject, level or
// "any level", group or "Individual", frequency), the current amount or
// "Not offered", the dates ("Open-ended"), the due rule and a period's
// dates; and a per-period catalog price as a choice.

export function useTuitionLabels() {
  const { t } = useTranslation();
  const prices = usePriceLabels();
  const { formatMoney, formatLocalDate } = useFormatters();

  const frequency = (value: PeriodFrequency) => prices.frequency(value);
  const subject = (plan: Pick<TuitionPlan, 'service_price'>) =>
    t('billing.tuition.planSubject', {
      subject: plan.service_price.subject.name,
      level:
        plan.service_price.education_level?.name ??
        t('billing.tuition.anyLevel'),
    });
  const scope = (plan: Pick<TuitionPlan, 'group'>) =>
    plan.group?.name ?? t('billing.tuition.individual');

  return {
    frequency,
    subject,
    scope,
    /** "Álgebra · Any level · Individual · Monthly", on one line. */
    plan: (plan: Pick<TuitionPlan, 'service_price' | 'group' | 'frequency'>) =>
      t('billing.tuition.planLine', {
        subject: subject(plan),
        scope: scope(plan),
        frequency: frequency(plan.frequency),
      }),
    amount: (plan: Pick<TuitionPlan, 'current_amount'>) =>
      plan.current_amount === null
        ? t('billing.prices.notOffered')
        : formatMoney(plan.current_amount),
    startsOn: (plan: Pick<TuitionPlan, 'starts_on'>) =>
      formatLocalDate(plan.starts_on),
    endsOn: (plan: Pick<TuitionPlan, 'ends_on'>) =>
      plan.ends_on === null
        ? t('billing.tuition.openEnded')
        : formatLocalDate(plan.ends_on),
    due: (plan: Pick<TuitionPlan, 'due_offset_days'>) =>
      plan.due_offset_days === 0
        ? t('billing.tuition.dueOnStart')
        : t('billing.tuition.dueAfter', { count: plan.due_offset_days }),
    period: (period: { period_start: string; period_end: string }) =>
      t('billing.tuition.periodRange', {
        from: formatLocalDate(period.period_start),
        to: formatLocalDate(period.period_end),
      }),
    date: (value: string) => formatLocalDate(value),
    /** A per-period catalog price as a create choice. */
    priceOption: (price: ServicePrice) =>
      t('billing.tuition.priceOption', {
        subject: t('billing.tuition.planSubject', {
          subject: price.subject.name,
          level: price.education_level?.name ?? t('billing.tuition.anyLevel'),
        }),
        scope:
          price.class_type === 'individual'
            ? t('billing.tuition.individual')
            : (price.group?.name ?? t('billing.tuition.anyGroup')),
        frequency: price.period_frequency
          ? frequency(price.period_frequency)
          : '',
        amount: prices.current(price),
      }),
  };
}
