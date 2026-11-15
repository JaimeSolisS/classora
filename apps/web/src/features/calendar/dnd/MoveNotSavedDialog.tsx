import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { SchedulingConflict } from '@/features/scheduling/create/api';
import { conflictsOf } from '@/features/scheduling/create/api';
import { ConflictPanel } from '@/features/scheduling/create/ConflictPanel';
import { OverrideDialog } from '@/features/scheduling/create/OverrideDialog';
import {
  overrideAction,
  overrideReasonError,
} from '@/features/scheduling/create/rules';
import { formatLocalDate, toLocalDate } from '@/lib/time';
import { sessionTimeText, type LabelContext } from '../labels';
import type { RefusedMove } from './useMoveSession';

// "Move not saved" (#58 F1-F3): a refused drop (409 scheduling_conflict)
// in a dialog, so the panel doesn't push the grid. The title names the
// attempted local date and times; the #48/#49 conflict panel lists every
// conflict ("Can be overridden" / "Not allowed"). Under it, the #49 F2
// branches: a holder of schedule.override with only soft conflicts gets
// "Override and move" (the #49 override dialog, every soft code together,
// a required reason); with a hard conflict, "Fix the conflicts that aren't
// allowed first."; anyone else reads who can override.
//
// `onOverride(reason)` resends the same body and version with the override.
// It resolves once the dialog may close (moved, or the caller turned a
// version conflict into its reload notice) and rejects with the error: a
// new 409 closes the override dialog and the panel shows the new list
// (focus on its heading); a 422 on the reason stays on the field; anything
// else (a 403) is the override dialog's ErrorAlert. The focus starts on the
// title; on close the caller puts it back on the event.

export interface MoveNotSavedDialogProps {
  refused: RefusedMove | null;
  context: LabelContext;
  canOverride: boolean;
  onOverride: (reason: string) => Promise<void>;
  /** A new conflict list after an override was refused. */
  onConflicts: (conflicts: SchedulingConflict[]) => void;
  onClose: () => void;
  onCloseAutoFocus: (event: Event) => void;
  /** Stories: the override dialog open with this reason. */
  initialReason?: string;
}

export function MoveNotSavedDialog({
  refused,
  context,
  canOverride,
  onOverride,
  onConflicts,
  onClose,
  onCloseAutoFocus,
  initialReason,
}: MoveNotSavedDialogProps) {
  const { t } = useTranslation();
  const title = useRef<HTMLHeadingElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const [overriding, setOverriding] = useState(initialReason !== undefined);
  // Where the focus goes once the override dialog closes: its button
  // (cancel), the panel's heading (a new list), or nowhere (moved: this
  // dialog closes too and the caller places the focus).
  const focusAfter = useRef<'button' | 'panel' | 'none'>('button');

  const conflicts = refused?.conflicts ?? [];
  const action = overrideAction(conflicts, canOverride);
  const soft = conflicts.filter((c) => c.severity === 'soft');

  async function confirm(reason: string) {
    try {
      await onOverride(reason);
      focusAfter.current = 'none';
      setOverriding(false);
    } catch (caught) {
      const list = conflictsOf(caught);
      const onReason =
        caught instanceof ApiError
          ? overrideReasonError(caught.fieldErrors)
          : null;
      if (list && !onReason) {
        focusAfter.current = 'panel';
        setOverriding(false);
        onConflicts(list);
        return;
      }
      throw caught;
    }
  }

  function afterOverride(event: Event) {
    event.preventDefault();
    const target = focusAfter.current;
    focusAfter.current = 'button';
    if (target === 'panel') {
      panel.current?.querySelector<HTMLElement>('h2')?.focus();
    } else if (target === 'button') {
      button.current?.focus();
    }
  }

  const slot = refused
    ? {
        date: formatLocalDate(
          toLocalDate(refused.body.starts_at, context.timeZone),
          { language: context.language, style: 'long' },
        ),
        time: sessionTimeText(refused.body, context),
      }
    : null;

  return (
    <Dialog
      open={refused !== null}
      onOpenChange={(open) => {
        if (!open && !overriding) {
          onClose();
        }
      }}
    >
      {refused && slot ? (
        <DialogContent
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            title.current?.focus();
          }}
          onCloseAutoFocus={onCloseAutoFocus}
          // One "Close": the button under the panel.
          showCloseButton={false}
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg"
        >
          <DialogHeader>
            <DialogTitle
              ref={title}
              tabIndex={-1}
              className="outline-none focus-visible:ring-3 focus-visible:ring-ring"
            >
              {t('scheduling.calendar.move.notSaved')}
            </DialogTitle>
            <DialogDescription>
              {t('scheduling.calendar.move.attempted', {
                subject: refused.item.subject.name,
                date: slot.date,
                time: slot.time,
                zone: context.timeZone,
              })}
            </DialogDescription>
          </DialogHeader>
          <div ref={panel} className="flex min-w-0 flex-col gap-3">
            <ConflictPanel
              conflicts={conflicts}
              heading={t('scheduling.calendar.move.conflicts', {
                count: conflicts.length,
              })}
              hint={t('scheduling.calendar.move.backInPlace')}
              focusOnChange={false}
            />
            {action === 'override' ? (
              <div>
                <Button
                  ref={button}
                  type="button"
                  onClick={() => setOverriding(true)}
                >
                  {t('scheduling.calendar.move.overrideAction')}
                </Button>
              </div>
            ) : (
              <p className="text-sm">
                {action === 'fixHardFirst'
                  ? t('scheduling.override.fixHardFirst')
                  : t('scheduling.override.notAllowed')}
              </p>
            )}
          </div>
          <div className="flex justify-end">
            <Button type="button" variant="outline" onClick={onClose}>
              {t('scheduling.calendar.move.close')}
            </Button>
          </div>
          <OverrideDialog
            open={overriding && action === 'override'}
            conflicts={soft}
            initialReason={initialReason}
            onConfirm={confirm}
            onCancel={() => setOverriding(false)}
            onCloseAutoFocus={afterOverride}
            labels={{
              title: t('scheduling.calendar.move.overrideAction'),
              description: t('scheduling.calendar.move.overrideDescription'),
              submit: t('scheduling.calendar.move.overrideAction'),
              submitting: t('scheduling.calendar.move.overrideSubmitting'),
            }}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
