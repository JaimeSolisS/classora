import { CircleAlert } from 'lucide-react';
import { useEffect, useId, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import type { SchedulingConflict } from './api';
import { useConflictMessage } from './conflictMessages';
import { orderedConflicts } from './rules';

// The 409 scheduling_conflict panel (#48 F3, #49 F1): role="alert", the
// focus moves to its heading whenever the list changes, and each conflict
// shows a translated message and a label: "Not allowed" (hard) or "Can be
// overridden" (soft). The override action is rendered under it by the form.

export function ConflictPanel({
  conflicts,
}: {
  conflicts: readonly SchedulingConflict[];
}) {
  const { t } = useTranslation();
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
          {t('scheduling.conflicts.heading', { count: conflicts.length })}
        </h2>
        <ul className="flex flex-col gap-2">
          {orderedConflicts(conflicts).map((conflict, index) => (
            <li
              key={`${conflict.code}-${index}`}
              className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2"
            >
              <span className="w-fit shrink-0 rounded-sm border border-border px-1.5 text-xs font-medium">
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
