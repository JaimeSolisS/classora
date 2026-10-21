import { CircleAlert } from 'lucide-react';
import { useEffect, useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useFormatters } from '@/lib/formatters';
import type { SchedulingConflict } from './api';
import { isConflictCode, orderedConflicts } from './rules';

// The 409 scheduling_conflict panel (#48 F3): role="alert", the focus moves
// to its heading, and each conflict shows a translated message (with the
// details that help: the tutor's reasons, the date, the opening hours, the
// notice hours) and a severity label. Soft conflicts can't be overridden
// until #49 adds the override dialog.

const REASONS = ['time_off', 'outside_weekly_availability'] as const;

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

export function ConflictPanel({
  conflicts,
}: {
  conflicts: readonly SchedulingConflict[];
}) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    heading.current?.focus();
  }, [conflicts]);

  function message(conflict: SchedulingConflict): string {
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
  }

  return (
    <section
      role="alert"
      aria-labelledby={headingId}
      className="flex gap-3 rounded-md border border-destructive bg-background p-4 text-foreground"
    >
      <CircleAlert
        aria-hidden="true"
        className="mt-0.5 size-5 shrink-0 text-destructive"
      />
      <div className="flex min-w-0 flex-col gap-3">
        <h2
          id={headingId}
          ref={heading}
          tabIndex={-1}
          className="font-medium text-destructive outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {t('scheduling.conflicts.heading', { count: conflicts.length })}
        </h2>
        <ul className="flex flex-col gap-2">
          {orderedConflicts(conflicts).map((conflict, index) => (
            <li
              key={`${conflict.code}-${index}`}
              className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2"
            >
              <span className="w-fit rounded-sm border border-border px-1.5 text-xs font-medium">
                {conflict.severity === 'hard'
                  ? t('scheduling.severity.hard')
                  : t('scheduling.severity.soft')}
              </span>
              <span className="min-w-0 break-words">{message(conflict)}</span>
            </li>
          ))}
        </ul>
        <p className="text-sm text-muted-foreground">
          {t('scheduling.conflicts.keepForm')}
        </p>
      </div>
    </section>
  );
}
