import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
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
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import type { PaymentMethod } from '@/features/billing/accounts/api';
import { useAccountLabels } from '@/features/billing/accounts/labels';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { useFormatters } from '@/lib/formatters';
import { useIdempotentSubmission } from '@/lib/idempotency';
import type { LocalDate } from '@/lib/time';
import type { PayrollPaymentCreate } from './api';
import {
  duplicatePaymentId,
  emptyPaymentForm,
  exceedsRemaining,
  PAYMENT_FIELD_ORDER,
  PAYMENT_NOTE_MAX,
  PAYMENT_REFERENCE_MAX,
  paymentCreateBody,
  paymentServerErrors,
  textLength,
  validatePaymentForm,
  worse,
  type AttemptFailure,
  type PaymentDialogOutcome,
  type PaymentErrors,
  type PaymentField,
  type PaymentFormValues,
} from './paymentForm';
import { isCode, isUnknownResult } from './periodRules';
import { INPUT_CLASS, SELECT_CLASS, SHEET_CLASS } from './sheet';

// "Registrar pago" (M7-09b B), without any API call. The body is mounted once
// per opening, so is its idempotent submission (E2): pressing the button again
// with the same body resends the same key, so a retry after a lost response
// can't log the payment twice; an edited body is a new submission with a new
// key, and the server's duplicate rule (same method and reference, #112 B5) is
// the backstop. A new opening starts with a new key.
//
// A lost response (no answer, or a 5xx) keeps the dialog open and says the
// outcome is unknown. Whatever failed, closing the dialog by any route
// (Cancel, Escape, the close button) tells the page an attempt failed, and
// the page refetches the payments and the period before anything can be
// submitted again. A 422 goes on its field; every other refusal is the alert,
// the duplicate naming the payment already logged and the cap naming what's
// left.

export type PaymentMethodOptions =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; methods: readonly PaymentMethod[] };

export interface RecordPayrollPaymentDialogProps {
  open: boolean;
  tutorName: string;
  startsOn: string;
  endsOn: string;
  /** The center's today: the default and latest date. */
  today: LocalDate;
  /** What's left to pay now (the period's strings). */
  remaining: string;
  methods: PaymentMethodOptions;
  /** Refetches the methods after `payment_method_unavailable`. */
  onReloadMethods: () => void;
  /** A loaded payment in words, for the duplicate refusal; null if unknown. */
  describePayment: (paymentId: string) => string | null;
  /** Records; rejects with the error to show. */
  onSubmit: (body: PayrollPaymentCreate, key: string) => Promise<void>;
  /** `failed`: how an attempt of this opening failed, if one did. */
  onClose: (outcome: PaymentDialogOutcome, failed: AttemptFailure) => void;
  /** Stories: the values to start from. */
  initialValues?: Partial<PaymentFormValues>;
}

export function RecordPayrollPaymentDialog(
  props: RecordPayrollPaymentDialogProps,
) {
  const busy = useRef(false);
  const failed = useRef<AttemptFailure>('none');
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose('cancelled', failed.current);
          failed.current = 'none';
        }
      }}
    >
      {props.open ? (
        <Body {...props} busyRef={busy} failedRef={failed} />
      ) : null}
    </Dialog>
  );
}

