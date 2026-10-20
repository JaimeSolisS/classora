import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { WeeklyWindowsEditor } from '@/components/weekly-windows/WeeklyWindowsEditor';
import {
  hasWindowErrors,
  rowsOf,
  validateWindows,
  windowFieldId,
  windowsOf,
  type WindowRow,
  type WindowRowErrors,
} from '@/components/weekly-windows/windows';
import { FieldError } from '@/features/auth/FieldError';
import { z } from '@/lib/forms';
import type { Classroom } from './api';
import {
  CAPACITY_MAX,
  CAPACITY_MIN,
  type ClassroomFormValues,
} from './classroomRules';
import { NAME_MAX_LENGTH } from './rules';

// The add / edit dialog of a classroom (#39 D3, D4): name, capacity, the
// weekly windows editor and, when editing, Deactivate / Reactivate. Without
// any API call: the page passes `onSubmit` and `onSetActive`, which resolve
// once the change is stored, and the dialog then closes. Errors stay in the
// dialog: name_taken on the name, a server field error on its field (a
// window's on its row), version_conflict with "Reload", anything else in
// the alert region. The windows are checked on save (as the API checks
// them) and again on every change after a failed save. Focus returns to the
// opener.

type MessageKey =
  | 'catalog.validation.nameRequired'
  | 'catalog.validation.nameTooLong'
  | 'catalog.classrooms.validation.capacityRange';

const NAME_TAKEN = 'errors:name_taken';
const WHOLE_NUMBER = /^[0-9]+$/;

const formSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'catalog.validation.nameRequired')
    .max(NAME_MAX_LENGTH, 'catalog.validation.nameTooLong'),
  capacity: z
    .string()
    .trim()
    .refine((value) => {
      const number = Number(value);
      return (
        WHOLE_NUMBER.test(value) &&
        number >= CAPACITY_MIN &&
        number <= CAPACITY_MAX
      );
    }, 'catalog.classrooms.validation.capacityRange'),
});

interface FormFields {
  name: string;
  /** As typed; a whole number from 1 to 500 when valid. */
  capacity: string;
}

export interface ClassroomDialogProps {
  open: boolean;
  /** The classroom being edited; null to add one. */
  classroom: Classroom | null;
  /** Saves the form; rejects with the error to show. */
  onSubmit: (values: ClassroomFormValues) => Promise<void>;
  /** Deactivates (false) or reactivates (true) the edited classroom. */
  onSetActive: (active: boolean) => Promise<void>;
  /** Refetches the classroom after a version conflict. */
  onReload: () => Promise<void>;
  /** The dialog closed: `saved` after a successful change. */
  onClose: (saved: boolean) => void;
}

