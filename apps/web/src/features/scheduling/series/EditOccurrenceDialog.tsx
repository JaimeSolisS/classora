import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
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
  type Option,
  type SchedulingConflict,
} from '../create/api';
import type { ClassSessionDetail } from '../session/api';
import type { SeriesOccurrence } from './api';
import { ClassroomSelect, DialogFailure, SlotInput } from './dialogParts';
import { useFieldMessage, useReturnFocus } from './hooks';
import {
  bodyError,
  occurrenceValues,
  slotServerErrors,
  validateOccurrence,
  type OccurrenceValues,
  type SlotErrors,
  type SlotField,
} from './rules';
import { SeriesConflictPanel } from './SeriesConflictPanel';

// "Edit this occurrence" (#54b C1): the date, start, end and room of one
// occurrence, pre-filled from the row (times in the center zone) and its
// session (the room and the version, read when the dialog opens). The page
// sends PATCH /class-sessions/{id}; this dialog shows what comes back: a
// 409 scheduling_conflict as the conflict panel inside the dialog, a 409
// version_conflict as "This changed since you opened it" with a reload, a
// 422 on the fields (session_unchanged above them), anything else
// (session_not_editable, …) in the shared ErrorAlert. Focus is trapped,
// returns to the trigger, and Esc closes it unless a request is running.

export interface EditOccurrenceDialogProps {
  occurrence: SeriesOccurrence | null;
  /** The occurrence's session (its version and room); null while loading. */
  session: ClassSessionDetail | null;
  sessionError?: unknown;
  classrooms: readonly Option[];
  timeZone: string;
  /** Sends the PATCH; resolves once the dialog may close. */
  onSubmit: (values: OccurrenceValues, version: number) => Promise<void>;
  onCancel: () => void;
  /** Reads the session and the series again (after a version conflict). */
  onReload: () => void;
  /** Stories: an outcome to start from. */
  initialFailure?: unknown;
  initialConflicts?: SchedulingConflict[];
}

export function EditOccurrenceDialog(props: EditOccurrenceDialogProps) {
  const busy = useRef(false);
  const { t } = useTranslation();
  const returnFocus = useReturnFocus();
  const { occurrence, onCancel } = props;
  return (
    <Dialog
      open={occurrence !== null}
      onOpenChange={(open) => {
        if (!open && !busy.current) {
          onCancel();
        }
      }}
    >
      {occurrence ? (
        <DialogContent
          {...returnFocus}
          onInteractOutside={(event) => event.preventDefault()}
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg"
        >
          <DialogHeader>
            <DialogTitle>{t('scheduling.series.occurrence.title')}</DialogTitle>
            <DialogDescription>
              {t('scheduling.series.occurrence.description')}
            </DialogDescription>
          </DialogHeader>
          <EditOccurrenceContent
            {...props}
            occurrence={occurrence}
            busyRef={busy}
            // A reload with a new version starts the form again.
            key={`${occurrence.id}-${props.session?.version ?? 'loading'}`}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function EditOccurrenceContent({
  occurrence,
  session,
  sessionError,
  classrooms,
  timeZone,
  onSubmit,
  onCancel,
  onReload,
  initialFailure,
  initialConflicts,
  busyRef,
}: EditOccurrenceDialogProps & {
  occurrence: SeriesOccurrence;
  busyRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const id = useId();
  const [values, setValues] = useState<OccurrenceValues>(() =>
    // The session as read (after a reload: its current times, not the row's).
    occurrenceValues(
      session ?? occurrence,
      session?.classroom?.id ?? null,
      timeZone,
    ),
  );
  const [errors, setErrors] = useState<SlotErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(
    initialConflicts ?? null,
  );
  const [failure, setFailure] = useState<unknown>(initialFailure ?? null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: SlotField) => `${id}-${field}`;

  function set<K extends keyof OccurrenceValues>(
    key: K,
    value: OccurrenceValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function focus(found: SlotErrors) {
    const first = (['date', 'start', 'end', 'classroom'] as const).find(
      (field) => found[field],
    );
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  }

  async function send() {
    if (!session) {
      return;
    }
    setConflicts(null);
    setFailure(null);
    setFormError(null);
    const found = validateOccurrence(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focus(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values, session.version);
    } catch (caught) {
      const list = conflictsOf(caught);
      if (list) {
        setConflicts(list);
      } else if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
        const mapped = slotServerErrors(caught.fieldErrors);
        const onBody = bodyError(caught.fieldErrors);
        if (Object.keys(mapped).length > 0 || onBody) {
          setErrors(mapped);
          setFormError(onBody);
          focus(mapped);
        } else {
          setFailure(caught);
        }
      } else {
        setFailure(caught);
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busyRef.current) {
      void send();
    }
  }

  const zoneId = `${id}-zone`;
  let body;
  if (sessionError) {
    body = <ErrorAlert error={sessionError} onRetry={onReload} />;
  } else if (!session) {
    body = <LoadingState />;
  } else {
    body = (
      <form
        noValidate
        onSubmit={onFormSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        {conflicts ? (
          <SeriesConflictPanel
            conflicts={conflicts}
            grouped={false}
            overridable={false}
            heading={t('scheduling.series.conflicts.occurrenceHeading', {
              count: conflicts.length,
            })}
          />
        ) : null}
        {failure ? <DialogFailure error={failure} onReload={onReload} /> : null}
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
        <ClassroomSelect
          id={fieldId('classroom')}
          value={values.classroomId}
          classrooms={classrooms}
          current={
            session.classroom
              ? { id: session.classroom.id, name: session.classroom.name }
              : null
          }
          error={message(errors.classroom)}
          disabled={submitting}
          onChange={(value) => set('classroomId', value)}
        />
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onCancel}
          >
            {t('scheduling.series.dialogCancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('scheduling.series.saving')} showLabel />
            ) : (
              t('scheduling.series.occurrence.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  return body;
}
