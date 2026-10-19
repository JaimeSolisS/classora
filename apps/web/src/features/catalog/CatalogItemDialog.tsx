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
import { z } from '@/lib/forms';
import { NAME_MAX_LENGTH, POSITION_MAX, type CatalogFormValues } from './rules';

// The add / edit dialog of a catalog record (#32 H3, H4, I2): a level (name
// and position) or a subject (name). Editing also offers Deactivate or
// Reactivate. Without any API call: the page passes `onSubmit` and
// `onSetActive`, which resolve once the change is stored, and the dialog then
// closes (`onDone`). Errors stay in the dialog: name_taken on the name field,
// anything else in its alert region, version_conflict with a "Reload" button
// that refetches (`onReload`) and resets the form to the server values.
// Focus returns to the element that opened the dialog.

type MessageKey =
  | 'catalog.validation.nameRequired'
  | 'catalog.validation.nameTooLong'
  | 'catalog.validation.positionInvalid';

/** The name field's message for 409 name_taken (the errors catalog). */
const NAME_TAKEN = 'errors:name_taken';

const formSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, 'catalog.validation.nameRequired')
    .max(NAME_MAX_LENGTH, 'catalog.validation.nameTooLong'),
  position: z
    .string()
    .trim()
    .refine(
      (value) =>
        value === '' ||
        (/^[0-9]{1,4}$/.test(value) && Number(value) <= POSITION_MAX),
      'catalog.validation.positionInvalid',
    ),
});

type FormFields = z.infer<typeof formSchema>;

/** The record being edited, in the dialog's terms. */
export interface CatalogDialogItem {
  name: string;
  /** Levels only. */
  sortOrder?: number;
  isActive: boolean;
  version: number;
}

export interface CatalogItemDialogProps {
  open: boolean;
  title: string;
  /** Shows the optional position field (levels). */
  withPosition?: boolean;
  /** The record being edited; null to add one. */
  item: CatalogDialogItem | null;
  /** Saves the form; rejects with the error to show. */
  onSubmit: (values: CatalogFormValues) => Promise<void>;
  /** Deactivates (false) or reactivates (true) the edited record. */
  onSetActive?: (active: boolean) => Promise<void>;
  /** Refetches the record after a version conflict. */
  onReload: () => Promise<void>;
  /** The dialog closed: `saved` after a successful change. */
  onClose: (saved: boolean) => void;
}

export function CatalogItemDialog(props: CatalogItemDialogProps) {
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

function fieldsOf(item: CatalogDialogItem | null): FormFields {
  return {
    name: item?.name ?? '',
    position: item?.sortOrder === undefined ? '' : String(item.sortOrder),
  };
}

function DialogBody({
  title,
  withPosition = false,
  item,
  onSubmit,
  onSetActive,
  onReload,
  onClose,
  busyRef,
}: CatalogItemDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const { t: tErrors } = useTranslation('errors');
  const id = useId();
  const ids = {
    name: `${id}-name`,
    position: `${id}-position`,
    positionHelp: `${id}-position-help`,
    statusHelp: `${id}-status-help`,
  };
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
    resolver: zodResolver(formSchema),
    defaultValues: fieldsOf(item),
    shouldFocusError: true,
  });

  // A new version from the server (after Reload): the fields show it.
  const version = item?.version;
  useEffect(() => {
    if (version !== undefined) {
      reset(fieldsOf(item));
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
        const fields = caught.fieldErrors.map((e) => String(e.path[1]));
        if (fields.includes('name')) {
          setFieldError('name', {
            message: 'catalog.validation.nameRequired',
          });
          setFocus('name');
          return;
        }
        if (fields.includes('sort_order') && withPosition) {
          setFieldError('position', {
            message: 'catalog.validation.positionInvalid',
          });
          setFocus('position');
          return;
        }
      }
      setError(caught);
    }
  }

  const submit = handleSubmit((fields) =>
    run('save', () =>
      onSubmit({
        name: fields.name,
        position:
          withPosition && fields.position ? Number(fields.position) : null,
      }),
    ),
  );

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit(event);
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
  const nameMessage = message(errors.name?.message);
  const positionMessage = message(errors.position?.message);
  const isConflict =
    error instanceof ApiError && error.code === 'version_conflict';
  const describedBy = (...parts: (string | false)[]) =>
    parts.filter(Boolean).join(' ') || undefined;

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
            {t('catalog.levels.name')}
          </label>
          <Input
            id={ids.name}
            type="text"
            autoComplete="off"
            aria-invalid={nameMessage ? true : undefined}
            aria-describedby={nameMessage ? `${ids.name}-error` : undefined}
            {...register('name')}
          />
          <FieldError id={`${ids.name}-error`} message={nameMessage} />
        </div>
        {withPosition ? (
          <div className="flex flex-col gap-2">
            <label htmlFor={ids.position} className="text-sm font-medium">
              {t('catalog.levels.position')}
            </label>
            <Input
              id={ids.position}
              type="text"
              inputMode="numeric"
              autoComplete="off"
              aria-invalid={positionMessage ? true : undefined}
              aria-describedby={describedBy(
                ids.positionHelp,
                Boolean(positionMessage) && `${ids.position}-error`,
              )}
              {...register('position')}
            />
            <p id={ids.positionHelp} className="text-sm text-muted-foreground">
              {t('catalog.levels.positionHelp')}
            </p>
            <FieldError
              id={`${ids.position}-error`}
              message={positionMessage}
            />
          </div>
        ) : null}
        {item && onSetActive ? (
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
                {t('catalog.deactivateHelp')}
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