function Body({
  tutorName,
  startsOn,
  endsOn,
  today,
  remaining,
  methods,
  onReloadMethods,
  describePayment,
  onSubmit,
  onClose,
  initialValues,
  busyRef,
  failedRef,
}: RecordPayrollPaymentDialogProps & {
  busyRef: { current: boolean };
  failedRef: { current: AttemptFailure };
}) {
  const { t } = useTranslation();
  const { formatLocalDate, formatMoney } = useFormatters();
  const labels = useAccountLabels();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const submission = useIdempotentSubmission();
  const id = useId();
  const [values, setValues] = useState<PaymentFormValues>(() => ({
    ...emptyPaymentForm(today, remaining),
    ...initialValues,
  }));
  const [errors, setErrors] = useState<PaymentErrors>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const D = 'payroll.paymentDialog';

  const fieldId = (field: PaymentField) => `${id}-${field}`;
  const errorId = (field: PaymentField) => `${id}-${field}-error`;
  const helpId = (field: PaymentField) => `${id}-${field}-help`;
  const describedBy = (field: PaymentField, help = false) =>
    [
      help ? helpId(field) : undefined,
      errors[field] ? errorId(field) : undefined,
    ]
      .filter(Boolean)
      .join(' ') || undefined;
  const active =
    methods.status === 'ready'
      ? methods.methods.filter((method) => method.is_active)
      : [];

  function change<K extends PaymentField>(key: K, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
  }

  function focusFirst(found: PaymentErrors) {
    const first = PAYMENT_FIELD_ORDER.find((field) => found[field]);
    if (first) document.getElementById(fieldId(first))?.focus();
  }

  async function submit() {
    const found = validatePaymentForm(values, { startsOn, today, remaining });
    setErrors(found);
    if (Object.keys(found).length > 0) {
      focusFirst(found);
      return;
    }
    const body = paymentCreateBody(values);
    // Outside the call (#88): a resend of this body resends this key.
    const key = submission.keyFor(body);
    setFailure(null);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(body, key);
      submission.succeeded();
      busyRef.current = false;
      onClose('saved', failedRef.current);
      failedRef.current = 'none';
      return;
    } catch (caught) {
      failedRef.current = worse(failedRef.current, caught);
      const fromServer = isUnknownResult(caught)
        ? null
        : paymentServerErrors(caught);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
        if (isCode(caught, 'payment_method_unavailable')) onReloadMethods();
      } else {
        setFailure(caught);
      }
    }
    busyRef.current = false;
    setSubmitting(false);
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busyRef.current) return;
    void submit();
  }

  function close() {
    onClose('cancelled', failedRef.current);
    failedRef.current = 'none';
  }

  let failureMessage: string | undefined;
  if (failure && isUnknownResult(failure)) {
    failureMessage = t(`${D}.unknown`);
  } else if (exceedsRemaining(failure) !== null) {
    failureMessage = t(`${D}.exceeds`, {
      remaining: formatMoney(exceedsRemaining(failure)!),
    });
  } else {
    const duplicate = duplicatePaymentId(failure);
    const named = duplicate ? describePayment(duplicate) : null;
    if (named) failureMessage = t(`${D}.duplicateOf`, { payment: named });
  }

  let content;
  if (methods.status === 'error') {
    content = <ErrorAlert error={methods.error} onRetry={methods.onRetry} />;
  } else if (methods.status === 'loading') {
    content = <LoadingState rows={5} />;
  } else {
    content = (
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <p className="text-sm font-medium" data-payment-remaining={remaining}>
          {t(`${D}.remaining`, { amount: formatMoney(remaining) })}
        </p>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('paidOn')} className="text-sm font-medium">
            {t(`${D}.paidOn`)}
          </label>
          <Input
            id={fieldId('paidOn')}
            type="date"
            className={INPUT_CLASS}
            min={startsOn}
            max={today}
            value={values.paidOn}
            aria-invalid={errors.paidOn ? true : undefined}
            aria-describedby={describedBy('paidOn', true)}
            onChange={(event) => change('paidOn', event.target.value)}
          />
          <p id={helpId('paidOn')} className="text-sm text-muted-foreground">
            {t(`${D}.paidOnHelp`, { start: formatLocalDate(startsOn) })}
          </p>
          <FieldError id={errorId('paidOn')} message={message(errors.paidOn)} />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('amount')} className="text-sm font-medium">
            {t(`${D}.amount`)}
          </label>
          <Input
            id={fieldId('amount')}
            inputMode="decimal"
            autoComplete="off"
            className={INPUT_CLASS}
            value={values.amount}
            aria-invalid={errors.amount ? true : undefined}
            aria-describedby={describedBy('amount', true)}
            onChange={(event) => change('amount', event.target.value)}
          />
          <p id={helpId('amount')} className="text-sm text-muted-foreground">
            {t(`${D}.amountHelp`)}
          </p>
          <FieldError id={errorId('amount')} message={message(errors.amount)} />
        </div>
        <div className="flex flex-col gap-2">
          <label
            htmlFor={fieldId('methodCode')}
            className="text-sm font-medium"
          >
            {t(`${D}.method`)}
          </label>
          <select
            id={fieldId('methodCode')}
            className={SELECT_CLASS}
            value={values.methodCode}
            aria-invalid={errors.methodCode ? true : undefined}
            aria-describedby={describedBy('methodCode')}
            onChange={(event) => change('methodCode', event.target.value)}
          >
            <option value="">{t(`${D}.choose`)}</option>
            {active.map((method) => (
              <option key={method.code} value={method.code}>
                {labels.method(method.code)}
              </option>
            ))}
          </select>
          <FieldError
            id={errorId('methodCode')}
            message={message(errors.methodCode)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('reference')} className="text-sm font-medium">
            {t(`${D}.reference`)}
          </label>
          <Input
            id={fieldId('reference')}
            autoComplete="off"
            className={INPUT_CLASS}
            value={values.reference}
            aria-invalid={errors.reference ? true : undefined}
            aria-describedby={describedBy('reference', true)}
            onChange={(event) => change('reference', event.target.value)}
          />
          <p id={helpId('reference')} className="text-sm text-muted-foreground">
            {t(`${D}.referenceHelp`, {
              n: textLength(values.reference),
              max: PAYMENT_REFERENCE_MAX,
            })}
          </p>
          <FieldError
            id={errorId('reference')}
            message={message(errors.reference)}
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor={fieldId('note')} className="text-sm font-medium">
            {t(`${D}.note`)}
          </label>
          <textarea
            id={fieldId('note')}
            rows={2}
            className={TEXTAREA_CLASS}
            value={values.note}
            aria-invalid={errors.note ? true : undefined}
            aria-describedby={describedBy('note', true)}
            onChange={(event) => change('note', event.target.value)}
          />
          <p id={helpId('note')} className="text-sm text-muted-foreground">
            {t(`${D}.noteHelp`, {
              n: textLength(values.note),
              max: PAYMENT_NOTE_MAX,
            })}
          </p>
          <FieldError id={errorId('note')} message={message(errors.note)} />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={submitting}
            onClick={close}
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
              t(`${D}.submit`)
            )}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
      data-payment-dialog
    >
      <DialogHeader>
        <DialogTitle>{t(`${D}.title`, { name: tutorName })}</DialogTitle>
        <DialogDescription>
          {t(`${D}.help`, {
            start: formatLocalDate(startsOn),
            end: formatLocalDate(endsOn),
          })}
        </DialogDescription>
      </DialogHeader>
      {failure ? <ErrorAlert error={failure} message={failureMessage} /> : null}
      {content}
    </DialogContent>
  );
}
