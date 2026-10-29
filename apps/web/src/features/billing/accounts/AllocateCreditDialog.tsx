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
import { FieldError } from '@/features/auth/FieldError';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { useIdempotentSubmission } from '@/lib/idempotency';
import { AllocationEditor } from './AllocationEditor';
import type { ChargeItem, CreditAllocationCreate } from './api';
import { apiCents } from './cents';
import { useAccountLabels } from './labels';
import {
  CREDIT_MODES,
  creditBody,
  creditServerErrors,
  EMPTY_CREDIT_FORM,
  exceededChargeId,
  hasErrors,
  validateCreditForm,
  type CreditErrors,
  type CreditFormValues,
} from './rules';

// "Apply account credit" (#379 E; #96 M4), without any call: oldest first
// (an optional amount; empty applies the whole credit) or chosen charges
// (the allocation editor, capped by the credit). The same idempotent,
// non-optimistic submission as "Record payment" (D3, D4). A 409
// insufficient_account_credit / nothing_to_allocate / allocation_exceeds_open
// is an alert and reloads the summary and the open charges (`onStale`).

const STALE_CODES = new Set([
  'insufficient_account_credit',
  'nothing_to_allocate',
  'allocation_exceeds_open',
]);

export interface AllocateCreditDialogProps {
  open: boolean;
  /** The account credit (the summary's `account_credit`). */
  credit: string;
  /** The account's open charges, oldest first; null while they load. */
  charges: readonly ChargeItem[] | null;
  chargesError?: unknown;
  onReloadCharges?: () => void;
  /** Reloads the summary and the open charges after a 409. */
  onStale?: () => void;
  /** Allocates with the key; rejects with the error to show. */
  onSubmit: (
    body: CreditAllocationCreate,
    idempotencyKey: string,
  ) => Promise<void>;
  onClose: () => void;
  /** Stories: the values to start from. */
  initialValues?: Partial<CreditFormValues>;
}

export function AllocateCreditDialog(props: AllocateCreditDialogProps) {
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
  credit,
  charges,
  chargesError,
  onReloadCharges,
  onStale,
  onSubmit,
  onClose,
  initialValues,
  busyRef,
}: AllocateCreditDialogProps & { busyRef: { current: boolean } }) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const labels = useAccountLabels();
  const returnFocus = useReturnFocus();
  const submission = useIdempotentSubmission();
  const id = useId();
  const [values, setValues] = useState<CreditFormValues>(() => ({
    ...EMPTY_CREDIT_FORM,
    ...initialValues,
  }));
  const [errors, setErrors] = useState<CreditErrors>({});
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const available = apiCents(credit);
  const openCharges = charges ?? [];
  const ids = {
    amount: `${id}-amount`,
    amountHelp: `${id}-amount-help`,
    amountError: `${id}-amount-error`,
    allocationError: `${id}-allocation-error`,
  };

  function focusFirst(found: CreditErrors) {
    const lineId = Object.keys(found.lines ?? {})[0];
    const target = found.amount
      ? document.getElementById(ids.amount)
      : found.allocation
        ? document.getElementById(`${id}-mode-${values.mode}`)
        : lineId
          ? document.getElementById(`${id}-line-${lineId}`)
          : null;
    target?.focus();
  }

  async function submit() {
    setFailure(null);
    const found = validateCreditForm(values, available, openCharges);
    setErrors(found);
    if (hasErrors(found)) {
      focusFirst(found);
      return;
    }
    const body = creditBody(values, openCharges);
    const sent = body.allocation.lines ?? [];
    // Outside the call (#88): a retry of the same body resends this key.
    const key = submission.keyFor(body);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onSubmit(body, key);
      submission.succeeded();
    } catch (caught) {
      const fromServer = creditServerErrors(caught, sent);
      if (fromServer) {
        setErrors(fromServer);
        focusFirst(fromServer);
      } else {
        setFailure(caught);
        if (caught instanceof ApiError && STALE_CODES.has(caught.code)) {
          const exceeded = exceededChargeId(caught);
          if (exceeded) {
            setErrors({
              lines: {
                [exceeded]: 'billing.accounts.validation.lineAboveOpen',
              },
            });
          }
          onStale?.();
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

  let detail;
  if (values.mode === 'oldest_first') {
    detail = (
      <div className="flex min-w-0 flex-col gap-2 sm:max-w-xs">
        <label htmlFor={ids.amount} className="text-sm font-medium">
          {t('billing.accounts.credit.fields.amount')}
        </label>
        <Input
          id={ids.amount}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          value={values.amount}
          disabled={submitting}
          aria-invalid={errors.amount ? true : undefined}
          aria-describedby={[
            ids.amountHelp,
            errors.amount ? ids.amountError : undefined,
          ]
            .filter(Boolean)
            .join(' ')}
          onChange={(event) => {
            setValues((current) => ({
              ...current,
              amount: event.target.value,
            }));
            setErrors((current) => ({ ...current, amount: undefined }));
          }}
        />
        <p id={ids.amountHelp} className="text-sm text-muted-foreground">
          {t('billing.accounts.credit.hints.amount', {
            amount: labels.money(credit),
          })}
        </p>
        <FieldError id={ids.amountError} message={message(errors.amount)} />
      </div>
    );
  } else if (chargesError) {
    detail = <ErrorAlert error={chargesError} onRetry={onReloadCharges} />;
  } else if (!charges) {
    detail = <LoadingState rows={3} />;
  } else {
    detail = (
      <AllocationEditor
        charges={openCharges}
        lines={values.lines}
        onLines={(lines) => {
          setValues((current) => ({ ...current, lines }));
          setErrors((current) => ({
            ...current,
            lines: undefined,
            allocation: undefined,
          }));
        }}
        errors={errors.lines}
        available={available}
        remainderKey="billing.accounts.editor.remainderUnused"
        idPrefix={id}
        disabled={submitting}
      />
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
        <DialogTitle>{t('billing.accounts.credit.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.accounts.credit.help', { amount: labels.money(credit) })}
        </DialogDescription>
      </DialogHeader>
      {failure ? <ErrorAlert error={failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <fieldset
          className="flex min-w-0 flex-col gap-3"
          aria-describedby={errors.allocation ? ids.allocationError : undefined}
        >
          <legend className="mb-2 text-sm font-medium">
            {t('billing.accounts.record.fields.allocation')}
          </legend>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {CREDIT_MODES.map((mode) => (
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
                  onChange={() => {
                    setValues((current) => ({ ...current, mode }));
                    setErrors({});
                  }}
                />
                {t(`billing.accounts.record.modes.${mode}`)}
              </label>
            ))}
          </div>
          <FieldError
            id={ids.allocationError}
            message={message(errors.allocation)}
          />
          {detail}
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
              <Spinner label={t('billing.accounts.credit.saving')} showLabel />
            ) : (
              t('billing.accounts.credit.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
