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
import { FieldError } from '@/features/auth/FieldError';
import { SELECT_CLASS } from '@/features/students/styles';
import { z } from '@/lib/forms';
import { daysBetween, type RangeFormValues } from './rangeRules';
import { NAME_MAX_LENGTH } from './rules';

// The add / edit dialog of an academic period or a closure (#38 E2, E3):
// name, type (a select; read-only when `typeLocked`), start and end dates
// (inclusive, `type="date"`), and Deactivate / Reactivate when editing.
// Without any API call: the page passes `onSubmit` and `onSetActive`, which
// resolve once the change is stored, and the dialog then closes. Errors stay
// in the dialog: name_taken on the name field, a server field error on its
// field, anything else (period_overlap included) in the alert region, and
// version_conflict with a "Reload" button. Focus returns to the opener.

type MessageKey =
  | 'catalog.validation.nameRequired'
  | 'catalog.validation.nameTooLong'
  | 'catalog.validation.typeRequired'
  | 'catalog.validation.startsOnRequired'
  | 'catalog.validation.endsOnRequired'
  | 'catalog.validation.endsBeforeStart'
  | 'catalog.validation.spanTooLong'
  | 'catalog.validation.datesInvalid';

const NAME_TAKEN = 'errors:name_taken';
const LOCAL_DATE = /^\d{4}-\d{2}-\d{2}$/;

function formSchema(maxSpanDays: number) {
  return z
    .object({
      name: z
        .string()
        .trim()
        .min(1, 'catalog.validation.nameRequired')
        .max(NAME_MAX_LENGTH, 'catalog.validation.nameTooLong'),
      type: z.string().min(1, 'catalog.validation.typeRequired'),
      startsOn: z
        .string()
        .regex(LOCAL_DATE, 'catalog.validation.startsOnRequired'),
      endsOn: z.string().regex(LOCAL_DATE, 'catalog.validation.endsOnRequired'),
    })
    .superRefine((fields, ctx) => {
      if (
        !LOCAL_DATE.test(fields.startsOn) ||
        !LOCAL_DATE.test(fields.endsOn)
      ) {
        return;
      }
      const days = daysBetween(fields.startsOn, fields.endsOn);
      if (days < 0) {
        ctx.addIssue({
          code: 'custom',
          path: ['endsOn'],
          message: 'catalog.validation.endsBeforeStart',
        });
      } else if (days > maxSpanDays) {
        ctx.addIssue({
          code: 'custom',
          path: ['endsOn'],
          message: 'catalog.validation.spanTooLong',
        });
      }
    });
}

type FormFields = RangeFormValues;

/** The record being edited, in the dialog's terms. */
export interface RangeDialogItem extends RangeFormValues {
  isActive: boolean;
  version: number;
}

export interface RangeItemDialogProps {
  open: boolean;
  title: string;
  /** The type select's options (value and translated label). */
  typeOptions: readonly { value: string; label: string }[];
  /** The type can't change (periods, when editing). */
  typeLocked?: boolean;
  /** The longest allowed span in days (end − start). */
  maxSpanDays: number;
  /** Shown under Deactivate: what an inactive record means. */
  deactivateHelp: string;
  /** The record being edited; null to add one. */
  item: RangeDialogItem | null;
  /** Saves the form; rejects with the error to show. */
  onSubmit: (values: RangeFormValues) => Promise<void>;
  /** Deactivates (false) or reactivates (true) the edited record. */
  onSetActive: (active: boolean) => Promise<void>;
  /** Refetches the record after a version conflict. */
  onReload: () => Promise<void>;
  /** The dialog closed: `saved` after a successful change. */
  onClose: (saved: boolean) => void;
}

