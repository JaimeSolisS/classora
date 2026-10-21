import { useTranslation } from 'react-i18next';

/** Minutes as "15 h" / "15 h 30 min" / "45 min". */
export function useDuration() {
  const { t } = useTranslation();
  return (minutes: number) => {
    const hours = Math.floor(minutes / 60);
    const rest = minutes % 60;
    if (hours === 0) {
      return t('courses.templates.minutesOnly', { count: rest });
    }
    if (rest === 0) {
      return t('courses.templates.hours', { count: hours });
    }
    return t('courses.templates.hoursMinutes', { hours, minutes: rest });
  };
}
