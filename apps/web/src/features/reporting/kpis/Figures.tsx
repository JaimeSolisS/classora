import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import { useKpiFormatters } from './format';

// A KPI percentage (M8-09b C4, C8): the API's two-decimal string,
// localized, or "—" with a screen-reader text when it is null.

export function Percent({
  value,
  missing,
}: {
  value: string | null;
  /** The screen-reader text for a null value (default "No data"). */
  missing?: string;
}) {
  const { t } = useTranslation();
  const format = useKpiFormatters();
  if (value === null) {
    return (
      <span data-percent="none">
        <span aria-hidden="true">{format.percent(null)}</span>
        <span className="sr-only">{missing ?? t('reports.kpis.noData')}</span>
      </span>
    );
  }
  return (
    <span data-percent className="whitespace-nowrap">
      {format.percent(value)}
    </span>
  );
}

/** A local month label with "(partial)" when the range clips it (C1). */
export function MonthLabel({
  month,
  from,
  to,
  partial,
}: {
  month: string;
  from: string;
  to: string;
  partial: boolean;
}) {
  const { t } = useTranslation();
  const format = useKpiFormatters();
  const { formatLocalDate } = useFormatters();
  if (!partial) return <>{format.month(month)}</>;
  const range = t('reports.kpis.partialRange', {
    from: formatLocalDate(from),
    to: formatLocalDate(to),
  });
  return (
    <span title={range} data-partial-month={month}>
      {format.month(month)} {t('reports.kpis.partial')}
      <span className="sr-only">{` ${range}`}</span>
    </span>
  );
}
