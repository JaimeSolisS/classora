import { i18n } from '@/i18n';
import type { Language } from '@/i18n/language';
import { formatLocalDate } from '@/lib/time';
import { weekdayList } from '@/features/tutor-portal/offers/rules';

// The rule summary (#54b A4): "Every 2 weeks on Monday and Thursday, until
// Dec 18, 2026", from the structured fields only. It never parses `rrule`
// (ADR-0005 D4). Pure: the language is an argument, so the text doesn't
// follow the active language unless the caller passes it.

export interface RecurrenceFields {
  weekdays: readonly number[];
  interval: number;
  until: string | null;
  count: number | null;
}

type Weekday = '1' | '2' | '3' | '4' | '5' | '6' | '7';

export function describeRecurrence(
  fields: RecurrenceFields,
  language: Language,
): string {
  const t = i18n.getFixedT(language, 'common');
  const days = weekdayList(
    fields.weekdays,
    (day) => {
      const name = t(`weeklyWindows.weekdays.${String(day) as Weekday}`);
      // Spanish day names are lower case inside a sentence ("los lunes").
      return language === 'es' ? name.toLocaleLowerCase('es') : name;
    },
    language,
  );
  const rule =
    fields.interval > 1
      ? t('scheduling.series.describe.everyWeeks', {
          count: fields.interval,
          days,
        })
      : t('scheduling.series.describe.everyWeek', { days });
  if (fields.until) {
    return t('scheduling.series.describe.until', {
      rule,
      date: formatLocalDate(fields.until, { language }),
    });
  }
  if (fields.count !== null) {
    return t('scheduling.series.describe.count', {
      rule,
      count: fields.count,
    });
  }
  return t('scheduling.series.describe.noEnd', { rule });
}
