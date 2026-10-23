import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  conflictsOf,
  type SchedulingConflict,
} from '@/features/scheduling/create/api';
import { ConflictPanel } from '@/features/scheduling/create/ConflictPanel';
import { OverrideDialog } from '@/features/scheduling/create/OverrideDialog';
import {
  overrideAction,
  overrideReasonError,
} from '@/features/scheduling/create/rules';
import { SlotInput } from '@/features/scheduling/series/dialogParts';
import { useFieldMessage } from '@/features/scheduling/series/hooks';
import {
  bodyError,
  slotServerErrors,
  validateOccurrence,
  type SlotErrors,
} from '@/features/scheduling/series/rules';
import type { CalendarItem, ClassSessionUpdate } from '../api';
import type { LabelContext } from '../labels';
import {
  overrideBody,
  refusalKind,
  rescheduleSlot,
  rescheduleValues,
  type RescheduleValues,
} from './rules';

// "Reschedule" (#58 E1-E4): the keyboard and single-pointer alternative to
// dragging (WCAG 2.2 SC 2.1.1, 2.5.7). Date, start and end in the center
// zone (shown), prefilled from the event as loaded (a reload with a new
// version starts the form again); the room is shown read-only, unchanged.
// The client checks only the required fields and "end after start"; the
// PATCH is the drop's, through the same `send`, so the server decides the
// rest. A 409 scheduling_conflict shows the conflict panel inside the
// dialog (the form keeps its values) with the #49 F2 override action; a
// 422 goes on the fields (session_unchanged above them); any other refusal
// (a version conflict, a session that can't move any more, a network error)
// goes to `onRefused`, which closes the dialog and shows the page notice.
// Focus starts on the date (from `onOpenAutoFocus`, never `autoFocus`) and
// is trapped; Esc closes it unless a request is running.

export interface RescheduleDialogProps {
  item: CalendarItem | null;
  context: LabelContext;
  canOverride: boolean;
  /** Sends the move; resolves once it is saved (the caller closes). */
  send: (item: CalendarItem, body: ClassSessionUpdate) => Promise<unknown>;
  /** A refusal the dialog doesn't show itself. */
  onRefused: (
    item: CalendarItem,
    body: ClassSessionUpdate,
    error: unknown,
  ) => void;
  onCancel: () => void;
  onCloseAutoFocus: (event: Event) => void;
  /** Stories: an outcome to start from. */
  initialConflicts?: SchedulingConflict[];
  initialErrors?: SlotErrors;
}

