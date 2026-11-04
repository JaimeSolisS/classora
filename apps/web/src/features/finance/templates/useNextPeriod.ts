import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { ExpenseTemplate } from '../api';

/** A template's next period, or "—" once it ended (M7-13b B1, C1). */
export function useNextPeriod() {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  return (template: Pick<ExpenseTemplate, 'status' | 'next_period_start'>) =>
    template.status === 'ended'
      ? t('finance.templates.none')
      : formatLocalDate(template.next_period_start);
}
