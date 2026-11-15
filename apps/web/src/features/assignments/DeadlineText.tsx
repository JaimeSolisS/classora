import { useTranslation } from 'react-i18next';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useFormatters } from '@/lib/formatters';
import { todayInZone } from '@/lib/time';
import type { SubmissionState } from './api';
import { deadlineParts } from './rules';

// The deadline text of a homework item (#313 E5): "Due {date}" or "Due
// {date} at {time}" (the local date as it is; the time of the deadline
// instant in the center zone), plus "today", "tomorrow" or "in {n} days"
// for a pending item due within 7 days (from local dates; `now` is
// injectable), "Missing: the deadline was {date}" for a missing one and
// "Handed in late" for a late submission.

export interface DeadlineItem {
  due_on: string;
  due_time: string | null;
  deadline_at: string;
  state: SubmissionState;
  is_late: boolean;
}

export function useDeadlineText(now: () => number = Date.now) {
  const { t } = useTranslation();
  const { formatInstant, formatLocalDate } = useFormatters();
  const zone = useCenterTimeZone();
  return (item: DeadlineItem): string => {
    const parts = deadlineParts(item, todayInZone(zone, new Date(now())));
    const date = formatLocalDate(parts.dueOn);
    const when = parts.dueTime
      ? t('assignments.deadline.dateTime', {
          date,
          time: formatInstant(item.deadline_at, 'time'),
        })
      : date;
    if (parts.missing) {
      return t('assignments.deadline.missing', { when });
    }
    const pieces = [t('assignments.deadline.due', { when })];
    if (parts.relative?.kind === 'today') {
      pieces.push(t('assignments.deadline.today'));
    } else if (parts.relative?.kind === 'tomorrow') {
      pieces.push(t('assignments.deadline.tomorrow'));
    } else if (parts.relative?.kind === 'days') {
      pieces.push(
        t('assignments.deadline.inDays', { count: parts.relative.days }),
      );
    }
    if (parts.late) {
      pieces.push(t('assignments.deadline.late'));
    }
    return pieces.join(' · ');
  };
}

/** "Due {date}[ at {time}]" for an assignment (no state: the staff views). */
export function useDueText() {
  const { t } = useTranslation();
  const { formatInstant, formatLocalDate } = useFormatters();
  return (item: Pick<DeadlineItem, 'due_on' | 'due_time' | 'deadline_at'>) =>
    t('assignments.deadline.due', {
      when: item.due_time
        ? t('assignments.deadline.dateTime', {
            date: formatLocalDate(item.due_on),
            time: formatInstant(item.deadline_at, 'time'),
          })
        : formatLocalDate(item.due_on),
    });
}
