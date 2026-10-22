import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { Button } from '@/components/ui/button';
import { toLanguage } from '@/i18n/language';
import { formatLocalDate } from '@/lib/time';
import { conflictsOf, type SchedulingConflict } from '../create/api';
import { useConflictMessage } from '../create/conflictMessages';
import { OverrideDialog } from '../create/OverrideDialog';
import { overrideAction, overrideReasonError } from '../create/rules';
import { affectedOccurrences, softConflictsByDate } from './overrides';
import { SeriesConflictPanel } from './SeriesConflictPanel';

// The date-grouped 409 of a series create or edit (#236 H1, H2): the panel
// ("Can be overridden" / "Not allowed"), then the #49 F2 branches under it:
// a holder of schedule.override with only soft conflicts gets "Override and
// schedule"; with a hard one too, "Fix the conflicts that aren't allowed
// first."; anyone else reads who can override. The button opens the #49
// dialog (its reason field and rules), here listing the soft conflicts by
// local date with how many occurrences they affect, all overridden
// together. `onOverride` resends the same write with every soft pair: it
// resolves once the write is done (the caller shows its success), and
// rejects with the error. A new 409 (or a 422 on the write's own fields)
// closes the dialog and goes to `onRefused` (the panel shows the new list,
// focus on its heading); a 422 on the reason stays in the dialog, and
// anything else (a 403) is the dialog's ErrorAlert.

export interface SeriesOverrideSectionProps {
  conflicts: readonly SchedulingConflict[];
  heading: string;
  canOverride: boolean;
  /** Resends the write with the trimmed reason (and every soft pair). */
  onOverride: (reason: string) => Promise<void>;
  /** A new conflict list or a field error, after the dialog closed. */
  onRefused: (error: unknown) => void;
  /** The reason to start from, with the dialog open (stories). */
  initialReason?: string;
}

export function SeriesOverrideSection({
  conflicts,
  heading,
  canOverride,
  onOverride,
  onRefused,
  initialReason,
}: SeriesOverrideSectionProps) {
  const { t, i18n } = useTranslation();
  const language = toLanguage(i18n.resolvedLanguage);
  const message = useConflictMessage();
  const [open, setOpen] = useState(initialReason !== undefined);
  const button = useRef<HTMLButtonElement>(null);
  const section = useRef<HTMLDivElement>(null);
  // Where the focus goes once the dialog closes: its button (cancel), or
  // the panel's heading (a new list).
  const focusAfter = useRef<'button' | 'panel'>('button');
  const action = overrideAction(conflicts, canOverride);
  const soft = conflicts.filter((c) => c.severity === 'soft');

  async function confirm(reason: string) {
    try {
      await onOverride(reason);
      setOpen(false);
    } catch (caught) {
      const list = conflictsOf(caught);
      const onReason =
        caught instanceof ApiError
          ? overrideReasonError(caught.fieldErrors)
          : null;
      const onForm =
        caught instanceof ApiError &&
        !onReason &&
        caught.fieldErrors.length > 0;
      if (list || onForm) {
        focusAfter.current = list ? 'panel' : 'button';
        setOpen(false);
        onRefused(caught);
        return;
      }
      throw caught;
    }
  }

  function afterDialog(event: Event) {
    event.preventDefault();
    const target = focusAfter.current;
    focusAfter.current = 'button';
    if (target === 'panel') {
      section.current?.querySelector<HTMLElement>('h2')?.focus();
    } else {
      button.current?.focus();
    }
  }

  return (
    <div ref={section} className="flex min-w-0 flex-col gap-3">
      <SeriesConflictPanel conflicts={conflicts} heading={heading} />
      {action === 'override' ? (
        <div>
          <Button ref={button} type="button" onClick={() => setOpen(true)}>
            {t('scheduling.override.action')}
          </Button>
        </div>
      ) : (
        <p className="text-sm">
          {action === 'fixHardFirst'
            ? t('scheduling.override.fixHardFirst')
            : t('scheduling.override.notAllowed')}
        </p>
      )}
      <OverrideDialog
        open={open && action === 'override'}
        conflicts={soft}
        initialReason={initialReason}
        onConfirm={confirm}
        onCancel={() => setOpen(false)}
        onCloseAutoFocus={afterDialog}
        labels={{
          title: t('scheduling.override.title'),
          description: t('scheduling.series.override.description', {
            count: affectedOccurrences(soft),
          }),
          submit: t('scheduling.override.submit'),
          submitting: t('scheduling.override.submitting'),
        }}
        rules={
          <ul className="flex flex-col gap-2">
            {softConflictsByDate(soft).map((group) => (
              <li key={group.date} className="flex flex-col gap-1">
                <h4 className="text-sm font-medium">
                  {group.date
                    ? formatLocalDate(group.date, { language, style: 'long' })
                    : t('scheduling.series.conflicts.noDate')}
                </h4>
                <ul className="flex list-disc flex-col gap-1 pl-5 text-sm">
                  {group.conflicts.map((conflict, index) => (
                    <li
                      key={`${conflict.code}-${index}`}
                      className="break-words"
                    >
                      {message(conflict)}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        }
      />
    </div>
  );
}
