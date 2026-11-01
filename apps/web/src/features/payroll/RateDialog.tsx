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
import type { ClassKind, RateModel } from './api';
import {
  changeRateKind,
  CLASS_KINDS,
  emptyRateForm,
  modelsFor,
  rateServerErrors,
  validateRateForm,
  type RateErrors,
  type RateField,
  type RateFormValues,
} from './rules';
import {
  CHECK_BOX_CLASS,
  CHECK_LABEL_CLASS,
  CHECK_ROW_CLASS,
  INPUT_CLASS,
  SELECT_CLASS,
  SHEET_CLASS,
} from './sheet';

// "Add rate" (#425 C6), without any API call: the class kind, the effective
// date, "No rate from this date", which clears and disables the model and
// the rate and sends null, the model (`per_course` only for courses) and the
// rate, up to four decimals. A 422 goes on its field (the payroll date rules
// on the date), anything else is an alert; the dialog stays open and nothing
// typed is lost.

export interface RateDialogProps {
  open: boolean;
  today: LocalDate;
  /** Stores the version; rejects with the error to show. */
  onSubmit: (values: RateFormValues) => Promise<void>;
  /** The dialog closed: `saved` after a stored version. */
  onClose: (saved: boolean) => void;
  /** Stories: the values and errors to start from. */
  initialValues?: RateFormValues;
  initialErrors?: RateErrors;
}

export function RateDialog(props: RateDialogProps) {
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

const FIELD_ORDER: readonly RateField[] = [
  'classKind',
  'effectiveFrom',
  'model',
  'rate',
];

function DialogBody({
  today,
  onSubmit,
  onClose,
  initialValues,
  initialErrors = {},
  busyRef,
}: RateDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const [values, setValues] = useState<RateFormValues>(
    () => initialValues ?? emptyRateForm(today),
  );
  const [errors, setErrors] = useState<RateErrors>(initialErrors);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: RateField) => `${id}-${field}`;
  const errorId = (field: RateField) => `${id}-${field}-error`;
  const ids = {
    noRate: `${id}-no-rate`,
    dateHelp: `${id}-date-help`,
    rateHelp: `${id}-rate-help`,
  };
  const describedBy = (field: RateField, help?: string) =>
    [help, errors[field] ? errorId(field) : undefined]
      .filter(Boolean)
      .join(' ') || undefined;
  const focusFirst = (found: RateErrors) => {
    const first = FIELD_ORDER.find((field) => found[field]);
    if (first) {
      document.getElementById(fieldId(first))?.focus();
    }
  };

  async function submit() {
    setFailure(null);
    const found = validateRateForm(values);
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
      const fromServer = rateServerErrors(caught);
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
        <DialogTitle>{t('payroll.rateDialog.title')}</DialogTitle>
        <DialogDescription>{t('payroll.rateDialog.help')}</DialogDescription>
      </DialogHeader>
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('classKind')} className="text-sm font-medium">
            {t('payroll.fields.classKind')}
          </label>
          <select
            id={fieldId('classKind')}
            className={SELECT_CLASS}
            value={values.classKind}
            aria-invalid={errors.classKind ? true : undefined}
            aria-describedby={describedBy('classKind')}
            onChange={(event) =>
              setValues(
                changeRateKind(values, event.target.value as ClassKind | ''),
              )
            }
          >
            <option value="">{t('payroll.fields.choose')}</option>
            {CLASS_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {t(`payroll.classKinds.${kind}`)}
              </option>
            ))}
          </select>
          <FieldError
            id={errorId('classKind')}
            message={message(errors.classKind)}
          />
        </div>
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
            {t('payroll.rateDialog.dateHelp')}
          </p>
          <FieldError
            id={errorId('effectiveFrom')}
            message={message(errors.effectiveFrom)}
          />
        </div>
        <div className={CHECK_ROW_CLASS}>
          <Checkbox
            id={ids.noRate}
            className={CHECK_BOX_CLASS}
            checked={values.noRate}
            disabled={submitting}
            onCheckedChange={(checked) => {
              const noRate = checked === true;
              setValues({
                ...values,
                noRate,
                model: noRate ? '' : values.model,
                rate: noRate ? '' : values.rate,
              });
              if (noRate) {
                setErrors((current) => ({
                  ...current,
                  model: undefined,
                  rate: undefined,
                }));
              }
            }}
          />
          <label htmlFor={ids.noRate} className={CHECK_LABEL_CLASS}>
            {t('payroll.rateDialog.noRate')}
          </label>
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('model')} className="text-sm font-medium">
            {t('payroll.fields.model')}
          </label>
          <select
            id={fieldId('model')}
            className={SELECT_CLASS}
            value={values.model}
            disabled={values.noRate}
            aria-invalid={errors.model ? true : undefined}
            aria-describedby={describedBy('model')}
            onChange={(event) =>
              setValues({
                ...values,
                model: event.target.value as RateModel | '',
              })
            }
          >
            <option value="">{t('payroll.fields.choose')}</option>
            {modelsFor(values.classKind).map((model) => (
              <option key={model} value={model}>
                {t(`payroll.models.${model}`)}
              </option>
            ))}
          </select>
          <FieldError id={errorId('model')} message={message(errors.model)} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('rate')} className="text-sm font-medium">
            {t('payroll.fields.rate')}
          </label>
          <Input
            id={fieldId('rate')}
            type="text"
            inputMode="decimal"
            autoComplete="off"
            className={INPUT_CLASS}
            value={values.rate}
            disabled={values.noRate}
            aria-invalid={errors.rate ? true : undefined}
            aria-describedby={describedBy('rate', ids.rateHelp)}
            onChange={(event) =>
              setValues({ ...values, rate: event.target.value })
            }
          />
          <p id={ids.rateHelp} className="text-sm text-muted-foreground">
            {t('payroll.rateDialog.rateHelp')}
          </p>
          <FieldError id={errorId('rate')} message={message(errors.rate)} />
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
              t('payroll.rateDialog.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
