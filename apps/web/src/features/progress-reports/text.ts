import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { errorMessage } from '@/app/errors';
import { toLanguage, type Language } from '@/i18n/language';
import { useFormatters } from '@/lib/formatters';
import { parseLocalDate } from '@/lib/time';
import type { ReportKind } from './api';
import type { ReportMessage } from './rules';

// The texts shared by the progress report pages (#315 C1, E1, F1): a
// report's kind and period ("Weekly · Week of Oct 5, 2026 – Oct 11, 2026",
// "Monthly · October 2026", "On demand · Oct 1, 2026 – Oct 24, 2026"), a
// month's label, and the text of a field message.

/** "October 2026" / "octubre de 2026": the month of a calendar date (no
 * zone: the date is read as UTC). */
export function monthLabel(date: string, language: Language): string {
  const [year, month] = parseLocalDate(date).split('-').map(Number) as [
    number,
    number,
  ];
  return new Intl.DateTimeFormat(language === 'es' ? 'es-MX' : 'en-US', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, month - 1, 1)));
}

export interface PeriodLike {
  kind: ReportKind;
  period_start: string;
  period_end: string;
}

/** The period alone, and "{kind} · {period}". */
export function usePeriodText() {
  const { t, i18n } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const language = toLanguage(i18n.resolvedLanguage);
  function period(report: PeriodLike): string {
    if (report.kind === 'monthly') {
      return monthLabel(report.period_start, language);
    }
    const range = {
      start: formatLocalDate(report.period_start),
      end: formatLocalDate(report.period_end),
    };
    return report.kind === 'weekly'
      ? t('progressReports.period.week', range)
      : t('progressReports.period.range', range);
  }
  return {
    period,
    kind: (kind: ReportKind) => t(`progressReports.kinds.${kind}`),
    line: (report: PeriodLike) =>
      t('progressReports.period.line', {
        kind: t(`progressReports.kinds.${report.kind}`),
        period: period(report),
      }),
  };
}

/** The text of a field message: a `common` key, or `errors:<code>`. */
export function useReportMessage() {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  return (message: ReportMessage | null | undefined): string | undefined => {
    if (!message) {
      return undefined;
    }
    if (message.startsWith('errors:')) {
      return errorMessage(
        new ApiError({ code: message.slice('errors:'.length), status: 422 }),
        tErrors,
      );
    }
    return (t as unknown as (key: string) => string)(message);
  };
}
