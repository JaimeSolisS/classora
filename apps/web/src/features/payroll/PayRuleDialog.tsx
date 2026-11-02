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
import { useFormatters } from '@/lib/formatters';
import type { LocalDate } from '@/lib/time';
import type { PayRule, PayRuleMode } from './api';
import {
  initialPayRuleForm,
  PAY_RULE_MODES,
  payRuleServerErrors,
  validatePayRuleForm,
  type PayRuleErrors,
  type PayRuleField,
  type PayRuleFormValues,
} from './payRuleForm';
import { isVersionConflict } from './rules';
import { INPUT_CLASS, SELECT_CLASS, SHEET_CLASS } from './sheet';

// "New version" of an event's pay rule (#444 B2-B5), without any API call. It
// works from one snapshot of the latest version: the prefilled values and the
// `based_on_id` both come from it, so a version is never published over one
// the user hasn't seen (QA #425 D1, D3). A newer latest version replaces the
// snapshot and the form and says so, only while no save is in flight, in
// whatever order a refetch and the response arrive; what was typed is dropped
// and saving again takes a deliberate click. A `409 version_conflict` reloads
// by itself; if that fails it stays with "Reload". The reset shows only what
// the server holds: the date stays empty. A 422 goes on its field, and
// anything else is an alert.

export interface PayRuleDialogProps {
  open: boolean;
  /** The event's latest version (the last upcoming one, else the current). */
  latest: PayRule;
  /** Today's date in the center's zone, the date's `min`. */
  today: LocalDate;
  /** Stores `values` based on `base`, the snapshot they were edited from;
   * rejects with the error to show. */
  onSubmit: (values: PayRuleFormValues, base: PayRule) => Promise<void>;
  /** Refetches the rules after a version conflict. */
  onReload: () => Promise<void>;
  /** The dialog closed: `saved` after a stored version. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and the errors to start from. */
  initialValues?: PayRuleFormValues;
  initialErrors?: PayRuleErrors;
}

