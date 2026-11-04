import { useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import type { PaymentMethod } from '@/features/billing/accounts/api';
import { useAccountLabels } from '@/features/billing/accounts/labels';
import type { AttemptFailure } from '@/features/billing/accounts/rules';
import { isCode } from '@/features/payroll/periodRules';
import {
  INPUT_CLASS,
  SELECT_CLASS,
  SHEET_CLASS,
} from '@/features/payroll/sheet';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { LocalDate } from '@/lib/time';
import type { ExpenseDetail, ExpensePaymentCreate } from '../api';
import { FormField } from './FormField';
import { LockedDialog } from './LockedDialog';
import type { PickerState } from './NewExpenseDialog';
import {
  duplicatePaymentId,
  emptyPaymentForm,
  exceededOpen,
  NOTE_MAX,
  PAYMENT_FIELD_ORDER,
  PAYMENT_REFERENCE_MAX,
  paymentCreateBody,
  paymentServerErrors,
  textLength,
  validatePaymentForm,
  type PaymentErrors,
  type PaymentField,
  type PaymentFormValues,
} from './rules';
import { useLockedSubmission, type LockedControl } from './useLockedSubmission';

// "Registrar pago" (M7-12b D2), without any call: the date (the center's
// today by default, never later; before the expense date is fine, #115 E2),
// the amount (all that's open by default, never more), an active method, an
// optional reference and note. A duplicate names the payment already stored
// (the page refetches before the refusal reaches the dialog), the cap shows
// `details.open`. The lost-answer rule is E2 (a): useLockedSubmission.

export interface ExpensePaymentDialogProps {
  open: boolean;
  today: LocalDate;
  expense: Pick<ExpenseDetail, 'open_amount'>;
  /** Every payment method (the dialog offers the active ones). */
  methods: PickerState<PaymentMethod>;
  onReloadMethods: () => void;
  /** A stored payment in words, or null when the page doesn't hold it. */
  describePayment: (paymentId: string) => string | null;
  onSubmit: (body: ExpensePaymentCreate, key: string) => Promise<void>;
  onSaved: () => void;
  onClose: (failed: AttemptFailure) => void;
  initialValues?: Partial<PaymentFormValues>;
}

export function ExpensePaymentDialog(props: ExpensePaymentDialogProps) {
  return (
    <LockedDialog
      open={props.open}
      onClose={props.onClose}
      body={Body}
      bodyProps={props}
    />
  );
}

const D = 'finance.expenses.dialog';

function Body({
  today,
  expense,
  methods,
  onReloadMethods,
  describePayment,
  onSubmit,
  onSaved,
  initialValues,
  busyRef,
  failedRef,
  onCancel,
}: ExpensePaymentDialogProps & LockedControl) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const labels = useAccountLabels();
  const id = useId();
  const submission = useLockedSubmission<ExpensePaymentCreate>(
    busyRef,
    failedRef,
  );
  const [values, setValues] = useState<PaymentFormValues>(() => ({
    ...emptyPaymentForm(today, expense.open_amount),
    ...initialValues,
  }));
  const [errors, setErrors] = useState<PaymentErrors>({});
  const [alertText, setAlertText] = useState<string | null>(null);
  const fieldId = (field: PaymentField) => `${id}-${field}`;
  const locked = submission.locked;
  const active =
    methods.status === 'ready'
      ? methods.items.filter((method) => method.is_active)
      : [];

  function change(key: PaymentField, value: string) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
  }

  function focusFirst(found: PaymentErrors) {
    const field = PAYMENT_FIELD_ORDER.find((name) => found[name]);
    if (field) document.getElementById(fieldId(field))?.focus();
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAlertText(null);
    void submission.submit({
      build: () => {
        const found = validatePaymentForm(values, {
          today,
          open: expense.open_amount,
        });
        setErrors(found);
        if (Object.keys(found).length > 0) {
          focusFirst(found);
          return null;
        }
        return paymentCreateBody(values);
      },
      send: onSubmit,
      onSaved,
      onRefused: (caught) => {
        if (isCode(caught, 'payment_method_unavailable')) onReloadMethods();
        const found = paymentServerErrors(caught);
        if (found) {
          setErrors(found);
          focusFirst(found);
          return true;
        }
        const duplicate = duplicatePaymentId(caught);
        const stored = duplicate ? describePayment(duplicate) : null;
        const open = exceededOpen(caught);
        if (stored) {
          setAlertText(t(`${D}.payment.duplicate`, { payment: stored }));
        } else if (open) {
          setAlertText(
            t(`${D}.payment.exceedsOpen`, { open: labels.money(open) }),
          );
        }
        return false;
      },
    });
  }

  let content;
  if (methods.status === 'error') {
    content = <ErrorAlert error={methods.error} onRetry={methods.onRetry} />;
  } else if (methods.status === 'loading') {
    content = <LoadingState rows={4} />;
  } else {
    content = (
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        <p className="text-sm font-medium" data-payment-open>
          {t(`${D}.payment.open`, {
            amount: labels.money(expense.open_amount),
          })}
        </p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <FormField
            id={fieldId('paidOn')}
            label={t(`${D}.payment.paidOn`)}
            error={message(errors.paidOn)}
          >
            {(field) => (
              <Input
                {...field}
                type="date"
                max={today}
                className={INPUT_CLASS}
                value={values.paidOn}
                disabled={locked}
                onChange={(event) => change('paidOn', event.target.value)}
              />
            )}
          </FormField>
          <FormField
            id={fieldId('amount')}
            label={t(`${D}.payment.amount`)}
            hint={t(`${D}.amountHint`)}
            error={message(errors.amount)}
          >
            {(field) => (
              <Input
                {...field}
                type="text"
                inputMode="decimal"
                autoComplete="off"
                className={INPUT_CLASS}
                value={values.amount}
                disabled={locked}
                onChange={(event) => change('amount', event.target.value)}
              />
            )}
          </FormField>
          <FormField
            id={fieldId('methodCode')}
            label={t(`${D}.payment.method`)}
            error={message(errors.methodCode)}
          >
            {(field) => (
              <select
                {...field}
                className={SELECT_CLASS}
                value={values.methodCode}
                disabled={locked}
                onChange={(event) => change('methodCode', event.target.value)}
              >
                <option value="">{t(`${D}.choose`)}</option>
                {active.map((method) => (
                  <option key={method.code} value={method.code}>
                    {labels.method(method.code)}
                  </option>
                ))}
              </select>
            )}
          </FormField>
        </div>
        <FormField
          id={fieldId('reference')}
          label={t(`${D}.payment.reference`)}
          hint={t(`${D}.payment.referenceHint`)}
          counter={t(`${D}.counter`, {
            n: textLength(values.reference),
            max: PAYMENT_REFERENCE_MAX,
          })}
          error={message(errors.reference)}
        >
          {(field) => (
            <Input
              {...field}
              type="text"
              autoComplete="off"
              className={INPUT_CLASS}
              value={values.reference}
              disabled={locked}
              onChange={(event) => change('reference', event.target.value)}
            />
          )}
        </FormField>
        <FormField
          id={fieldId('note')}
          label={t(`${D}.payment.note`)}
          counter={t(`${D}.counter`, {
            n: textLength(values.note),
            max: NOTE_MAX,
          })}
          error={message(errors.note)}
        >
          {(field) => (
            <textarea
              {...field}
              rows={2}
              className={TEXTAREA_CLASS}
              value={values.note}
              disabled={locked}
              onChange={(event) => change('note', event.target.value)}
            />
          )}
        </FormField>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={submission.submitting}
            onClick={onCancel}
          >
            {t('finance.cancel')}
          </Button>
          <Button
            type="submit"
            className="min-h-11 md:min-h-9"
            disabled={submission.submitting}
          >
            {submission.submitting ? (
              <Spinner label={t('finance.saving')} showLabel />
            ) : submission.lost ? (
              t(`${D}.payment.resend`)
            ) : (
              t(`${D}.payment.submit`)
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
      data-expense-dialog="payment"
    >
      <DialogHeader>
        <DialogTitle>{t(`${D}.payment.title`)}</DialogTitle>
        <DialogDescription>{t(`${D}.payment.help`)}</DialogDescription>
      </DialogHeader>
      {submission.failure ? (
        <ErrorAlert
          error={submission.failure}
          message={
            submission.lost ? t(`${D}.payment.lost`) : (alertText ?? undefined)
          }
        />
      ) : null}
      {content}
    </DialogContent>
  );
}
