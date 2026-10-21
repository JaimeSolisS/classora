import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useLanguage } from '@/i18n/use-language';
import type { SummaryContext } from './summary';

/** The center zone and the active language, for the summaries' times. */
export function useSummaryContext(): SummaryContext {
  const timeZone = useCenterTimeZone();
  const { language } = useLanguage();
  return { timeZone, language };
}
