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
  type Option,
  type SchedulingConflict,
} from '../create/api';
import type { ClassSeriesDetail } from '../session/api';
import {
  ClassroomSelect,
  DialogFailure,
  ScopeFields,
  SlotInput,
} from './dialogParts';
import { useFieldMessage, useReturnFocus } from './hooks';
import { RECURRENCE_FIELD_ORDER, recurrenceFieldId } from './recurrence';
import { RecurrenceFields } from './RecurrenceFields';
import {
  bodyError,
  hasErrors,
  seriesEditServerErrors,
  seriesEditValues,
  validateSeriesEdit,
  type SeriesEditErrors,
  type SeriesEditValues,
} from './rules';
import { SeriesConflictPanel } from './SeriesConflictPanel';

// "Edit series" (#54b C2): the scope ("This and following, from <date>",
// the date defaulting to the next occurrence, or "All future
// occurrences"), then the start, end, room and the weekly rule, pre-filled
// from the series (a COUNT rule ends "On date" = its last occurrence, so
// the end stays the same). The page sends POST /class-series/{id}/edits
// with the series version; this dialog shows the date-grouped 409, the
// 422s on their fields (series_unchanged above them), a version conflict
// with a reload, and the other 409s (series_offer_pending, series_ended,
// occurrence_started) in the shared ErrorAlert. Before submit it says that
// past and completed classes never change and edited occurrences in scope
// take the new times.

export interface EditSeriesDialogProps {
  open: boolean;
  series: ClassSeriesDetail;
  /** The scope's default date (the next occurrence's local date). */
  defaultFromDate: string;
  classrooms: readonly Option[];
  timeZone: string;
  /** Sends the edit; resolves once the dialog may close. */
  onSubmit: (values: SeriesEditValues) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
  /** Stories: values and an outcome to start from. */
  initialValues?: SeriesEditValues;
  initialFailure?: unknown;
  initialConflicts?: SchedulingConflict[];
}

export function EditSeriesDialog(props: EditSeriesDialogProps) {
  const busy = useRef(false);
  const { t } = useTranslation();
  const returnFocus = useReturnFocus();
  return (
    <Dialog
      open={props.open}
      onOpenChange={(open) => {
        if (!open && !busy.current) {
          props.onCancel();
        }
      }}
    >
      {props.open ? (
        <DialogContent
          {...returnFocus}
          onInteractOutside={(event) => event.preventDefault()}
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-xl"
        >
          <DialogHeader>
            <DialogTitle>{t('scheduling.series.edit.title')}</DialogTitle>
            <DialogDescription>
              {t('scheduling.series.edit.description')}
            </DialogDescription>
          </DialogHeader>
          <EditSeriesContent
            {...props}
            busyRef={busy}
            key={props.series.version}
          />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

const NO_ERRORS: SeriesEditErrors = { slot: {}, recurrence: {} };

function EditSeriesContent({
  series,
  defaultFromDate,
  classrooms,
  timeZone,
  onSubmit,
  onCancel,
  onReload,
  initialValues,
  initialFailure,
  initialConflicts,
  busyRef,
}: EditSeriesDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const id = useId();
  const [values, setValues] = useState<SeriesEditValues>(
    () => initialValues ?? seriesEditValues(series, defaultFromDate),
  );
  const [errors, setErrors] = useState<SeriesEditErrors>(NO_ERRORS);
  const [formError, setFormError] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<SchedulingConflict[] | null>(
    initialConflicts ?? null,
  );
  const [failure, setFailure] = useState<unknown>(initialFailure ?? null);
  const [submitting, setSubmitting] = useState(false);
  const recurrencePrefix = `${id}-recurrence`;

  function focus(found: SeriesEditErrors) {
    let target: string | undefined;
    if (found.fromDate) {
      target = `${id}-scope-from`;
    } else {
      const slot = (['start', 'end', 'classroom'] as const).find(
        (field) => found.slot[field],
      );
      const rule = RECURRENCE_FIELD_ORDER.find(
        (field) => found.recurrence[field],
      );
      if (slot) {
        target = `${id}-${slot}`;
      } else if (rule) {
        target = recurrenceFieldId(recurrencePrefix, rule);
      }
    }
    if (target) {
      document.getElementById(target)?.focus();
    }
  }

  async function send() {
    setConflicts(null);
    setFailure(null);
    setFormError(null);
    const found = validateSeriesEdit(values, defaultFromDate);
    setErrors(found);
    if (hasErrors(found)) {
      focus(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values);
    } catch (caught) {
      const list = conflictsOf(caught);
      if (list) {
        setConflicts(list);
      } else if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
        const mapped = seriesEditServerErrors(caught.fieldErrors);
        const onBody = bodyError(caught.fieldErrors);
        if (hasErrors(mapped) || onBody) {
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
  const noticeId = `${id}-notice`;

  return (
    <form
      noValidate
      onSubmit={onFormSubmit}
      aria-describedby={noticeId}
      className="flex min-w-0 flex-col gap-4"
    >
      {conflicts ? (
        <SeriesConflictPanel
          conflicts={conflicts}
          heading={t('scheduling.series.conflicts.editHeading', {
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
      <ScopeFields
        idPrefix={`${id}-scope`}
        mode={values.mode}
        fromDate={values.fromDate}
        error={message(errors.fromDate)}
        disabled={submitting}
        onChange={(mode, fromDate) =>
          setValues((current) => ({ ...current, mode, fromDate }))
        }
      />
      <p id={zoneId} className="text-sm text-muted-foreground">
        {t('scheduling.zone', { zone: timeZone })}
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <SlotInput
          id={`${id}-start`}
          label={t('scheduling.fields.start')}
          type="time"
          value={values.start}
          error={message(errors.slot.start)}
          describedBy={zoneId}
          disabled={submitting}
          onChange={(start) => setValues((current) => ({ ...current, start }))}
        />
        <SlotInput
          id={`${id}-end`}
          label={t('scheduling.fields.end')}
          type="time"
          value={values.end}
          error={message(errors.slot.end)}
          describedBy={zoneId}
          disabled={submitting}
          onChange={(end) => setValues((current) => ({ ...current, end }))}
        />
      </div>
      <ClassroomSelect
        id={`${id}-classroom`}
        value={values.classroomId}
        classrooms={classrooms}
        current={
          series.classroom
            ? { id: series.classroom.id, name: series.classroom.name }
            : null
        }
        error={message(errors.slot.classroom)}
        disabled={submitting}
        onChange={(classroomId) =>
          setValues((current) => ({ ...current, classroomId }))
        }
      />
      <RecurrenceFields
        idPrefix={recurrencePrefix}
        values={values.recurrence}
        errors={errors.recurrence}
        disabled={submitting}
        onChange={(recurrence) =>
          setValues((current) => ({ ...current, recurrence }))
        }
      />
      <p id={noticeId} className="rounded-md border border-border p-3 text-sm">
        {t('scheduling.series.edit.notice')}
      </p>
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
            t('scheduling.series.edit.submit')
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