export function RescheduleDialog(props: RescheduleDialogProps) {
  const { t } = useTranslation();
  const busy = useRef(false);
  const content = useRef<HTMLDivElement>(null);
  const { item, onCancel, onCloseAutoFocus } = props;
  return (
    <Dialog
      open={item !== null}
      onOpenChange={(open) => {
        if (!open && !busy.current) {
          onCancel();
        }
      }}
    >
      {item ? (
        <DialogContent
          ref={content}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            content.current
              ?.querySelector<HTMLInputElement>('input[type="date"]')
              ?.focus();
          }}
          onCloseAutoFocus={onCloseAutoFocus}
          onInteractOutside={(event) => event.preventDefault()}
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg"
        >
          <DialogHeader>
            <DialogTitle>
              {t('scheduling.calendar.reschedule.title')}
            </DialogTitle>
            <DialogDescription>
              {t('scheduling.calendar.reschedule.description', {
                subject: item.subject.name,
              })}
            </DialogDescription>
          </DialogHeader>
          <RescheduleForm
            {...props}
            item={item}
            busyRef={busy}
            // A reload with a new version starts the form again (#235 QA D1).
            key={`${item.id}-${item.version}`}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function RescheduleForm({
  item,
  context,
  canOverride,
  send,
  onRefused,
  onCancel,
  initialConflicts,
  initialErrors,
  busyRef,
}: RescheduleDialogProps & {
  item: CalendarItem;
  busyRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const id = useId();
  const { timeZone } = context;
  const [values, setValues] = useState<RescheduleValues>(() =>
    rescheduleValues(item, timeZone),
  );
  const [errors, setErrors] = useState<SlotErrors>(initialErrors ?? {});
  const [formError, setFormError] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(
    initialConflicts ?? null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [overriding, setOverriding] = useState(false);
  const lastBody = useRef<ClassSessionUpdate | null>(
    initialConflicts
      ? { version: item.version, ...rescheduleSlot(values, timeZone) }
      : null,
  );
  const panel = useRef<HTMLDivElement>(null);
  const overrideButton = useRef<HTMLButtonElement>(null);
  const focusAfter = useRef<'button' | 'panel' | 'none'>('button');

  const fieldId = (field: 'date' | 'start' | 'end') => `${id}-${field}`;
  const zoneId = `${id}-zone`;

  function set<K extends keyof RescheduleValues>(key: K, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function focusFirst(found: SlotErrors) {
    const first = (['date', 'start', 'end'] as const).find((f) => found[f]);
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  }

  /** A 422 on the fields or the body; false when it isn't one. */
  function showFieldErrors(error: unknown): boolean {
    if (!(error instanceof ApiError) || error.fieldErrors.length === 0) {
      return false;
    }
    const mapped = slotServerErrors(error.fieldErrors);
    const onBody = bodyError(error.fieldErrors);
    if (Object.keys(mapped).length === 0 && !onBody) {
      return false;
    }
    setErrors(mapped);
    setFormError(onBody);
    focusFirst(mapped);
    return true;
  }

  async function submit() {
    setConflicts(null);
    setFormError(null);
    const found = validateOccurrence({ ...values, classroomId: '' });
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    const body: ClassSessionUpdate = {
      version: item.version,
      ...rescheduleSlot(values, timeZone),
    };
    lastBody.current = body;
    busyRef.current = true;
    setSubmitting(true);
    try {
      await send(item, body);
    } catch (caught) {
      const list = conflictsOf(caught);
      if (list) {
        setConflicts(list);
      } else if (!showFieldErrors(caught)) {
        onRefused(item, body, caught);
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  async function confirmOverride(reason: string) {
    const base = lastBody.current;
    if (!base || !conflicts) {
      return;
    }
    const body = overrideBody(base, conflicts, reason);
    busyRef.current = true;
    try {
      await send(item, body);
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
        setConflicts(list);
        return;
      }
      if (onReason) {
        throw caught;
      }
      const kind = refusalKind(caught);
      if (kind === 'invalid') {
        focusAfter.current = 'none';
        setOverriding(false);
        setConflicts(null);
        showFieldErrors(caught);
        return;
      }
      if (kind === 'stale' || kind === 'gone') {
        focusAfter.current = 'none';
        setOverriding(false);
        onRefused(item, body, caught);
        return;
      }
      // A 403 (the delegation was revoked) or a network error: the override
      // dialog's ErrorAlert.
      throw caught;
    } finally {
      busyRef.current = false;
    }
  }

  function afterOverride(event: Event) {
    event.preventDefault();
    const target = focusAfter.current;
    focusAfter.current = 'button';
    if (target === 'panel') {
      panel.current?.querySelector<HTMLElement>('h2')?.focus();
    } else if (target === 'button') {
      overrideButton.current?.focus();
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busyRef.current) {
      void submit();
    }
  }

  const action = conflicts ? overrideAction(conflicts, canOverride) : null;

  // The override dialog stays outside the form: React events bubble
  // through portals, so its submit would also submit this form.
  return (
    <>
      <form
        noValidate
        onSubmit={onSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        {conflicts ? (
          <div ref={panel} className="flex min-w-0 flex-col gap-3">
            <ConflictPanel
              conflicts={conflicts}
              heading={t('scheduling.calendar.move.conflicts', {
                count: conflicts.length,
              })}
            />
            {action === 'override' ? (
              <div>
                <Button
                  ref={overrideButton}
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
        ) : null}
        {formError ? (
          <p role="alert" className="text-sm text-destructive">
            {message(formError)}
          </p>
        ) : null}
        <p id={zoneId} className="text-sm text-muted-foreground">
          {t('scheduling.zone', { zone: timeZone })}
        </p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <SlotInput
            id={fieldId('date')}
            label={t('scheduling.fields.date')}
            type="date"
            value={values.date}
            error={message(errors.date)}
            disabled={submitting}
            onChange={(value) => set('date', value)}
          />
          <SlotInput
            id={fieldId('start')}
            label={t('scheduling.fields.start')}
            type="time"
            value={values.start}
            error={message(errors.start)}
            describedBy={zoneId}
            disabled={submitting}
            onChange={(value) => set('start', value)}
          />
          <SlotInput
            id={fieldId('end')}
            label={t('scheduling.fields.end')}
            type="time"
            value={values.end}
            error={message(errors.end)}
            describedBy={zoneId}
            disabled={submitting}
            onChange={(value) => set('end', value)}
          />
        </div>
        <dl className="grid gap-1 text-sm sm:grid-cols-[8rem_1fr] sm:gap-3">
          <dt className="font-medium">
            {t('scheduling.calendar.dialog.room')}
          </dt>
          <dd className="min-w-0 break-words">
            {item.classroom
              ? t('scheduling.calendar.reschedule.roomUnchanged', {
                  room: item.classroom.name,
                })
              : t('scheduling.calendar.reschedule.noRoomUnchanged')}
          </dd>
        </dl>
        {item.series_id ? (
          <p className="text-sm text-muted-foreground">
            {t('scheduling.calendar.reschedule.occurrenceNote')}
          </p>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onCancel}
          >
            {t('scheduling.calendar.reschedule.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner
                label={t('scheduling.calendar.reschedule.submitting')}
                showLabel
              />
            ) : (
              t('scheduling.calendar.reschedule.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
      <OverrideDialog
        open={overriding && action === 'override'}
        conflicts={(conflicts ?? []).filter((c) => c.severity === 'soft')}
        onConfirm={confirmOverride}
        onCancel={() => setOverriding(false)}
        onCloseAutoFocus={afterOverride}
        labels={{
          title: t('scheduling.calendar.move.overrideAction'),
          description: t('scheduling.calendar.move.overrideDescription'),
          submit: t('scheduling.calendar.move.overrideAction'),
          submitting: t('scheduling.calendar.move.overrideSubmitting'),
        }}
      />
    </>
  );
}