export function ClassroomDialog(props: ClassroomDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose(false);
        }
      }}
    >
      {props.open ? <DialogBody {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

function fieldsOf(classroom: Classroom | null): FormFields {
  return {
    name: classroom?.name ?? '',
    capacity: classroom ? String(classroom.capacity) : '',
  };
}

const WINDOW_FIELDS: Record<string, keyof WindowRowErrors> = {
  weekday: 'weekday',
  starts_at: 'startsAt',
  ends_at: 'endsAt',
};

/** The rows' errors from a server 422, or null when none is about a row. */
function serverWindowErrors(
  error: ApiError,
  rowCount: number,
): WindowRowErrors[] | null {
  const errors: WindowRowErrors[] = Array.from(
    { length: rowCount },
    () => ({}),
  );
  let found = false;
  for (const fieldError of error.fieldErrors) {
    const [, field, index, part] = fieldError.path;
    if (field !== 'availability' || typeof index !== 'number') {
      continue;
    }
    const row = errors[index];
    const key = WINDOW_FIELDS[String(part)] ?? 'startsAt';
    if (row) {
      row[key] =
        fieldError.code === 'window_overlap'
          ? 'weeklyWindows.validation.overlap'
          : 'weeklyWindows.validation.invalid';
      found = true;
    }
  }
  return found ? errors : null;
}

function firstWindowError(
  editorId: string,
  rows: readonly WindowRow[],
  errors: readonly WindowRowErrors[],
): string | null {
  for (const [index, row] of rows.entries()) {
    const rowErrors = errors[index] ?? {};
    for (const field of ['weekday', 'startsAt', 'endsAt'] as const) {
      if (rowErrors[field]) {
        return windowFieldId(editorId, row.key, field);
      }
    }
  }
  return null;
}

function DialogBody({
  classroom,
  onSubmit,
  onSetActive,
  onReload,
  onClose,
  busyRef,
}: ClassroomDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const ids = {
    name: `${id}-name`,
    capacity: `${id}-capacity`,
    capacityHelp: `${id}-capacity-help`,
    windows: `${id}-windows`,
    statusHelp: `${id}-status-help`,
  };
  const returnFocus = useRef<Element | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [working, setWorking] = useState<'save' | 'status' | null>(null);
  const [rows, setRows] = useState<WindowRow[]>(() =>
    rowsOf(classroom?.availability ?? []),
  );
  const [windowErrors, setWindowErrors] = useState<WindowRowErrors[]>([]);
  const [checkWindows, setCheckWindows] = useState(false);
  const {
    register,
    handleSubmit,
    reset,
    setError: setFieldError,
    setFocus,
    formState: { errors },
  } = useForm<FormFields>({
    resolver: zodResolver(formSchema),
    defaultValues: fieldsOf(classroom),
    shouldFocusError: true,
  });

  // A new version from the server (after Reload): the fields and the
  // windows show it (the windows are adjusted while rendering, the form's
  // fields in an effect).
  const version = classroom?.version;
  const [shownVersion, setShownVersion] = useState(version);
  if (version !== shownVersion) {
    setShownVersion(version);
    setRows(rowsOf(classroom?.availability ?? []));
    setWindowErrors([]);
    setCheckWindows(false);
  }
  useEffect(() => {
    if (version !== undefined) {
      reset(fieldsOf(classroom));
    }
    // Only a new version resets the typed values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reset, version]);

  function changeRows(next: WindowRow[]) {
    setRows(next);
    setWindowErrors(checkWindows ? validateWindows(next) : []);
  }

  async function run(kind: 'save' | 'status', action: () => Promise<void>) {
    if (busyRef.current) {
      return;
    }
    busyRef.current = true;
    setWorking(kind);
    setError(null);
    try {
      await action();
      busyRef.current = false;
      onClose(true);
    } catch (caught) {
      busyRef.current = false;
      setWorking(null);
      if (caught instanceof ApiError && caught.code === 'name_taken') {
        setFieldError('name', { message: NAME_TAKEN });
        setFocus('name');
        return;
      }
      if (caught instanceof ApiError && caught.fieldErrors.length > 0) {
        const field = caught.fieldErrors[0]?.path[1];
        if (field === 'name' || field === 'capacity') {
          setFieldError(field, {
            message:
              field === 'name'
                ? 'catalog.validation.nameRequired'
                : 'catalog.classrooms.validation.capacityRange',
          });
          setFocus(field);
          return;
        }
        const fromServer = serverWindowErrors(caught, rows.length);
        if (fromServer) {
          setWindowErrors(fromServer);
          const target = firstWindowError(ids.windows, rows, fromServer);
          if (target) {
            document.getElementById(target)?.focus();
          }
          return;
        }
      }
      setError(caught);
    }
  }

  // handleSubmit is called in the event handler, never during render.
  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validateWindows(rows);
    setWindowErrors(found);
    setCheckWindows(true);
    void handleSubmit((fields) => {
      if (hasWindowErrors(found)) {
        const target = firstWindowError(ids.windows, rows, found);
        if (target) {
          document.getElementById(target)?.focus();
        }
        return;
      }
      return run('save', () =>
        onSubmit({
          name: fields.name,
          capacity: Number(fields.capacity),
          availability: windowsOf(rows),
        }),
      );
    })(event);
  }

  async function reload() {
    setError(null);
    await onReload();
  }

  // Messages are stored as keys and translated here, so they follow the
  // active language.
  const message = (key: string | undefined) =>
    key === NAME_TAKEN
      ? tErrors('name_taken')
      : key
        ? t(key as MessageKey)
        : undefined;
  const messages = {
    name: message(errors.name?.message),
    capacity: message(errors.capacity?.message),
  };
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  return (
    <DialogContent
      aria-describedby={undefined}
      className="max-h-[90vh] overflow-y-auto sm:max-w-2xl"
      onOpenAutoFocus={() => {
        returnFocus.current = document.activeElement;
      }}
      onCloseAutoFocus={(event) => {
        event.preventDefault();
        const target = returnFocus.current;
        if (target instanceof HTMLElement && target.isConnected) {
          target.focus();
        }
      }}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>
          {classroom
            ? t('catalog.classrooms.edit')
            : t('catalog.classrooms.add')}
        </DialogTitle>
      </DialogHeader>
      {error ? (
        <ErrorAlert
          error={error}
          onRetry={isConflict ? () => void reload() : undefined}
          retryLabel={isConflict ? t('catalog.conflict.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-[1fr_10rem]">
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.name} className="text-sm font-medium">
              {t('catalog.classrooms.name')}
            </label>
            <Input
              id={ids.name}
              type="text"
              autoComplete="off"
              aria-invalid={messages.name ? true : undefined}
              aria-describedby={messages.name ? `${ids.name}-error` : undefined}
              {...register('name')}
            />
            <FieldError id={`${ids.name}-error`} message={messages.name} />
          </div>
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.capacity} className="text-sm font-medium">
              {t('catalog.classrooms.capacity')}
            </label>
            <Input
              id={ids.capacity}
              type="number"
              inputMode="numeric"
              min={CAPACITY_MIN}
              max={CAPACITY_MAX}
              step={1}
              autoComplete="off"
              aria-invalid={messages.capacity ? true : undefined}
              aria-describedby={
                messages.capacity
                  ? `${ids.capacity}-error ${ids.capacityHelp}`
                  : ids.capacityHelp
              }
              {...register('capacity')}
            />
            <p id={ids.capacityHelp} className="text-sm text-muted-foreground">
              {t('catalog.classrooms.capacityHelp')}
            </p>
            <FieldError
              id={`${ids.capacity}-error`}
              message={messages.capacity}
            />
          </div>
        </div>
        <WeeklyWindowsEditor
          id={ids.windows}
          rows={rows}
          onChange={changeRows}
          errors={windowErrors}
          disabled={working !== null}
        />
        {classroom ? (
          <div className="flex flex-col items-start gap-2 border-t border-border pt-4">
            <Button
              type="button"
              variant={classroom.is_active ? 'destructive' : 'outline'}
              disabled={working !== null}
              aria-describedby={
                classroom.is_active ? ids.statusHelp : undefined
              }
              onClick={() =>
                void run('status', () => onSetActive(!classroom.is_active))
              }
            >
              {working === 'status' ? (
                <Spinner label={t('catalog.actions.saving')} showLabel />
              ) : classroom.is_active ? (
                t('catalog.actions.deactivate')
              ) : (
                t('catalog.actions.reactivate')
              )}
            </Button>
            {classroom.is_active ? (
              <p id={ids.statusHelp} className="text-sm text-muted-foreground">
                {t('catalog.classrooms.deactivateHelp')}
              </p>
            ) : null}
          </div>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={working !== null}
            onClick={() => onClose(false)}
          >
            {t('catalog.actions.cancel')}
          </Button>
          <Button type="submit" disabled={working !== null}>
            {working === 'save' ? (
              <Spinner label={t('catalog.actions.saving')} showLabel />
            ) : (
              t('catalog.actions.save')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
