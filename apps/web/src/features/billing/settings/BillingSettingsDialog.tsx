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
import type { LocalDate } from '@/lib/time';
import type { BillingSettings } from './api';
import {
  billingServerError,
  initialBillingForm,
  isVersionConflict,
  MAX_START_DATE,
  MIN_START_DATE,
  startsInThePast,
  validateBillingForm,
  type BillingDateError,
  type BillingFormValues,
  type BillingMode,
} from './rules';

// "Change billing" (#366 B2, B3), without any API call: "Charge sessions
// from a date" with the date (prefilled with the current start date, or
// today while billing is off) or, while billing is on, "Turn billing off".
// A date before the center's today shows a warning, not an error: past
// confirmed and completed sessions from that date will be charged.
// `onSubmit` resolves once the settings are stored; a 422 goes on the date,
// a 409 version_conflict shows the reload message with "Reload" (the page
// refetches; the next save uses the new version), anything else is an
// alert. Nothing typed is lost on any error.

export interface BillingSettingsDialogProps {
  open: boolean;
  settings: BillingSettings;
  today: LocalDate;
  /** Stores the settings; rejects with the error to show. */
  onSubmit: (values: BillingFormValues) => Promise<void>;
  /** Refetches the settings after a version conflict. */
  onReload: () => Promise<void>;
  /** The dialog closed: `saved` after stored settings. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and the date error to start from. */
  initialValues?: BillingFormValues;
  initialError?: BillingDateError;
}

export function BillingSettingsDialog(props: BillingSettingsDialogProps) {
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
  settings,
  today,
  onSubmit,
  onReload,
  onClose,
  initialValues,
  initialError,
  busyRef,
}: BillingSettingsDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [values, setValues] = useState<BillingFormValues>(
    () => initialValues ?? initialBillingForm(settings, today),
  );
  const [dateError, setDateError] = useState<BillingDateError | undefined>(
    initialError,
  );
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const billingOn = settings.billing_starts_on !== null;

  const ids = {
    date: `${id}-date`,
    dateHelp: `${id}-date-help`,
    dateWarning: `${id}-date-warning`,
    dateError: `${id}-date-error`,
    offHelp: `${id}-off-help`,
  };
  const past = startsInThePast(values, today);

  async function submit() {
    setFailure(null);
    const found = validateBillingForm(values);
    setDateError(found);
    if (found) {
      document.getElementById(ids.date)?.focus();
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
      const fromServer = billingServerError(caught);
      if (fromServer && values.mode === 'on') {
        setDateError(fromServer);
        document.getElementById(ids.date)?.focus();
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

  function setMode(mode: BillingMode) {
    setValues({ ...values, mode });
    if (mode === 'off') {
      setDateError(undefined);
    }
  }

  const conflict = isVersionConflict(failure);
  const describedBy = [
    ids.dateHelp,
    past ? ids.dateWarning : undefined,
    dateError ? ids.dateError : undefined,
  ]
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
        <DialogTitle>{t('billing.settings.dialog.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.settings.card.rules')}
        </DialogDescription>
      </DialogHeader>
      {failure ? (
        <ErrorAlert
          error={failure}
          onRetry={
            conflict
              ? () => {
                  setFailure(null);
                  void onReload();
                }
              : undefined
          }
          retryLabel={conflict ? t('billing.settings.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        {billingOn ? (
          <fieldset className="flex min-w-0 flex-col gap-3">
            <legend className="mb-2 text-sm font-medium">
              {t('billing.settings.dialog.mode')}
            </legend>
            {(['on', 'off'] as const).map((mode) => (
              <label
                key={mode}
                htmlFor={`${id}-mode-${mode}`}
                className="flex min-h-6 items-center gap-2 text-sm"
              >
                <input
                  id={`${id}-mode-${mode}`}
                  type="radio"
                  name={`${id}-mode`}
                  value={mode}
                  checked={values.mode === mode}
                  disabled={submitting}
                  aria-describedby={mode === 'off' ? ids.offHelp : undefined}
                  className="size-4 accent-primary outline-none focus-visible:ring-3 focus-visible:ring-ring"
                  onChange={() => setMode(mode)}
                />
                {t(`billing.settings.dialog.modes.${mode}`)}
              </label>
            ))}
            <p id={ids.offHelp} className="text-sm text-muted-foreground">
              {t('billing.settings.dialog.offHelp')}
            </p>
          </fieldset>
        ) : null}
        {values.mode === 'on' ? (
          <div className="flex max-w-xs flex-col gap-2">
            <label htmlFor={ids.date} className="text-sm font-medium">
              {t('billing.settings.dialog.startsOn')}
            </label>
            <Input
              id={ids.date}
              type="date"
              min={MIN_START_DATE}
              max={MAX_START_DATE}
              value={values.startsOn}
              aria-invalid={dateError ? true : undefined}
              aria-describedby={describedBy}
              onChange={(event) =>
                setValues({ ...values, startsOn: event.target.value })
              }
            />
            <p id={ids.dateHelp} className="text-sm text-muted-foreground">
              {t('billing.settings.dialog.dateHelp')}
            </p>
            {past ? (
              <p
                id={ids.dateWarning}
                className="text-sm font-medium"
                data-billing-warning
              >
                {t('billing.settings.dialog.pastWarning')}
              </p>
            ) : null}
            <FieldError id={ids.dateError} message={message(dateError)} />
          </div>
        ) : null}
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={() => onClose(false)}
          >
            {t('billing.settings.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.settings.saving')} showLabel />
            ) : (
              t('billing.settings.save')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
