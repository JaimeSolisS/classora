import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { toLanguage } from '@/i18n/language';
import { formatMoney } from './money';
import {
  DEFAULT_CENTER_TIME_ZONE,
  formatInstant,
  formatLocalDate,
  type Instant,
  type InstantStyle,
  type LocalDate,
  type LocalDateStyle,
} from './time';

export interface Formatters {
  formatInstant: (instant: Instant, style?: InstantStyle) => string;
  formatLocalDate: (
    localDate: LocalDate | string,
    style?: LocalDateStyle,
  ) => string;
  formatMoney: (amount: string) => string;
}

/**
 * Formatters bound to the active UI language and the center time zone
 * (DEFAULT_CENTER_TIME_ZONE until M3-01). Components using it re-render on a
 * language change.
 */
export function useFormatters(): Formatters {
  const { i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  return useMemo(
    () => ({
      formatInstant: (instant, style) =>
        formatInstant(instant, {
          timeZone: DEFAULT_CENTER_TIME_ZONE,
          language,
          style,
        }),
      formatLocalDate: (localDate, style) =>
        formatLocalDate(localDate, { language, style }),
      formatMoney,
    }),
    [language],
  );
}
