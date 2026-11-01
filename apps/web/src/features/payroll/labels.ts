import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import { formatRate } from '@/lib/money';
import type {
  ClassKind,
  CompensationRate,
  PayFrequency,
  PayTerms,
  RateModel,
} from './api';

// The words of a pay version (#425 C1-C4, G2), shared by the tutor list, the
// compensation view and the delete confirmation. A salary always names its
// period, so it isn't read as a monthly figure (C4).

export function usePayLabels() {
  const { t } = useTranslation();
  const { formatMoney, formatLocalDate } = useFormatters();

  const frequency = (value: PayFrequency) => t(`payroll.frequencies.${value}`);
  const salary = (amount: string | null, value: PayFrequency) =>
    amount === null
      ? t('payroll.compensation.noSalary')
      : t('payroll.compensation.perPeriod', {
          amount: formatMoney(amount),
          frequency: t(`payroll.frequencyWords.${value}`),
        });

  return {
    frequency,
    salary,
    kind: (value: ClassKind) => t(`payroll.classKinds.${value}`),
    model: (value: RateModel) => t(`payroll.models.${value}`),
    date: (item: { effective_from: string }) =>
      formatLocalDate(item.effective_from),
    terms: (item: PayTerms) =>
      item.pay_frequency === null
        ? t('payroll.compensation.stoppedSince', {
            date: formatLocalDate(item.effective_from),
          })
        : t('payroll.compensation.termsValue', {
            frequency: frequency(item.pay_frequency),
            salary: salary(item.salary_amount, item.pay_frequency),
          }),
    rate: (item: CompensationRate) =>
      item.rate === null || item.rate_model === null
        ? t('payroll.compensation.noRateSince', {
            date: formatLocalDate(item.effective_from),
          })
        : t('payroll.compensation.rateValue', {
            model: t(`payroll.models.${item.rate_model}`),
            rate: formatRate(item.rate),
          }),
  };
}
