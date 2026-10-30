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
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { SELECT_CLASS } from '@/features/students/styles';
import type { LocalDate } from '@/lib/time';
import type { PaymentDetail, PaymentMethod, RefundCreate } from './api';
import { apiCents } from './cents';
import { CorrectionField } from './CorrectionField';
import {
  anyError,
  emptyRefund,
  isCode,
  REASON_MAX,
  REFUND_FIELD_ORDER,
  REFUND_STALE_CODES,
  refundBody,
  refundServerErrors,
  validateRefund,
  type RefundErrors,
  type RefundField,
  type RefundValues,
} from './corrections';
import { useAccountLabels } from './labels';
import { REFERENCE_MAX } from './rules';
import { useCorrectionSubmission } from './useCorrectionSubmission';

// "Refund" (#386 B3; #100 C4, R4, R5), without any call: the amount (the
// payment's unallocated amount by default and at most), the refund date
// (center today by default, not before the payment's date, not later than
// today), the method (the active ones), a reference and the reason, and the
// line that a refund can't be undone. Needs a recent password confirmation
// (apiCall's dialog, the same key on the retry). A 422 goes on its field;
// a 409 payment_reversed or refund_exceeds_remainder is an alert and
// reloads the payment.

export interface RefundDialogProps {
  open: boolean;
  today: LocalDate;
  payment: Pick<
    PaymentDetail,
    'id' | 'amount' | 'received_on' | 'unallocated_amount'
  > | null;
  /** Every payment method; null while they load. */
  methods: readonly PaymentMethod[] | null;
  methodsError?: unknown;
  onRetryMethods?: () => void;
  onSubmit: (body: RefundCreate, idempotencyKey: string) => Promise<void>;
  onStale?: () => void;
  onClose: () => void;
  initialValues?: Partial<RefundValues>;
  initialErrors?: RefundErrors;
}

export function RefundDialog(props: RefundDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose();
      }}
    >
      {props.open && props.payment ? (
        <Body {...props} payment={props.payment} busy={busy} />
      ) : null}
    </Dialog>
  );
}

function Body({
  today,
  payment,
  methods,
  methodsError,
  onRetryMethods,
  onSubmit,
  onStale,
  onClose,
  initialValues,
  initialErrors = {},
  busy,
}: RefundDialogProps & {
  payment: NonNullable<RefundDialogProps['payment']>;
  busy: { current: boolean };
}) {
  const { t } = useTranslation();
  const labels = useAccountLabels();
  const returnFocus = useReturnFocus();
  const id = useId();
  const submission = useCorrectionSubmission(busy);
  const [values, setValues] = useState<RefundValues>(() => ({
    ...emptyRefund(today, payment.unallocated_amount),
    ...initialValues,
  }));
  const [errors, setErrors] = useState<RefundErrors>(initialErrors);
  const fieldId = (field: RefundField) => `${id}-${field}`;
  const active = (methods ?? []).filter((method) => method.is_active);
  const unallocated = labels.money(payment.unallocated_amount);

  function change(field: RefundField, value: string) {
    setValues((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
  }

  function focusFirst(found: RefundErrors) {
    const field = REFUND_FIELD_ORDER.find((name) => found[name]);
    if (field) document.getElementById(fieldId(field))?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validateRefund(values, {
      unallocated: apiCents(payment.unallocated_amount),
      receivedOn: payment.received_on,
      today,
    });
    setErrors(found);
    if (anyError(found)) {
      focusFirst(found);
      return;
    }
    await submission.run(refundBody(values), onSubmit, {
      onFields: (caught) => {
        const fromServer = refundServerErrors(caught);
        if (!fromServer) return false;
        setErrors(fromServer);
        focusFirst(fromServer);
        return true;
      },
      onStale: (caught) => {
        if (isCode(caught, ...REFUND_STALE_CODES)) onStale?.();
      },
    });
  }

  const { submitting } = submission;
  let content;
  if (methodsError) {
    content = <ErrorAlert error={methodsError} onRetry={onRetryMethods} />;
  } else if (!methods) {
    content = <LoadingState rows={4} />;
  } else {
    content = (
      <form
        method="post"
        noValidate
        onSubmit={(event) => void submit(event)}
        className="flex min-w-0 flex-col gap-4"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <CorrectionField
            id={fieldId('amount')}
            label={t('billing.accounts.refund.fields.amount')}
            help={t('billing.accounts.refund.hints.amount', {
              amount: unallocated,
            })}
            error={errors.amount}
          >
            {(wiring) => (
              <Input
                {...wiring}
                type="text"
                inputMode="decimal"
                autoComplete="off"
                value={values.amount}
                onChange={(event) => change('amount', event.target.value)}
              />
            )}
          </CorrectionField>
          <CorrectionField
            id={fieldId('refundedOn')}
            label={t('billing.accounts.refund.fields.refundedOn')}
            help={t('billing.accounts.refund.hints.refundedOn', {
              date: labels.date(payment.received_on),
            })}
            error={errors.refundedOn}
          >
            {(wiring) => (
              <Input
                {...wiring}
                type="date"
                min={payment.received_on}
                max={today}
                value={values.refundedOn}
                onChange={(event) => change('refundedOn', event.target.value)}
              />
            )}
          </CorrectionField>
          <CorrectionField
            id={fieldId('method')}
            label={t('billing.accounts.refund.fields.method')}
            error={errors.method}
          >
            {(wiring) => (
              <select
                {...wiring}
                className={SELECT_CLASS}
                value={values.method}
                onChange={(event) => change('method', event.target.value)}
              >
                <option value="">{t('billing.prices.choose')}</option>
                {active.map((method) => (
                  <option key={method.code} value={method.code}>
                    {labels.method(method.code)}
                  </option>
                ))}
              </select>
            )}
          </CorrectionField>
        </div>
        <CorrectionField
          id={fieldId('reference')}
          label={t('billing.accounts.refund.fields.reference')}
          error={errors.reference}
        >
          {(wiring) => (
            <Input
              {...wiring}
              type="text"
              autoComplete="off"
              maxLength={REFERENCE_MAX}
              value={values.reference}
              onChange={(event) => change('reference', event.target.value)}
            />
          )}
        </CorrectionField>
        <CorrectionField
          id={fieldId('reason')}
          label={t('billing.accounts.refund.fields.reason')}
          error={errors.reason}
        >
          {(wiring) => (
            <textarea
              {...wiring}
              rows={2}
              className={TEXTAREA_CLASS}
              maxLength={REASON_MAX}
              value={values.reason}
              onChange={(event) => change('reason', event.target.value)}
            />
          )}
        </CorrectionField>
        <p className="text-sm font-medium">
          {t('billing.accounts.refund.final')}
        </p>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onClose}
          >
            {t('billing.prices.cancel')}
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.accounts.refund.saving')} showLabel />
            ) : (
              t('billing.accounts.refund.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-2xl [&>*]:min-w-0"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.accounts.refund.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.accounts.refund.help', { amount: unallocated })}
        </DialogDescription>
      </DialogHeader>
      <p className="text-sm text-muted-foreground">
        {t('billing.accounts.refund.passwordNote')}
      </p>
      {submission.failure ? <ErrorAlert error={submission.failure} /> : null}
      {content}
    </DialogContent>
  );
}
