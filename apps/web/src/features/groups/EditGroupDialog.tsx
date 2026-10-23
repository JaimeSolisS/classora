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
import { Input } from '@/components/ui/input';
import { FieldError } from '@/features/auth/FieldError';
import { DialogFailure } from '@/features/scheduling/series/dialogParts';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { GroupDetail } from './api';
import {
  editServerErrors,
  editValuesOf,
  hasChanges,
  MAX_STUDENTS,
  NAME_MAX_LENGTH,
  updateBody,
  validateEdit,
  type EditErrors,
  type EditField,
  type EditValues,
} from './rules';

// "Edit" (#234 D4): the name (1-120 characters, trimmed) and the maximum
// (1-500), pre-filled. The PATCH sends the version and only the changed
// fields; the submit is disabled while nothing changed (the client side of
// `422 no_changes`). group_name_taken goes on the name, and
// max_students_below_enrolled on the maximum with the active count. A
// maximum above the room's capacity is allowed; the field then says the
// room still limits the sessions. A version conflict offers a reload.

export interface EditGroupDialogProps {
  open: boolean;
  group: GroupDetail;
  /** The current room's capacity, when known. */
  roomCapacity: number | null;
  onSubmit: (body: ReturnType<typeof updateBody>) => Promise<void>;
  onCancel: () => void;
  onReload: () => void;
  /** Stories: values and an outcome to start from. */
  initialValues?: EditValues;
  initialErrors?: EditErrors;
}

export function EditGroupDialog(props: EditGroupDialogProps) {
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
            <DialogTitle>{t('groups.edit.title')}</DialogTitle>
            <DialogDescription>
              {t('groups.edit.description')}
            </DialogDescription>
          </DialogHeader>
          <EditContent {...props} key={props.group.version} busyRef={busy} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function EditContent({
  group,
  roomCapacity,
  onSubmit,
  onCancel,
  onReload,
  initialValues,
  initialErrors,
  busyRef,
}: EditGroupDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const id = useId();
  const fieldId = (field: EditField) => `${id}-${field}`;
  const errorId = (field: EditField) => `${id}-${field}-error`;
  const [values, setValues] = useState<EditValues>(
    () => initialValues ?? editValuesOf(group),
  );
  const [errors, setErrors] = useState<EditErrors>(initialErrors ?? {});
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const changed = hasChanges(group, values);
  const typedMax = Number(values.max.trim());
  const overRoom =
    roomCapacity !== null &&
    Number.isFinite(typedMax) &&
    typedMax > roomCapacity;

  function text(field: EditField): string | undefined {
    const key = errors[field];
    if (key === 'groups.edit.belowEnrolled') {
      return t('groups.edit.belowEnrolled', {
        count: group.enrolled_count ?? 0,
      });
    }
    return message(key);
  }

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
      await onSubmit(updateBody(group, values));
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
      {failure ? <DialogFailure error={failure} onReload={onReload} /> : null}
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId('name')} className="text-sm font-medium">
          {t('groups.fields.name')}
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
        <FieldError id={errorId('name')} message={text('name')} />
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId('max')} className="text-sm font-medium">
          {t('groups.fields.max')}
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
            {t('groups.capacity.roomLimit', { capacity: roomCapacity })}
          </p>
        ) : null}
        <FieldError id={errorId('max')} message={text('max')} />
      </div>
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={submitting}
          onClick={onCancel}
        >
          {t('groups.cancel')}
        </Button>
        <Button type="submit" disabled={submitting || !changed}>
          {submitting ? (
            <Spinner label={t('groups.edit.submitting')} showLabel />
          ) : (
            t('groups.edit.submit')
          )}
        </Button>
      </DialogFooter>
    </form>
  );
}
