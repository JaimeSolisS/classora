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
// Another write (#58: a calendar move) passes its own heading and hint, and
// can leave the focus to its dialog (`focusOnChange={false}`).

export function ConflictPanel({
  conflicts,
  heading,
  hint,
  focusOnChange = true,
}: {
  conflicts: readonly SchedulingConflict[];
  /** Default: "The session can't be scheduled: N conflicts". */
  heading?: string;
  /** Default: "Change the form and try again; nothing was saved." */
  hint?: string;
  focusOnChange?: boolean;
}) {
  const { t } = useTranslation();
  const message = useConflictMessage();
  const headingId = useId();
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (focusOnChange) {
      headingRef.current?.focus();
    }
  }, [conflicts, focusOnChange]);

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
          {heading ??
            t('scheduling.conflicts.heading', { count: conflicts.length })}
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
          {hint ?? t('scheduling.conflicts.keepForm')}
        </p>
      </div>
    </section>
  );
}
