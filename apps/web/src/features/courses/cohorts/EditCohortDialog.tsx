import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
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
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { CohortDetail, CohortUpdate } from './api';
import {
  editServerErrors,
  editValuesOf,
  hasChanges,
  MAX_STUDENTS,
  NAME_MAX_LENGTH,
  roomLimited,
  updateBody,
  validateEdit,
  type EditErrors,
  type EditField,
  type EditValues,
} from './rules';

// "Edit" on the cohort page (#243 D5): the name (1-120 characters,
// trimmed) and the maximum (1-500), pre-filled. The PATCH sends the
// version and only the changed fields; Save is disabled while nothing
// changed (the client side of `422 no_changes`). cohort_name_taken goes on
// the name. A maximum above the room's capacity is allowed; the field then
// says the room still limits the sessions; one below the students enrolled
// (`422 max_students_below_enrolled`) says how many are (#245 D1). A
// version conflict says "This
// cohort changed. Reload to see the latest." with a reload action. The
// focus returns to "Edit" when the dialog closes (#234 QA D1).

export interface EditCohortDialogProps {
  open: boolean;
  cohort: CohortDetail;
  /** The current room's capacity, when known. */
  roomCapacity: number | null;
  onSubmit: (body: CohortUpdate) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
  /** Stories: values and an outcome to start from. */
  initialValues?: EditValues;
  initialErrors?: EditErrors;
  initialFailure?: unknown;
}

export function EditCohortDialog(props: EditCohortDialogProps) {
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
          className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg"
        >
          <DialogHeader>
            <DialogTitle>{t('courses.cohorts.edit.title')}</DialogTitle>
            <DialogDescription>
              {t('courses.cohorts.edit.description')}
            </DialogDescription>
          </DialogHeader>
          <EditContent {...props} key={props.cohort.version} busyRef={busy} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.code === 'version_conflict';
}

function EditContent({
  cohort,
  roomCapacity,
  onSubmit,
  onCancel,
  onReload,
  initialValues,
  initialErrors,
  initialFailure = null,
  busyRef,
}: EditCohortDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  // #245 D1: "At least {n} students are enrolled", n from the loaded cohort.
  const maxMessage = (key: EditErrors['max']) =>
    key === 'courses.cohorts.enrollment.belowEnrolled'
      ? t('courses.cohorts.enrollment.belowEnrolled', {
          count: cohort.enrolled_count ?? 0,
        })
      : message(key);
  const id = useId();
  const fieldId = (field: EditField) => `${id}-${field}`;
  const errorId = (field: EditField) => `${id}-${field}-error`;
  const [values, setValues] = useState<EditValues>(
    () => initialValues ?? editValuesOf(cohort),
  );
  const [errors, setErrors] = useState<EditErrors>(initialErrors ?? {});
  const [failure, setFailure] = useState<unknown>(initialFailure);
  const [submitting, setSubmitting] = useState(false);
  const changed = hasChanges(cohort, values);
  const typedMax = Number(values.max.trim());
  const limit = roomLimited(cohort) ? cohort.effective_capacity : roomCapacity;
  const overRoom =
    limit !== null && Number.isFinite(typedMax) && typedMax > limit;

  function focus(found: EditErrors) {
    const first = (['name', 'max'] as const).find((field) => found[field]);
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  }

  async function send() {
    setFailure(null);
    const found = validateEdit(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focus(found);
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(updateBody(cohort, values));
    } catch (caught) {
      const mapped = caught instanceof ApiError ? editServerErrors(caught) : {};
      if (Object.keys(mapped).length > 0) {
        setErrors(mapped);
        focus(mapped);
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
    if (!busyRef.current && changed) {
      void send();
    }
  }

  const hintId = `${id}-max-hint`;
  const describedBy = (field: EditField, extra?: string) =>
    [extra, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ') || undefined;

  return (
    <form
      noValidate
      onSubmit={onFormSubmit}
      className="flex min-w-0 flex-col gap-4"
    >
      {failure && isVersionConflict(failure) ? (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-md border border-destructive p-3"
        >
          <p className="font-medium">
            {t('courses.cohorts.edit.versionConflict')}
          </p>
          <div>
            <Button type="button" variant="outline" onClick={onReload}>
              {t('courses.cohorts.edit.reload')}
            </Button>
          </div>
        </div>
      ) : failure ? (
        <ErrorAlert error={failure} />
      ) : null}
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId('name')} className="text-sm font-medium">
          {t('courses.cohorts.fields.name')}
        </label>
        <Input
          id={fieldId('name')}
          value={values.name}
          maxLength={NAME_MAX_LENGTH + 20}
          disabled={submitting}
          aria-invalid={errors.name ? true : undefined}
          aria-describedby={describedBy('name')}
          onChange={(event) =>
            setValues((current) => ({ ...current, name: event.target.value }))
          }
        />
        <FieldError id={errorId('name')} message={message(errors.name)} />
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId('max')} className="text-sm font-medium">
          {t('courses.cohorts.fields.max')}
        </label>
        <Input
          id={fieldId('max')}
          type="number"
          inputMode="numeric"
          min={1}
          max={MAX_STUDENTS}
          step={1}
          value={values.max}
          disabled={submitting}
          aria-invalid={errors.max ? true : undefined}
          aria-describedby={describedBy('max', overRoom ? hintId : undefined)}
          onChange={(event) =>
            setValues((current) => ({ ...current, max: event.target.value }))
          }
        />
        {overRoom ? (
          <p id={hintId} className="text-sm text-muted-foreground">
            {t('courses.cohorts.edit.roomLimit', { capacity: limit })}
          </p>
        ) : null}
        <FieldError id={errorId('max')} message={maxMessage(errors.max)} />
      </div>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={submitting}
          onClick={onCancel}
        >
          {t('courses.cohorts.edit.cancel')}
        </Button>
        <Button type="submit" disabled={submitting || !changed}>
          {submitting ? (
            <Spinner label={t('courses.cohorts.edit.submitting')} showLabel />
          ) : (
            t('courses.cohorts.edit.submit')
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
