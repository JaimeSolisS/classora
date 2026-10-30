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
import { SELECT_CLASS } from '@/features/students/styles';
import { addDays, type LocalDate } from '@/lib/time';
import type { CancellationOutcome, CancellationRule } from './api';
import {
  cancellationServerErrors,
  EDITABLE_FIELDS,
  FIELD_KEYS,
  initialCancellationForm,
  isApiCode,
  validateCancellationForm,
  type CancellationFieldErrors,
  type CancellationFormValues,
} from './cancellationRules';

// "Schedule a change" (#385 B), without any API call: one Credited/Charged
// select per editable classification (on time, late, no-show), prefilled
// from the latest version; the excused outcome is a fixed line and the page
// sends it unchanged. "Takes effect on" is optional, the SchedulingPolicyForm
// date pattern: empty means now, a date (after today) starts at 00:00 in the
// center's zone. `onSubmit` resolves once the version is stored; a 422
// (invalid_effective_from included) goes on its field, a 409
// version_conflict shows the reload message with "Reload", anything else is
// an alert. Nothing typed is lost on any error.

export interface CancellationRuleDialogProps {
  open: boolean;
  /** The latest version (the last upcoming one, else the one in force). */
  latest: CancellationRule;
  /** Today's date in the center's zone. */
  today: LocalDate;
  /** The center's zone, named in the date's help text. */
  timeZone: string;
  /** Stores the version; rejects with the error to show. */
  onSubmit: (values: CancellationFormValues) => Promise<void>;
  /** Refetches the rules after a version conflict. */
  onReload: () => Promise<void>;
  /** The dialog closed: `saved` after a stored version. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and the field errors to start from. */
  initialValues?: CancellationFormValues;
  initialErrors?: CancellationFieldErrors;
}

export function CancellationRuleDialog(props: CancellationRuleDialogProps) {
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
  latest,
  today,
  timeZone,
  onSubmit,
  onReload,
  onClose,
  initialValues,
  initialErrors,
  busyRef,
}: CancellationRuleDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [values, setValues] = useState<CancellationFormValues>(
    () => initialValues ?? initialCancellationForm(latest),
  );
  const [errors, setErrors] = useState<CancellationFieldErrors>(
    initialErrors ?? {},
  );
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: string) => `${id}-${field}`;
  const errorId = (field: string) => `${id}-${field}-error`;
  const ids = {
    hint: `${id}-hint`,
    date: fieldId('date'),
    dateHelp: `${id}-date-help`,
    dateError: errorId('date'),
  };

  function focusFirstError(found: CancellationFieldErrors) {
    const first = EDITABLE_FIELDS.find((field) => found[field]);
    document.getElementById(first ? fieldId(first) : ids.date)?.focus();
  }

  async function submit() {
    setFailure(null);
    const dateError = validateCancellationForm(values, today);
    if (dateError) {
      const found = { effectiveDate: dateError };
      setErrors(found);
      focusFirstError(found);
      return;
    }
    setErrors({});
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(values);
      busyRef.current = false;
      onClose(true);
      return;
    } catch (caught) {
      const fromServer = cancellationServerErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        focusFirstError(fromServer);
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

  const conflict = isApiCode(failure, 'version_conflict');
  const dateMessage = message(errors.effectiveDate);

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-lg"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>
          {t('billing.settings.cancellation.dialog.title')}
        </DialogTitle>
        <DialogDescription id={ids.hint}>
          {t('billing.settings.cancellation.dialog.hint')}
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
        {EDITABLE_FIELDS.map((field) => {
          const fieldMessage = message(errors[field]);
          return (
            <div key={field} className="flex max-w-xs flex-col gap-2">
              <label htmlFor={fieldId(field)} className="text-sm font-medium">
                {t(
                  `billing.settings.cancellation.classes.${FIELD_KEYS[field]}`,
                )}
              </label>
              <select
                id={fieldId(field)}
                className={SELECT_CLASS}
                value={values[field]}
                disabled={submitting}
                aria-invalid={fieldMessage ? true : undefined}
                aria-describedby={fieldMessage ? errorId(field) : undefined}
                onChange={(event) =>
                  setValues({
                    ...values,
                    [field]: event.target.value as CancellationOutcome,
                  })
                }
              >
                {(['credit', 'charge'] as const).map((outcome) => (
                  <option key={outcome} value={outcome}>
                    {t(`billing.settings.cancellation.outcomes.${outcome}`)}
                  </option>
                ))}
              </select>
              <FieldError id={errorId(field)} message={fieldMessage} />
            </div>
          );
        })}
        <p className="text-sm text-muted-foreground" data-excused-note>
          {t('billing.settings.cancellation.dialog.excused')}
        </p>
        <div className="flex max-w-xs flex-col gap-2">
          <label htmlFor={ids.date} className="text-sm font-medium">
            {t('billing.settings.cancellation.dialog.effectiveDate')}
          </label>
          <Input
            id={ids.date}
            type="date"
            min={addDays(today, 1)}
            value={values.effectiveDate}
            disabled={submitting}
            aria-invalid={dateMessage ? true : undefined}
            aria-describedby={
              dateMessage ? `${ids.dateHelp} ${ids.dateError}` : ids.dateHelp
            }
            onChange={(event) =>
              setValues({ ...values, effectiveDate: event.target.value })
            }
          />
          <p id={ids.dateHelp} className="text-sm text-muted-foreground">
            {t('billing.settings.cancellation.dialog.dateHelp', {
              zone: timeZone.replaceAll('_', ' '),
            })}
          </p>
          <FieldError id={ids.dateError} message={dateMessage} />
        </div>
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
