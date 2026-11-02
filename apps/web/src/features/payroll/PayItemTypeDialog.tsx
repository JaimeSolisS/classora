import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
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
import type { PayItemType } from './api';
import {
  initialPayItemTypeForm,
  PAY_ITEM_DIRECTIONS,
  payItemTypeServerErrors,
  validatePayItemTypeForm,
  type PayItemTypeErrors,
  type PayItemTypeField,
  type PayItemTypeFormValues,
} from './payItemForm';
import { isVersionConflict } from './rules';
import { INPUT_CLASS, SELECT_CLASS, SHEET_CLASS } from './sheet';

// Add a type, or edit one's name (#454 A3, A5), without any API call. The
// direction is picked once: on edit it is shown as text, with why it can't
// change. An edit works from one snapshot, as the pay rule dialog does: a newer
// version replaces it and the form and says so, never during a save, and a
// `409 version_conflict` reloads by itself. A taken name goes on the field.

export interface PayItemTypeDialogProps {
  open: boolean;
  /** The type being edited, as the list has it now; null adds one. */
  type: PayItemType | null;
  /** Stores `values` based on `base`, the snapshot they were edited from;
   * rejects with the error to show. */
  onSubmit: (
    values: PayItemTypeFormValues,
    base: PayItemType | null,
  ) => Promise<void>;
  /** Refetches the list after a version conflict. */
  onReload: () => Promise<void>;
  /** The dialog closed: `saved` after a stored change. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and the errors to start from. */
  initialValues?: PayItemTypeFormValues;
  initialErrors?: PayItemTypeErrors;
}

export function PayItemTypeDialog(props: PayItemTypeDialogProps) {
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

const FIELD_ORDER: readonly PayItemTypeField[] = ['name', 'direction'];

function DialogBody({
  type: server,
  onSubmit,
  onReload,
  onClose,
  initialValues,
  initialErrors = {},
  busyRef,
}: PayItemTypeDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [base, setBase] = useState(server);
  const [values, setValues] = useState<PayItemTypeFormValues>(
    () => initialValues ?? initialPayItemTypeForm(server),
  );
  const [errors, setErrors] = useState<PayItemTypeErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [reloaded, setReloaded] = useState(false);
  if (!submitting && server && server.version !== base?.version) {
    // Adjusting state while rendering: see PayRuleDialog.
    setBase(server);
    setValues(initialPayItemTypeForm(server));
    setErrors({});
    setFailure(null);
    setReloaded(true);
  }

  const fieldId = (field: PayItemTypeField) => `${id}-${field}`;
  const errorId = (field: PayItemTypeField) => `${id}-${field}-error`;
  const locked = submitting || reloading;
  const editing = base !== null;

  function focusFirst(found: PayItemTypeErrors) {
    const first = FIELD_ORDER.find((field) => found[field]);
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  }

  async function reload() {
    setReloading(true);
    try {
      await onReload();
    } finally {
      setReloading(false);
    }
  }

  async function submit() {
    const found = validatePayItemTypeForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    setFailure(null);
    setReloaded(false);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values, base);
      busyRef.current = false;
      onClose(true);
      return;
    } catch (caught) {
      const fromServer = payItemTypeServerErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setFailure(caught);
        if (isVersionConflict(caught)) {
          setSubmitting(false);
          await reload();
        }
      }
    }
    busyRef.current = false;
    setSubmitting(false);
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) {
      return;
    }
    void submit();
  }

  const conflict = isVersionConflict(failure);
  const directionLabelId = `${id}-direction-label`;

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>
          {editing
            ? t('payroll.payItemTypes.dialog.editTitle')
            : t('payroll.payItemTypes.dialog.addTitle')}
        </DialogTitle>
        <DialogDescription>
          {editing
            ? t('payroll.payItemTypes.dialog.editHelp')
            : t('payroll.payItemTypes.dialog.addHelp')}
        </DialogDescription>
      </DialogHeader>
      {reloaded ? (
        <p role="alert" className="text-sm font-medium" data-pay-type-reloaded>
          {t('payroll.payItemTypes.conflictReloaded')}
        </p>
      ) : null}
      {failure ? (
        <ErrorAlert
          error={failure}
          onRetry={conflict && !reloading ? () => void reload() : undefined}
          retryLabel={conflict ? t('payroll.payItemTypes.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('name')} className="text-sm font-medium">
            {t('payroll.payItemTypes.dialog.name')}
          </label>
          <Input
            id={fieldId('name')}
            autoComplete="off"
            className={INPUT_CLASS}
            value={values.name}
            disabled={locked}
            aria-invalid={errors.name ? true : undefined}
            aria-describedby={errors.name ? errorId('name') : undefined}
            onChange={(event) =>
              setValues({ ...values, name: event.target.value })
            }
          />
          <FieldError id={errorId('name')} message={message(errors.name)} />
        </div>
        {editing ? (
          <div className="flex flex-col gap-1" data-pay-type-direction>
            <p id={directionLabelId} className="text-sm font-medium">
              {t('payroll.payItemTypes.dialog.direction')}
            </p>
            <p aria-labelledby={directionLabelId}>
              {t(`payroll.payItems.directions.${base.direction}`)}
            </p>
            <p className="text-sm text-muted-foreground">
              {t('payroll.payItemTypes.dialog.directionLocked')}
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <label
              htmlFor={fieldId('direction')}
              className="text-sm font-medium"
            >
              {t('payroll.payItemTypes.dialog.direction')}
            </label>
            <select
              id={fieldId('direction')}
              className={SELECT_CLASS}
              value={values.direction}
              disabled={locked}
              aria-invalid={errors.direction ? true : undefined}
              aria-describedby={
                errors.direction ? errorId('direction') : undefined
              }
              onChange={(event) =>
                setValues({
                  ...values,
                  direction: event.target
                    .value as PayItemTypeFormValues['direction'],
                })
              }
            >
              <option value="">
                {t('payroll.payItemTypes.dialog.chooseDirection')}
              </option>
              {PAY_ITEM_DIRECTIONS.map((direction) => (
                <option key={direction} value={direction}>
                  {t(`payroll.payItems.directions.${direction}`)}
                </option>
              ))}
            </select>
            <FieldError
              id={errorId('direction')}
              message={message(errors.direction)}
            />
          </div>
        )}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={submitting}
            onClick={() => onClose(false)}
          >
            {t('payroll.cancel')}
          </Button>
          <Button
            type="submit"
            className="min-h-11 md:min-h-9"
            disabled={locked}
          >
            {submitting ? (
              <Spinner label={t('payroll.saving')} showLabel />
            ) : (
              t('payroll.payItemTypes.dialog.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
