import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { toLanguage } from '@/i18n/language';
import { formatMoney } from './money';
import {
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
 * Formatters bound to the active UI language and the center time zone (the
 * business setting from the session, #37). Components using it re-render on
 * a language or zone change.
 */
export function useFormatters(): Formatters {
  const { i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const timeZone = useCenterTimeZone();
  return useMemo(
    () => ({
      formatInstant: (instant, style) =>
        formatInstant(instant, {
          timeZone,
          language,
          style,
        }),
      formatLocalDate: (localDate, style) =>
        formatLocalDate(localDate, { language, style }),
      formatMoney,
    }),
    [language, timeZone],
  );
}
