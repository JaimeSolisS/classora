import { CircleAlert } from 'lucide-react';
import { useEffect, useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { toLanguage } from '@/i18n/language';
import { formatLocalDate } from '@/lib/time';
import type { SchedulingConflict } from '../create/api';
import { useConflictMessage } from '../create/conflictMessages';
import { conflictsByDate } from './rules';

// A series offer's 409 scheduling_conflict (#233 G2): every conflict of
// every covered occurrence, grouped by its local date, as a plain list
// (role="alert"; the focus moves to the heading when the list changes).
// No override: a series offer takes none (E3). A cohort's offer (#243 D4)
// passes its own `noOverride` text (it has no "this session only").

export function SeriesConflictList({
  conflicts,
  noOverride,
}: {
  conflicts: readonly SchedulingConflict[];
  noOverride?: string;
}) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const message = useConflictMessage();
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    heading.current?.focus();
  }, [conflicts]);

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
          {t('scheduling.offer.series.conflictsHeading', {
            count: conflicts.length,
          })}
        </h2>
        <ul className="flex flex-col gap-3">
          {conflictsByDate(conflicts).map((group) => (
            <li key={group.date} className="flex flex-col gap-1">
              <h3 className="text-sm font-medium">
                {group.date
                  ? formatLocalDate(group.date, { language })
                  : t('scheduling.offer.series.noDate')}
              </h3>
              <ul className="flex flex-col gap-1 pl-4 text-sm">
                {group.conflicts.map((conflict, index) => (
                  <li
                    key={`${conflict.code}-${index}`}
                    className="list-disc break-words"
                  >
                    {message(conflict)}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
        <p className="text-sm">
          {noOverride ?? t('scheduling.offer.series.noOverride')}
        </p>
      </div>
    </section>
  );
}