export function RangeItemDialog(props: RangeItemDialogProps) {
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

function fieldsOf(
  item: RangeDialogItem | null,
  defaultType: string,
): FormFields {
  return {
    name: item?.name ?? '',
    type: item?.type ?? defaultType,
    startsOn: item?.startsOn ?? '',
    endsOn: item?.endsOn ?? '',
  };
}

const SERVER_FIELDS: Record<string, keyof FormFields> = {
  name: 'name',
  period_type: 'type',
  closure_type: 'type',
  starts_on: 'startsOn',
  ends_on: 'endsOn',
};

/** The form field of the first server field error, if the form shows it. */
function serverField(
  error: ApiError,
  typeLocked: boolean,
): keyof FormFields | undefined {
  for (const fieldError of error.fieldErrors) {
    const name = SERVER_FIELDS[String(fieldError.path[1])];
    if (name !== undefined && !(typeLocked && name === 'type')) {
      return name;
    }
  }
  return undefined;
}

function DialogBody({
  title,
  typeOptions,
  typeLocked = false,
  maxSpanDays,
  deactivateHelp,
  item,
  onSubmit,
  onSetActive,
  onReload,
  onClose,
  busyRef,
}: RangeItemDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const ids = {
    name: `${id}-name`,
    type: `${id}-type`,
    typeHelp: `${id}-type-help`,
    startsOn: `${id}-starts-on`,
    endsOn: `${id}-ends-on`,
    statusHelp: `${id}-status-help`,
  };
  const defaultType = typeOptions[0]?.value ?? '';
  const returnFocus = useRef<Element | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [working, setWorking] = useState<'save' | 'status' | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    setError: setFieldError,
    setFocus,
    formState: { errors },
  } = useForm<FormFields>({
    resolver: zodResolver(formSchema(maxSpanDays)),
    defaultValues: fieldsOf(item, defaultType),
    shouldFocusError: true,
  });

  // A new version from the server (after Reload): the fields show it.
  const version = item?.version;
  useEffect(() => {
    if (version !== undefined) {
      reset(fieldsOf(item, defaultType));
    }
    // Only a new version resets the typed values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reset, version]);

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
        const field = serverField(caught, typeLocked);
        if (field) {
          setFieldError(field, {
            message:
              field === 'name'
                ? 'catalog.validation.nameRequired'
                : 'catalog.validation.datesInvalid',
          });
          setFocus(field);
          return;
        }
      }
      setError(caught);
    }
  }

  // handleSubmit is called in the event handler, never during render.
  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void handleSubmit((fields) =>
      run('save', () =>
        onSubmit({
          name: fields.name,
          // A locked type isn't a form control: it keeps the loaded value.
          type: fields.type,
          startsOn: fields.startsOn,
          endsOn: fields.endsOn,
        }),
      ),
    )(event);
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
        ? t(key as MessageKey, { days: maxSpanDays })
        : undefined;
  const messages = {
    name: message(errors.name?.message),
    type: message(errors.type?.message),
    startsOn: message(errors.startsOn?.message),
    endsOn: message(errors.endsOn?.message),
  };
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';

  function dateField(name: 'startsOn' | 'endsOn', label: string) {
    const fieldId = ids[name];
    const fieldMessage = messages[name];
    return (
      <div className="flex flex-col gap-2">
        <label htmlFor={fieldId} className="text-sm font-medium">
          {label}
        </label>
        <Input
          id={fieldId}
          type="date"
          autoComplete="off"
          aria-invalid={fieldMessage ? true : undefined}
          aria-describedby={fieldMessage ? `${fieldId}-error` : undefined}
          {...register(name)}
        />
        <FieldError id={`${fieldId}-error`} message={fieldMessage} />
      </div>
    );
  }

  return (
    <DialogContent
      aria-describedby={undefined}
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
        <DialogTitle>{title}</DialogTitle>
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
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.name} className="text-sm font-medium">
            {t('catalog.range.name')}
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
          <label htmlFor={ids.type} className="text-sm font-medium">
            {t('catalog.range.type')}
          </label>
          {typeLocked ? (
            <Input
              id={ids.type}
              type="text"
              readOnly
              value={
                typeOptions.find((option) => option.value === item?.type)
                  ?.label ?? ''
              }
              aria-describedby={ids.typeHelp}
            />
          ) : (
            <select
              id={ids.type}
              className={SELECT_CLASS}
              aria-invalid={messages.type ? true : undefined}
              aria-describedby={messages.type ? `${ids.type}-error` : undefined}
              {...register('type')}
            >
              {typeOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          )}
          {typeLocked ? (
            <p id={ids.typeHelp} className="text-sm text-muted-foreground">
              {t('catalog.periods.typeLocked')}
            </p>
          ) : null}
          <FieldError id={`${ids.type}-error`} message={messages.type} />
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {dateField('startsOn', t('catalog.range.startsOn'))}
          {dateField('endsOn', t('catalog.range.endsOn'))}
        </div>
        {item ? (
          <div className="flex flex-col items-start gap-2 border-t border-border pt-4">
            <Button
              type="button"
              variant={item.isActive ? 'destructive' : 'outline'}
              disabled={working !== null}
              aria-describedby={item.isActive ? ids.statusHelp : undefined}
              onClick={() =>
                void run('status', () => onSetActive(!item.isActive))
              }
            >
              {working === 'status' ? (
                <Spinner label={t('catalog.actions.saving')} showLabel />
              ) : item.isActive ? (
                t('catalog.actions.deactivate')
              ) : (
                t('catalog.actions.reactivate')
              )}
            </Button>
            {item.isActive ? (
              <p id={ids.statusHelp} className="text-sm text-muted-foreground">
                {deactivateHelp}
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
