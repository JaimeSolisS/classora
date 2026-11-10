import { useTranslation } from 'react-i18next';
import type { Language } from '@/i18n/language';
import { formatHour, type CenterHours } from './hours';
import type { CalendarView } from './views';

// HF-08: the hours a time grid shows, in words (the shading never carries
// them alone), next to the time zone line. Day and Week only.

/** "Center hours: 9:00 AM–8:00 PM", in Day and Week only. */
export function CenterHoursCaption({
  hours,
  view,
  language,
}: {
  hours: CenterHours | null;
  view: CalendarView;
  language: Language;
}) {
  const { t } = useTranslation();
  if (!hours || (view !== 'day' && view !== 'week')) {
    return null;
  }
  const values = {
    from: formatHour(hours.from, language),
    to: formatHour(hours.to, language),
  };
  return (
    <p className="text-sm text-muted-foreground">
      {hours.outside
        ? t('scheduling.calendar.hours.outside', values)
        : t('scheduling.calendar.hours.center', values)}
    </p>
  );
}
