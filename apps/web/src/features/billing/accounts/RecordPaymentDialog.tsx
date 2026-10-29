import { useId, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ApiError } from '@/app/api';
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
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { SELECT_CLASS } from '@/features/students/styles';
import { useIdempotentSubmission } from '@/lib/idempotency';
import { isMoneyInput, normalizeMoneyInput } from '@/lib/money';
import type { LocalDate } from '@/lib/time';
import { AllocationEditor } from './AllocationEditor';
import type { ChargeItem, PaymentCreate, PaymentMethod } from './api';
import { centsOf, type Cents } from './cents';
import { useAccountLabels } from './labels';
import {
  emptyRecordForm,
  exceededChargeId,
  fillOldestFirst,
  hasErrors,
  NOTE_MAX,
  PAYER_NAME_MAX,
  RECEIVED_ON_MIN,
  RECORD_FIELD_ORDER,
  RECORD_MODES,
  recordBody,
  recordServerErrors,
  REFERENCE_MAX,
  validateRecordForm,
  type RecordErrors,
  type RecordField,
  type RecordFormValues,
  type RecordMode,
} from './rules';

// "Record payment" (#379 D; #96 M1-M5, M8), without any call: the amount,
// the received date (center today by default, no later date), the method
// (the active ones), reference, payer and note, and how to allocate it:
// oldest first (with an estimate from the loaded open charges; the server
// decides), chosen charges (the allocation editor) or kept as credit.
//
// Recording posts ledger rows, so it is an idempotent submission (#88): the
// key is computed from the body with `keyFor` BEFORE `onSubmit` runs the
// call, so a second press after a network error or a 5xx resends the same
// key, a changed form gets a new key, and `succeeded()` gives the next
// payment a new one. Nothing changes on the page before the server answers
// (#96 AC 4): the button shows the pending state and the dialog stays open
// until the 201. A 422 goes on its field (line errors on their row); a 409
// allocation_exceeds_open is an alert, marks the row and reloads the open
// charges; anything else (403, idempotency_key_reused, network) is an
// alert. Nothing typed is lost.

export interface RecordPaymentDialogProps {
  open: boolean;
  /** Today's date in the center's zone (the default and latest date). */
  today: LocalDate;
  /** Every payment method; null while they load. */
  methods: readonly PaymentMethod[] | null;
  methodsError?: unknown;
  onRetryMethods?: () => void;
  /** The account's open charges, oldest first; null while they load. */
  charges: readonly ChargeItem[] | null;
  chargesError?: unknown;
  /** Reloads the open charges (a retry, or after allocation_exceeds_open). */
  onReloadCharges?: () => void;
  /** Records the payment with the key; rejects with the error to show. */
  onSubmit: (body: PaymentCreate, idempotencyKey: string) => Promise<void>;
  onClose: () => void;
  /** Stories: the values to start from. */
  initialValues?: Partial<RecordFormValues>;
}

export function RecordPaymentDialog(props: RecordPaymentDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) {
          props.onClose();
        }
      }}
    >
      {props.open ? <DialogBody {...props} busyRef={busy} /> : null}
    </Dialog>
  );
}

