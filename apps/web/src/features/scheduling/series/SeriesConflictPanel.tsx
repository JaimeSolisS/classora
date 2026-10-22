import { CircleAlert } from 'lucide-react';
import { useEffect, useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { toLanguage } from '@/i18n/language';
import { formatLocalDate } from '@/lib/time';
import type { SchedulingConflict } from '../create/api';
import { useConflictMessage } from '../create/conflictMessages';
import { orderedConflicts } from '../create/rules';
import { conflictsByDate } from '../session/rules';

// A series 409 scheduling_conflict (#54b A3, C1, C2): the #48 panel with
// its severity labels, grouped by each occurrence's local date
// (`details.date`), hard first within a date. A series create or edit can
// override its soft conflicts (#236 H1), so they say "Can be overridden";
// the PATCH of one occurrence takes no override until #58
// (`overridable={false}`: "Can't be overridden here yet"). role="alert";
// the focus moves to the heading when the list changes. One occurrence's
// 409 (the PATCH) is one plain list. A group write (#234) passes which
// soft conflicts it accepts (`accepts`; the others say `noOverrideLabel`)
// and a heading for the conflicts of the whole group (no date), listed
// first.

export interface SeriesConflictPanelProps {
  conflicts: readonly SchedulingConflict[];
  /** The heading, with the count. */
  heading: string;
  /** false: one list, no dates (one occurrence). */
  grouped?: boolean;
  /** false: soft conflicts can't be overridden on this write (the PATCH). */
  overridable?: boolean;
  /** Which soft conflicts this write can override (default: `overridable`). */
  accepts?: (conflict: SchedulingConflict) => boolean;
  /** The label of a soft conflict it can't (default: the series text). */
  noOverrideLabel?: string;
  /** The heading of the conflicts without a date (default: "Other"). */
  noDateHeading?: string;
}

export function SeriesConflictPanel({
  conflicts,
  heading,
  grouped = true,
  overridable = true,
  accepts,
  noOverrideLabel,
  noDateHeading,
}: SeriesConflictPanelProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const message = useConflictMessage();
  const headingId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, [conflicts]);

  function list(items: readonly SchedulingConflict[]) {
    return (
      <ul className="flex flex-col gap-2">
        {orderedConflicts(items).map((conflict, index) => (
          <li
            key={`${conflict.code}-${index}`}
            className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2"
          >
            <span className="w-fit shrink-0 rounded-sm border border-border px-1.5 text-xs font-medium">
              {conflict.severity === 'hard'
                ? t('scheduling.severity.hard')
                : (accepts ? accepts(conflict) : overridable)
                  ? t('scheduling.severity.soft')
                  : (noOverrideLabel ??
                    t('scheduling.series.conflicts.noOverride'))}
            </span>
            <span className="min-w-0 break-words">{message(conflict)}</span>
          </li>
        ))}
      </ul>
    );
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
          ref={headingRef}
          tabIndex={-1}
          className="font-medium text-destructive outline-none focus-visible:ring-3 focus-visible:ring-ring"
        >
          {heading}
        </h2>
        {grouped ? (
          <ul className="flex flex-col gap-3">
            {conflictsByDate(conflicts).map((group) => (
              <li key={group.date} className="flex flex-col gap-2">
                <h3 className="text-sm font-medium">
                  {group.date
                    ? formatLocalDate(group.date, { language, style: 'long' })
                    : (noDateHeading ??
                      t('scheduling.series.conflicts.noDate'))}
                </h3>
                {list(group.conflicts)}
              </li>
            ))}
          </ul>
        ) : (
          list(conflicts)
        )}
        <p className="text-sm text-muted-foreground">
          {t('scheduling.conflicts.keepForm')}
        </p>
      </div>
    </section>
  );
}
