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
import type { PayFrequency } from './api';
import {
  emptyTermsForm,
  PAY_FREQUENCIES,
  termsServerErrors,
  validateTermsForm,
  type TermsErrors,
  type TermsField,
  type TermsFormValues,
} from './rules';
import {
  CHECK_BOX_CLASS,
  CHECK_LABEL_CLASS,
  CHECK_ROW_CLASS,
  INPUT_CLASS,
  SELECT_CLASS,
  SHEET_CLASS,
} from './sheet';

// "Add pay terms" (#425 C5), without any API call: the effective date (past
// or future), "Stop payroll from this date", which clears and disables the
// other fields and sends null, the frequency and an optional salary. A 422
// goes on its field (the payroll date rules on the date), anything else
// (`pay_terms_exists`, `payroll_own_compensation`) is an alert; the dialog
// stays open and nothing typed is lost.

export interface PayTermsDialogProps {
  open: boolean;
  today: LocalDate;
  /** Stores the version; rejects with the error to show. */
  onSubmit: (values: TermsFormValues) => Promise<void>;
  /** The dialog closed: `saved` after a stored version. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and errors to start from. */
  initialValues?: TermsFormValues;
  initialErrors?: TermsErrors;
}

export function PayTermsDialog(props: PayTermsDialogProps) {
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

const FIELD_ORDER: readonly TermsField[] = [
  'effectiveFrom',
  'frequency',
  'salary',
];

function DialogBody({
  today,
  onSubmit,
  onClose,
  initialValues,
  initialErrors = {},
  busyRef,
}: PayTermsDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [values, setValues] = useState<TermsFormValues>(
    () => initialValues ?? emptyTermsForm(today),
  );
  const [errors, setErrors] = useState<TermsErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: TermsField) => `${id}-${field}`;
  const errorId = (field: TermsField) => `${id}-${field}-error`;
  const ids = {
    stop: `${id}-stop`,
    dateHelp: `${id}-date-help`,
    salaryHelp: `${id}-salary-help`,
  };
  const describedBy = (field: TermsField, help?: string) =>
    [help, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ') || undefined;
  const focusFirst = (found: TermsErrors) => {
    const first = FIELD_ORDER.find((field) => found[field]);
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  };

  async function submit() {
    setFailure(null);
    const found = validateTermsForm(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
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
      const fromServer = termsServerErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
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

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('payroll.termsDialog.title')}</DialogTitle>
        <DialogDescription>{t('payroll.termsDialog.help')}</DialogDescription>
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
            {t('payroll.fields.effectiveFrom')}
          </label>
          <Input
            id={fieldId('effectiveFrom')}
            type="date"
            className={INPUT_CLASS}
            value={values.effectiveFrom}
            aria-invalid={errors.effectiveFrom ? true : undefined}
            aria-describedby={describedBy('effectiveFrom', ids.dateHelp)}
            onChange={(event) =>
              setValues({ ...values, effectiveFrom: event.target.value })
            }
          />
          <p id={ids.dateHelp} className="text-sm text-muted-foreground">
            {t('payroll.termsDialog.dateHelp')}
          </p>
          <FieldError
            id={errorId('effectiveFrom')}
            message={message(errors.effectiveFrom)}
          />
        </div>
        <div className={CHECK_ROW_CLASS}>
          <Checkbox
            id={ids.stop}
            className={CHECK_BOX_CLASS}
            checked={values.stop}
            disabled={submitting}
            onCheckedChange={(checked) => {
              const stop = checked === true;
              setValues({
                ...values,
                stop,
                frequency: stop ? '' : values.frequency,
                salary: stop ? '' : values.salary,
              });
              if (stop) {
                setErrors((current) => ({
                  ...current,
                  frequency: undefined,
                  salary: undefined,
                }));
              }
            }}
          />
          <label htmlFor={ids.stop} className={CHECK_LABEL_CLASS}>
            {t('payroll.termsDialog.stop')}
          </label>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('frequency')} className="text-sm font-medium">
            {t('payroll.fields.frequency')}
          </label>
          <select
            id={fieldId('frequency')}
            className={SELECT_CLASS}
            value={values.frequency}
            disabled={values.stop}
            aria-invalid={errors.frequency ? true : undefined}
            aria-describedby={describedBy('frequency')}
            onChange={(event) =>
              setValues({
                ...values,
                frequency: event.target.value as PayFrequency | '',
              })
            }
          >
            <option value="">{t('payroll.fields.choose')}</option>
            {PAY_FREQUENCIES.map((frequency) => (
              <option key={frequency} value={frequency}>
                {t(`payroll.frequencies.${frequency}`)}
              </option>
            ))}
          </select>
          <FieldError
            id={errorId('frequency')}
            message={message(errors.frequency)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('salary')} className="text-sm font-medium">
            {t('payroll.fields.salary')}
          </label>
          <Input
            id={fieldId('salary')}
            type="text"
            inputMode="decimal"
            autoComplete="off"
            className={INPUT_CLASS}
            value={values.salary}
            disabled={values.stop}
            aria-invalid={errors.salary ? true : undefined}
            aria-describedby={describedBy('salary', ids.salaryHelp)}
            onChange={(event) =>
              setValues({ ...values, salary: event.target.value })
            }
          />
          <p id={ids.salaryHelp} className="text-sm text-muted-foreground">
            {t('payroll.termsDialog.salaryHelp')}
          </p>
          <FieldError id={errorId('salary')} message={message(errors.salary)} />
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
            disabled={submitting}
          >
            {submitting ? (
              <Spinner label={t('payroll.saving')} showLabel />
            ) : (
              t('payroll.termsDialog.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