function DialogBody({
  today,
  methods,
  methodsError,
  onRetryMethods,
  charges,
  chargesError,
  onReloadCharges,
  onSubmit,
  onClose,
  initialValues,
  busyRef,
}: RecordPaymentDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const labels = useAccountLabels();
  const returnFocus = useReturnFocus();
  const submission = useIdempotentSubmission();
  const id = useId();
  const [values, setValues] = useState<RecordFormValues>(() => ({
    ...emptyRecordForm(today),
    ...initialValues,
  }));
  const [errors, setErrors] = useState<RecordErrors>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);

  const fieldId = (field: RecordField) => `${id}-${field}`;
  const errorId = (field: RecordField) => `${id}-${field}-error`;
  const helpId = (field: RecordField) => `${id}-${field}-help`;
  const describedBy = (field: RecordField, help?: boolean) =>
    [
      help ? helpId(field) : undefined,
      errors[field] ? errorId(field) : undefined,
    ]
      .filter(Boolean)
      .join(' ') || undefined;

  const active = (methods ?? []).filter((method) => method.is_active);
  const openCharges = charges ?? [];
  const amountText = normalizeMoneyInput(values.amount);
  const amount: Cents | null =
    isMoneyInput(amountText) && centsOf(amountText)! > 0
      ? centsOf(amountText)
      : null;

  function change<K extends keyof RecordFormValues>(
    key: K,
    value: RecordFormValues[K],
  ) {
    setValues((current) => ({ ...current, [key]: value }));
    if (key === 'lines') {
      setErrors((current) => ({
        ...current,
        lines: undefined,
        allocation: undefined,
      }));
    } else if (key === 'mode') {
      setErrors((current) => ({
        ...current,
        lines: undefined,
        allocation: undefined,
      }));
    } else {
      setErrors((current) => ({ ...current, [key]: undefined }));
    }
  }

  function focusFirst(found: RecordErrors) {
    const field = RECORD_FIELD_ORDER.find((name) => found[name]);
    const lineId = Object.keys(found.lines ?? {})[0];
    const target = field
      ? document.getElementById(
          field === 'allocation' ? `${id}-mode-${values.mode}` : fieldId(field),
        )
      : lineId
        ? document.getElementById(`${id}-line-${lineId}`)
        : null;
    target?.focus();
  }

  async function submit() {
    setFailure(null);
    const found = validateRecordForm(values, today, openCharges);
    setErrors(found);
    if (hasErrors(found)) {
      focusFirst(found);
      return;
    }
    const body = recordBody(values, openCharges);
    const sent = body.allocation.lines ?? [];
    // Outside the call (#88): a retry of the same body resends this key.
    const key = submission.keyFor(body);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(body, key);
      submission.succeeded();
    } catch (caught) {
      const fromServer = recordServerErrors(caught, sent);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setFailure(caught);
        const exceeded = exceededChargeId(caught);
        if (
          caught instanceof ApiError &&
          caught.code === 'allocation_exceeds_open'
        ) {
          if (exceeded) {
            setErrors({
              lines: {
                [exceeded]: 'billing.accounts.validation.lineAboveOpen',
              },
            });
          }
          onReloadCharges?.();
        }
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!busyRef.current) {
      void submit();
    }
  }

  let allocationDetail;
  if (values.mode === 'none') {
    allocationDetail = (
      <p className="text-sm">{t('billing.accounts.record.noneHelp')}</p>
    );
  } else if (chargesError) {
    allocationDetail = (
      <ErrorAlert error={chargesError} onRetry={onReloadCharges} />
    );
  } else if (!charges) {
    allocationDetail = <LoadingState rows={3} />;
  } else if (values.mode === 'explicit') {
    allocationDetail = (
      <AllocationEditor
        charges={openCharges}
        lines={values.lines}
        onLines={(lines) => change('lines', lines)}
        errors={errors.lines}
        available={amount}
        remainderKey="billing.accounts.editor.remainderCredit"
        idPrefix={id}
        disabled={submitting}
      />
    );
  } else if (openCharges.length === 0) {
    allocationDetail = (
      <p className="text-sm">{t('billing.accounts.record.noOpenCharges')}</p>
    );
  } else if (amount === null) {
    allocationDetail = (
      <p className="text-sm text-muted-foreground">
        {t('billing.accounts.record.estimateNeedsAmount')}
      </p>
    );
  } else {
    const plan = fillOldestFirst(openCharges, amount);
    allocationDetail = (
      <div className="flex flex-col gap-2" data-allocation-estimate="">
        <p className="text-sm font-medium">
          {t('billing.accounts.record.estimate', {
            amount: labels.cents(plan.allocated),
            count: plan.lines.length,
            credit: labels.cents(plan.remainder),
          })}
        </p>
        <ul className="list-disc pl-5 text-sm">
          {plan.lines.map((line) => (
            <li key={line.charge.id}>
              {t('billing.accounts.record.estimateLine', {
                charge: labels.chargeLabel(line.charge),
                student: line.charge.student.name,
                amount: labels.cents(line.amount),
              })}
            </li>
          ))}
        </ul>
        <p className="text-sm text-muted-foreground">
          {t('billing.accounts.record.estimateNote')}
        </p>
      </div>
    );
  }

  let content;
  if (methodsError) {
    content = <ErrorAlert error={methodsError} onRetry={onRetryMethods} />;
  } else if (!methods) {
    content = <LoadingState rows={5} />;
  } else {
    content = (
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={fieldId('amount')} className="text-sm font-medium">
              {t('billing.accounts.record.fields.amount')}
            </label>
            <Input
              id={fieldId('amount')}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={values.amount}
              aria-invalid={errors.amount ? true : undefined}
              aria-describedby={describedBy('amount', true)}
              onChange={(event) => change('amount', event.target.value)}
            />
            <p id={helpId('amount')} className="text-sm text-muted-foreground">
              {t('billing.prices.hints.amount')}
            </p>
            <FieldError
              id={errorId('amount')}
              message={message(errors.amount)}
            />
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <label
              htmlFor={fieldId('receivedOn')}
              className="text-sm font-medium"
            >
              {t('billing.accounts.record.fields.receivedOn')}
            </label>
            <Input
              id={fieldId('receivedOn')}
              type="date"
              min={RECEIVED_ON_MIN}
              max={today}
              value={values.receivedOn}
              aria-invalid={errors.receivedOn ? true : undefined}
              aria-describedby={describedBy('receivedOn', true)}
              onChange={(event) => change('receivedOn', event.target.value)}
            />
            <p
              id={helpId('receivedOn')}
              className="text-sm text-muted-foreground"
            >
              {t('billing.accounts.record.hints.receivedOn')}
            </p>
            <FieldError
              id={errorId('receivedOn')}
              message={message(errors.receivedOn)}
            />
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <label htmlFor={fieldId('method')} className="text-sm font-medium">
              {t('billing.accounts.record.fields.method')}
            </label>
            <select
              id={fieldId('method')}
              className={SELECT_CLASS}
              value={values.method}
              aria-invalid={errors.method ? true : undefined}
              aria-describedby={describedBy('method')}
              onChange={(event) => change('method', event.target.value)}
            >
              <option value="">{t('billing.prices.choose')}</option>
              {active.map((method) => (
                <option key={method.code} value={method.code}>
                  {labels.method(method.code)}
                </option>
              ))}
            </select>
            <FieldError
              id={errorId('method')}
              message={message(errors.method)}
            />
          </div>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-2">
            <label
              htmlFor={fieldId('reference')}
              className="text-sm font-medium"
            >
              {t('billing.accounts.record.fields.reference')}
            </label>
            <Input
              id={fieldId('reference')}
              type="text"
              autoComplete="off"
              maxLength={REFERENCE_MAX}
              value={values.reference}
              aria-invalid={errors.reference ? true : undefined}
              aria-describedby={describedBy('reference')}
              onChange={(event) => change('reference', event.target.value)}
            />
            <FieldError
              id={errorId('reference')}
              message={message(errors.reference)}
            />
          </div>
          <div className="flex min-w-0 flex-col gap-2">
            <label
              htmlFor={fieldId('payerName')}
              className="text-sm font-medium"
            >
              {t('billing.accounts.record.fields.payerName')}
            </label>
            <Input
              id={fieldId('payerName')}
              type="text"
              autoComplete="off"
              maxLength={PAYER_NAME_MAX}
              value={values.payerName}
              aria-invalid={errors.payerName ? true : undefined}
              aria-describedby={describedBy('payerName')}
              onChange={(event) => change('payerName', event.target.value)}
            />
            <FieldError
              id={errorId('payerName')}
              message={message(errors.payerName)}
            />
          </div>
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <label htmlFor={fieldId('note')} className="text-sm font-medium">
            {t('billing.accounts.record.fields.note')}
          </label>
          <textarea
            id={fieldId('note')}
            rows={2}
            className={TEXTAREA_CLASS}
            maxLength={NOTE_MAX}
            value={values.note}
            aria-invalid={errors.note ? true : undefined}
            aria-describedby={describedBy('note', true)}
            onChange={(event) => change('note', event.target.value)}
          />
          <p id={helpId('note')} className="text-sm text-muted-foreground">
            {t('billing.accounts.record.hints.note')}
          </p>
          <FieldError id={errorId('note')} message={message(errors.note)} />
        </div>
        <fieldset
          className="flex min-w-0 flex-col gap-3"
          aria-describedby={
            errors.allocation ? errorId('allocation') : undefined
          }
        >
          <legend className="mb-2 text-sm font-medium">
            {t('billing.accounts.record.fields.allocation')}
          </legend>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {RECORD_MODES.map((mode: RecordMode) => (
              <label
                key={mode}
                className="flex min-h-6 items-center gap-2 text-sm"
              >
                <input
                  id={`${id}-mode-${mode}`}
                  type="radio"
                  name={`${id}-mode`}
                  value={mode}
                  className="size-4 accent-primary"
                  checked={values.mode === mode}
                  onChange={() => change('mode', mode)}
                />
                {t(`billing.accounts.record.modes.${mode}`)}
              </label>
            ))}
          </div>
          <FieldError
            id={errorId('allocation')}
            message={message(errors.allocation)}
          />
          {allocationDetail}
        </fieldset>
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
              <Spinner label={t('billing.accounts.record.saving')} showLabel />
            ) : (
              t('billing.accounts.record.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    );
  }

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-3xl"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.accounts.record.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.accounts.record.help')}
        </DialogDescription>
      </DialogHeader>
      {failure ? <ErrorAlert error={failure} /> : null}
      {content}
    </DialogContent>
  );
}
