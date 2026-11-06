import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { toLanguage, type Language } from '@/i18n/language';
import { addDays, addMonths } from '@/lib/time';

// The KPI figures as text (M8-09b C8): every value as the API sent it,
// formatted for the UI language (es-MX / en). Percentages are decimal
// strings with two places, or null (`—`); hours are scheduled minutes / 60
// with one decimal. Money uses the financial report's `Money` (es-MX MXN in
// both languages, ADR-0004).

type DecimalString = `${number}`;

const MONTH = /^(\d{4})-(\d{2})$/;

function locale(language: Language): string {
  return language === 'es' ? 'es-MX' : 'en-US';
}

/** A month's first and last dates ("2031-02" → 2031-02-01, 2031-02-28). */
export function monthBounds(month: string): { from: string; to: string } {
  const from = `${month}-01`;
  return { from, to: addDays(addMonths(from, 1), -1) };
}

/** Whether a KPI month is clipped by the range (B1 "(parcial)"). */
export function isPartialMonth(item: {
  month: string;
  from: string;
  to: string;
}): boolean {
  if (!MONTH.test(item.month)) return false;
  const bounds = monthBounds(item.month);
  return item.from !== bounds.from || item.to !== bounds.to;
}

/** "2031-03" as "mar 2031" / "Mar 2031" (formatted in UTC on both sides). */
export function kpiMonthLabel(month: string, language: Language): string {
  const match = MONTH.exec(month);
  if (!match) return month;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
  return new Intl.DateTimeFormat(locale(language), {
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(date);
}

/** Whether a decimal string is exactly zero. */
export function isZeroAmount(amount: string | null): boolean {
  return amount === null || !/[1-9]/.test(amount);
}

export interface KpiFormatters {
  language: Language;
  /** An integer count or minutes, grouped. */
  count: (value: number) => string;
  /** Scheduled minutes as hours with one decimal. */
  hours: (minutes: number) => string;
  /** A percent string ("9.48") as "9.48 %"; null as "—". */
  percent: (value: string | null) => string;
  month: (month: string) => string;
}

export function useKpiFormatters(): KpiFormatters {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  return useMemo(() => {
    const integer = new Intl.NumberFormat(locale(language), {
      maximumFractionDigits: 0,
    });
    const oneDecimal = new Intl.NumberFormat(locale(language), {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });
    const twoDecimals = new Intl.NumberFormat(locale(language), {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
    return {
      language,
      count: (value) => integer.format(value),
      hours: (minutes) => oneDecimal.format(minutes / 60),
      percent: (value) =>
        value === null
          ? t('reports.kpis.noPercent')
          : t('reports.kpis.percent', {
              value: twoDecimals.format(value as DecimalString),
            }),
      month: (month) => kpiMonthLabel(month, language),
    };
  }, [language, t]);
}
