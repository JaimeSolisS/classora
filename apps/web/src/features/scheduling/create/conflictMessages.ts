import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { SchedulingConflict } from './api';
import { isConflictCode } from './rules';

// The translated message of one conflict (#48 F3), shared by the conflict
// panel and the override dialog (#49 F3): the details that help (the
// tutor's reasons, the date, the opening hours, the notice hours).

const REASONS = ['time_off', 'outside_weekly_availability'] as const;

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

export function useConflictMessage(): (conflict: SchedulingConflict) => string {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();

  return (conflict) => {
    const { code, details } = conflict;
    if (!isConflictCode(code)) {
      return t('scheduling.conflicts.unknown');
    }
    const date = text(details.date);
    switch (code) {
      case 'tutor_unavailable': {
        const reasons = Array.isArray(details.reasons) ? details.reasons : [];
        const known = REASONS.filter((reason) => reasons.includes(reason));
        return t('scheduling.conflicts.tutor_unavailable', {
          reasons: known
            .map((reason) => t(`scheduling.reasons.${reason}`))
            .join(t('scheduling.reasons.separator')),
        });
      }
      case 'center_closed':
        return t('scheduling.conflicts.center_closed', {
          date: date ? formatLocalDate(date) : '',
        });
      case 'outside_operating_hours': {
        const opens = text(details.opens_at);
        const closes = text(details.closes_at);
        const day = date ? formatLocalDate(date) : '';
        return opens && closes
          ? t('scheduling.conflicts.outside_operating_hours', {
              date: day,
              opens,
              closes,
            })
          : t('scheduling.conflicts.outside_operating_hours_closed', {
              date: day,
            });
      }
      case 'notice_window':
        return t('scheduling.conflicts.notice_window', {
          hours: Number(details.booking_notice_hours ?? 0),
        });
      default:
        return t(`scheduling.conflicts.${code}`);
    }
  };
}
