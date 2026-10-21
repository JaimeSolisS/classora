import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { SchedulingWarning } from './api';

// The translated message of one workload warning (#50 C1, C2): the date or
// the week, the limit and the total (daily, weekly), or the gap and the
// required break. Warnings never block; they're shown with a 201 and with
// each suggested time.

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function minutes(value: unknown): number {
  return typeof value === 'number' ? value : Number(value ?? 0);
}

export function useWarningMessage(): (warning: SchedulingWarning) => string {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();

  return ({ code, details }) => {
    switch (code) {
      case 'daily_limit_exceeded': {
        const date = text(details.date);
        return t('scheduling.warnings.daily_limit_exceeded', {
          date: date ? formatLocalDate(date) : '',
          scheduled: minutes(details.scheduled_minutes),
          limit: minutes(details.limit_minutes),
        });
      }
      case 'weekly_limit_exceeded': {
        const week = text(details.week_start);
        return t('scheduling.warnings.weekly_limit_exceeded', {
          week: week ? formatLocalDate(week) : '',
          scheduled: minutes(details.scheduled_minutes),
          limit: minutes(details.limit_minutes),
        });
      }
      case 'break_too_short':
        return t('scheduling.warnings.break_too_short', {
          count: minutes(details.gap_minutes),
          required: minutes(details.required_break_minutes),
        });
      default:
        return t('scheduling.warnings.unknown');
    }
  };
}
