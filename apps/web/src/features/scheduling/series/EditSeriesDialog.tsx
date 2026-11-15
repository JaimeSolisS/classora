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
import {
  DEFAULT_DURATION_MINUTES,
  lengthMinutes,
  withTimeFormat,
} from '../create/rules';
import { useEndFollowsStart } from '../create/useEndFollowsStart';
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
  clearSeriesEditServerErrors,
  hasErrors,
  seriesEditServerErrors,
  seriesEditValues,
  shownSeriesEditErrors,
  validateSeriesEdit,
  type SeriesEditErrors,
  type SeriesEditValues,
} from './rules';
import { seriesOverride, type SeriesOverride } from './overrides';
import { SeriesOverrideSection } from './SeriesOverrideSection';

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
// take the new times. Under the 409 the #236 override branches apply: the
// override dialog (nested) resends the same edit with every soft pair (the
// new instants the 409 names) and the reason.

export interface EditSeriesDialogProps {
  open: boolean;
  series: ClassSeriesDetail;
  /** The scope's default date (the next occurrence's local date). */
  defaultFromDate: string;
  classrooms: readonly Option[];
  timeZone: string;
  /** Sends the edit (with the override); resolves once the dialog may close. */
  onSubmit: (
    values: SeriesEditValues,
    override?: SeriesOverride,
  ) => Promise<void>;
  /** Whether the user holds schedule.override (#236). */
  canOverride: boolean;
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
  canOverride,
  busyRef,
}: EditSeriesDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const id = useId();
  const [values, setValues] = useState<SeriesEditValues>(
    () => initialValues ?? seriesEditValues(series, defaultFromDate),
  );
  // #600: the end follows the start per edit; an empty end (or one not
  // after the old start) gets the length the dialog opened with (E2 a).
  const [openedLength] = useState(
    () => lengthMinutes(values.start, values.end) ?? DEFAULT_DURATION_MINUTES,
  );
  const times = useEndFollowsStart(openedLength);
  // HF-13 (#598 E4): after the first submit the client checks follow the
  // values; the server's errors stay until their field changes.
  const [checked, setChecked] = useState(false);
  const [serverErrors, setServerErrors] = useState<SeriesEditErrors>(NO_ERRORS);
  const check = (): SeriesEditErrors => {
    const found = validateSeriesEdit(values, defaultFromDate);
    return { ...found, slot: withTimeFormat(found.slot, times.unreadable) };
  };
  const errors = shownSeriesEditErrors(checked, serverErrors, check);

  function update(change: Partial<SeriesEditValues>) {
    const next = { ...values, ...change };
    setValues(next);
    setServerErrors((current) =>
      clearSeriesEditServerErrors(current, values, next),
    );
  }

  /** A typed or picked start or end (#600): a new start moves the end. */
  function setTime(field: 'start' | 'end', value: string, unreadable: boolean) {
    update(times.change(values, field, value, unreadable));
  }
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
    const found = check();
    setChecked(true);
    setServerErrors(NO_ERRORS);
    if (hasErrors(found)) {
      focus(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values);
    } catch (caught) {
      handleFailure(caught);
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  function handleFailure(caught: unknown) {
    const list = conflictsOf(caught);
    if (list) {
      setConflicts(list);
      return;
    }
    setConflicts(null);
    if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
      const mapped = seriesEditServerErrors(caught.fieldErrors);
      const onBody = bodyError(caught.fieldErrors);
      if (hasErrors(mapped) || onBody) {
        setServerErrors(mapped);
        setFormError(onBody);
        focus(mapped);
        return;
      }
    }
    setFailure(caught);
  }

  async function override(reason: string) {
    if (!conflicts) {
      return;
    }
    busyRef.current = true;
    try {
      await onSubmit(values, seriesOverride(conflicts, reason));
    } finally {
      busyRef.current = false;
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
        <SeriesOverrideSection
          conflicts={conflicts}
          heading={t('scheduling.series.conflicts.editHeading', {
            count: conflicts.length,
          })}
          canOverride={canOverride}
          onOverride={override}
          onRefused={handleFailure}
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
        onChange={(mode, fromDate) => update({ mode, fromDate })}
      />
      <p id={zoneId} className="text-sm text-muted-foreground">
        {t('scheduling.zone', { zone: timeZone })}
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <SlotInput
          id={`${id}-start`}
          label={t('scheduling.fields.start')}
          kind="time"
          value={values.start}
          error={message(errors.slot.start)}
          describedBy={zoneId}
          disabled={submitting}
          onChange={(start, unreadable) => setTime('start', start, unreadable)}
          onEditEnd={times.endEdit}
        />
        <SlotInput
          id={`${id}-end`}
          label={t('scheduling.fields.end')}
          kind="time"
          value={values.end}
          error={message(errors.slot.end)}
          describedBy={zoneId}
          disabled={submitting}
          scrollTo={times.endScrollTo(values.start)}
          onChange={(end, unreadable) => setTime('end', end, unreadable)}
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
        onChange={(classroomId) => update({ classroomId })}
      />
      <RecurrenceFields
        idPrefix={recurrencePrefix}
        values={values.recurrence}
        errors={errors.recurrence}
        disabled={submitting}
        onChange={(recurrence) => update({ recurrence })}
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
