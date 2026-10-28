import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
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
import type { LocalDate } from '@/lib/time';
import type { PriceVersion } from './api';
import {
  defaultVersionDate,
  validateVersionForm,
  versionMinDate,
  versionServerErrors,
  type VersionErrors,
  type VersionField,
  type VersionFormValues,
} from './rules';

// "Schedule a price change" (#363 D3), without any API call: the date
// (today, or the day after the latest version when that one starts today or
// later; not before today unless the item has no versions, #89 E3), the
// amount, and "Not offered from this date", which clears and disables the
// amount and sends `amount: null`. `onSubmit` resolves once the version is
// stored; a 422 goes on its field (price_effective_from_invalid on the
// date), anything else is an alert. Nothing typed is lost on any error.

export interface PriceVersionDialogProps {
  open: boolean;
  /** The item's versions (the default and the minimum date). */
  versions: readonly PriceVersion[];
  today: LocalDate;
  /** Stores the version; rejects with the error to show. */
  onSubmit: (values: VersionFormValues) => Promise<void>;
  /** The dialog closed: `saved` after a stored version. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and errors to start from. */
  initialValues?: VersionFormValues;
  initialErrors?: VersionErrors;
}

export function PriceVersionDialog(props: PriceVersionDialogProps) {
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

function DialogBody({
  versions,
  today,
  onSubmit,
  onClose,
  initialValues,
  initialErrors = {},
  busyRef,
}: PriceVersionDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const min = versionMinDate(versions, today);
  const [values, setValues] = useState<VersionFormValues>(
    () =>
      initialValues ?? {
        effectiveFrom: defaultVersionDate(versions, today),
        amount: '',
        notOffered: false,
      },
  );
  const [errors, setErrors] = useState<VersionErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: VersionField) => `${id}-${field}`;
  const errorId = (field: VersionField) => `${id}-${field}-error`;
  const ids = {
    notOffered: `${id}-not-offered`,
    amountHelp: `${id}-amount-help`,
    dateHelp: `${id}-date-help`,
  };

  async function submit() {
    setFailure(null);
    const found = validateVersionForm(values, min);
    setErrors(found);
    const first = (['effectiveFrom', 'amount'] as const).find(
      (field) => found[field],
    );
    if (first) {
      document.getElementById(fieldId(first))?.focus();
      return;
    }
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values);
      busyRef.current = false;
      onClose(true);
      return;
    } catch (caught) {
      const fromServer = versionServerErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        const target = fromServer.effectiveFrom ? 'effectiveFrom' : 'amount';
        document.getElementById(fieldId(target))?.focus();
      } else {
        setFailure(caught);
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

  const describedBy = (field: VersionField, help: string) =>
    [help, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ');

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-lg"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.prices.schedule.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.prices.schedule.help')}
        </DialogDescription>
      </DialogHeader>
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label
            htmlFor={fieldId('effectiveFrom')}
            className="text-sm font-medium"
          >
            {t('billing.prices.fields.effectiveFrom')}
          </label>
          <Input
            id={fieldId('effectiveFrom')}
            type="date"
            min={min}
            value={values.effectiveFrom}
            aria-invalid={errors.effectiveFrom ? true : undefined}
            aria-describedby={describedBy('effectiveFrom', ids.dateHelp)}
            onChange={(event) =>
              setValues({ ...values, effectiveFrom: event.target.value })
            }
          />
          <p id={ids.dateHelp} className="text-sm text-muted-foreground">
            {min
              ? t('billing.prices.schedule.dateHelp')
              : t('billing.prices.schedule.dateHelpFirst')}
          </p>
          <FieldError
            id={errorId('effectiveFrom')}
            message={message(errors.effectiveFrom)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('amount')} className="text-sm font-medium">
            {t('billing.prices.fields.amount')}
          </label>
          <Input
            id={fieldId('amount')}
            type="text"
            inputMode="decimal"
            autoComplete="off"
            value={values.amount}
            disabled={values.notOffered}
            aria-invalid={errors.amount ? true : undefined}
            aria-describedby={describedBy('amount', ids.amountHelp)}
            onChange={(event) =>
              setValues({ ...values, amount: event.target.value })
            }
          />
          <p id={ids.amountHelp} className="text-sm text-muted-foreground">
            {t('billing.prices.hints.amount')}
          </p>
          <FieldError id={errorId('amount')} message={message(errors.amount)} />
        </div>
        <div className="flex items-center gap-3">
          <Checkbox
            id={ids.notOffered}
            checked={values.notOffered}
            disabled={submitting}
            onCheckedChange={(checked) => {
              const notOffered = checked === true;
              setValues({
                ...values,
                notOffered,
                amount: notOffered ? '' : values.amount,
              });
              if (notOffered) {
                setErrors((current) => ({ ...current, amount: undefined }));
              }
            }}
          />
          <label htmlFor={ids.notOffered} className="text-sm font-medium">
            {t('billing.prices.schedule.notOffered')}
          </label>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={() => onClose(false)}
          >
            {t('billing.prices.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.prices.saving')} showLabel />
            ) : (
              t('billing.prices.schedule.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