export function PayRuleDialog(props: PayRuleDialogProps) {
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

const FIELD_ORDER: readonly PayRuleField[] = [
  'mode',
  'percent',
  'amount',
  'effectiveFrom',
];

function DialogBody({
  latest: server,
  today,
  onSubmit,
  onReload,
  onClose,
  initialValues,
  initialErrors = {},
  busyRef,
}: PayRuleDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [latest, setLatest] = useState(server);
  const [values, setValues] = useState<PayRuleFormValues>(
    () => initialValues ?? initialPayRuleForm(server),
  );
  const [errors, setErrors] = useState<PayRuleErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [reloaded, setReloaded] = useState(false);
  if (!submitting && server.id !== latest.id) {
    // Adjusting state while rendering, so no render pairs the new snapshot
    // with the old values. During a save nothing moves: a refused save is
    // reconciled here when it ends, and our own save closes the dialog.
    setLatest(server);
    setValues(initialPayRuleForm(server));
    setErrors({});
    setFailure(null);
    setReloaded(true);
  }

  const fieldId = (field: PayRuleField) => `${id}-${field}`;
  const errorId = (field: PayRuleField) => `${id}-${field}-error`;
  const ids = {
    percentHelp: `${id}-percent-help`,
    dateHelp: `${id}-date-help`,
  };
  const locked = submitting || reloading;
  const eventName = t(`payroll.payRules.events.${latest.event}`);
  const describedBy = (field: PayRuleField, help?: string) =>
    [help, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ') || undefined;
  const focusFirst = (found: PayRuleErrors) => {
    const first = FIELD_ORDER.find((field) => found[field]);
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  };

  async function reload() {
    setReloading(true);
    try {
      await onReload();
    } finally {
      setReloading(false);
    }
  }

  async function submit() {
    const found = validatePayRuleForm(values, today);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    // Only a save that goes out clears these (QA #425 round 4 O1).
    setFailure(null);
    setReloaded(false);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values, latest);
      busyRef.current = false;
      onClose(true);
      return;
    } catch (caught) {
      const fromServer = payRuleServerErrors(caught, values.mode);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setFailure(caught);
        if (isVersionConflict(caught)) {
          // Not submitting any more, so a newer version resets the form;
          // busyRef still holds off a save until the reload ends.
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

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>
          {t('payroll.payRules.newVersionFor', { event: eventName })}
        </DialogTitle>
        <DialogDescription>
          {t('payroll.payRules.dialogHelp')}
        </DialogDescription>
      </DialogHeader>
      {latest.event === 'late_cancellation' ? (
        <p className="text-sm text-muted-foreground" data-pay-rule-late-note>
          {t('payroll.payRules.lateNote')}
        </p>
      ) : null}
      {reloaded ? (
        <p role="alert" className="text-sm font-medium" data-pay-rule-reloaded>
          {t('payroll.payRules.conflictReloaded')}
        </p>
      ) : null}
      {failure ? (
        <ErrorAlert
          error={failure}
          onRetry={conflict && !reloading ? () => void reload() : undefined}
          retryLabel={conflict ? t('payroll.payRules.reload') : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('mode')} className="text-sm font-medium">
            {t('payroll.payRules.fields.mode')}
          </label>
          <select
            id={fieldId('mode')}
            className={SELECT_CLASS}
            value={values.mode}
            disabled={locked}
            aria-invalid={errors.mode ? true : undefined}
            aria-describedby={describedBy('mode')}
            onChange={(event) =>
              setValues({ ...values, mode: event.target.value as PayRuleMode })
            }
          >
            {PAY_RULE_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {t(`payroll.payRules.modes.${mode}`)}
              </option>
            ))}
          </select>
          <FieldError id={errorId('mode')} message={message(errors.mode)} />
        </div>
        {values.mode === 'percent' ? (
          <div className="flex flex-col gap-2">
            <label htmlFor={fieldId('percent')} className="text-sm font-medium">
              {t('payroll.payRules.fields.percent')}
            </label>
            <Input
              id={fieldId('percent')}
              inputMode="decimal"
              autoComplete="off"
              className={INPUT_CLASS}
              value={values.percent}
              disabled={locked}
              aria-invalid={errors.percent ? true : undefined}
              aria-describedby={describedBy('percent', ids.percentHelp)}
              onChange={(event) =>
                setValues({ ...values, percent: event.target.value })
              }
            />
            <p id={ids.percentHelp} className="text-sm text-muted-foreground">
              {t('payroll.payRules.percentHelp')}
            </p>
            <FieldError
              id={errorId('percent')}
              message={message(errors.percent)}
            />
          </div>
        ) : null}
        {values.mode === 'fixed' ? (
          <div className="flex flex-col gap-2">
            <label htmlFor={fieldId('amount')} className="text-sm font-medium">
              {t('payroll.payRules.fields.amount')}
            </label>
            <Input
              id={fieldId('amount')}
              inputMode="decimal"
              autoComplete="off"
              className={INPUT_CLASS}
              value={values.amount}
              disabled={locked}
              aria-invalid={errors.amount ? true : undefined}
              aria-describedby={describedBy('amount')}
              onChange={(event) =>
                setValues({ ...values, amount: event.target.value })
              }
            />
            <FieldError
              id={errorId('amount')}
              message={message(errors.amount)}
            />
          </div>
        ) : null}
        <div className="flex flex-col gap-2">
          <label
            htmlFor={fieldId('effectiveFrom')}
            className="text-sm font-medium"
          >
            {t('payroll.payRules.fields.effectiveFrom')}
          </label>
          <Input
            id={fieldId('effectiveFrom')}
            type="date"
            min={today}
            className={INPUT_CLASS}
            value={values.effectiveFrom}
            disabled={locked}
            aria-invalid={errors.effectiveFrom ? true : undefined}
            aria-describedby={describedBy('effectiveFrom', ids.dateHelp)}
            onChange={(event) =>
              setValues({ ...values, effectiveFrom: event.target.value })
            }
          />
          <p id={ids.dateHelp} className="text-sm text-muted-foreground">
            {t('payroll.payRules.dateHelp', {
              date: formatLocalDate(latest.effective_from),
            })}
          </p>
          <FieldError
            id={errorId('effectiveFrom')}
            message={message(errors.effectiveFrom)}
          />
        </div>
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
              t('payroll.payRules.save')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
