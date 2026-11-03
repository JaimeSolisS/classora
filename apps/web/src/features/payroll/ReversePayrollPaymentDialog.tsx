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
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { FieldError } from '@/features/auth/FieldError';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import { useIdempotentSubmission } from '@/lib/idempotency';
import type { PayrollPaymentReversalCreate } from './api';
import {
  REVERSAL_REASON_MAX,
  reversalServerError,
  textLength,
  validateReversalReason,
  worse,
  type AttemptFailure,
  type PaymentDialogOutcome,
} from './paymentForm';
import { isCode, isUnknownResult } from './periodRules';
import { SHEET_CLASS } from './sheet';

// "Revertir" a payroll payment (M7-09b C), without any API call. The reason is
// required. One idempotent submission per opening: the key is computed before
// the call, so apiCall's retry after the password dialog
// (`finance.refund_or_reversal`) resends it, and so does pressing the button
// again after a lost response with the same reason. A cancelled password
// dialog leaves the dialog as it was. The lost-response rules are the payment
// dialog's (B3): the outcome is unknown, and closing after any failed attempt
// makes the page refetch the payments and the period.

export interface ReversePayrollPaymentDialogProps {
  open: boolean;
  /** The payment in words: date, amount, method and reference. */
  summary: string;
  /** Reverses; rejects with the error to show. */
  onSubmit: (body: PayrollPaymentReversalCreate, key: string) => Promise<void>;
  onClose: (outcome: PaymentDialogOutcome, failed: AttemptFailure) => void;
  /** Stories: the reason to start from. */
  initialReason?: string;
}

export function ReversePayrollPaymentDialog(
  props: ReversePayrollPaymentDialogProps,
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
  summary,
  onSubmit,
  onClose,
  initialReason = '',
  busyRef,
  failedRef,
}: ReversePayrollPaymentDialogProps & {
  busyRef: { current: boolean };
  failedRef: { current: AttemptFailure };
}) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const submission = useIdempotentSubmission();
  const id = useId();
  const [reason, setReason] = useState(initialReason);
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const D = 'payroll.reversalDialog';
  const ids = {
    reason: `${id}-reason`,
    help: `${id}-reason-help`,
    counter: `${id}-reason-counter`,
    error: `${id}-reason-error`,
  };

  async function submit() {
    const found = validateReversalReason(reason);
    setError(found);
    if (found) {
      document.getElementById(ids.reason)?.focus();
      return;
    }
    const body: PayrollPaymentReversalCreate = { reason: reason.trim() };
    // Outside the call (#88): the password retry and a resend reuse it.
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
      if (isCode(caught, 'reauth_required')) {
        // The password dialog was cancelled: nothing was sent, as it was.
      } else {
        failedRef.current = worse(failedRef.current, caught);
        const fromServer = isUnknownResult(caught)
          ? null
          : reversalServerError(caught);
        if (fromServer) {
          setError(fromServer);
          document.getElementById(ids.reason)?.focus();
        } else {
          setFailure(caught);
        }
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

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
      data-reversal-dialog
    >
      <DialogHeader>
        <DialogTitle>{t(`${D}.title`)}</DialogTitle>
        <DialogDescription>{t(`${D}.help`)}</DialogDescription>
      </DialogHeader>
      {failure ? (
        <ErrorAlert
          error={failure}
          message={isUnknownResult(failure) ? t(`${D}.unknown`) : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex flex-col gap-4"
      >
        <p className="text-sm break-words" data-reversal-payment>
          {t(`${D}.payment`, { payment: summary })}
        </p>
        <div className="flex flex-col gap-2">
          <label htmlFor={ids.reason} className="text-sm font-medium">
            {t(`${D}.reason`)}
          </label>
          <textarea
            id={ids.reason}
            rows={3}
            className={TEXTAREA_CLASS}
            value={reason}
            aria-invalid={error ? true : undefined}
            aria-describedby={[ids.help, ids.counter, error ? ids.error : '']
              .filter(Boolean)
              .join(' ')}
            onChange={(event) => {
              setReason(event.target.value);
              setError(null);
            }}
          />
          <p id={ids.help} className="text-sm text-muted-foreground">
            {t(`${D}.reasonHelp`)}
          </p>
          <p id={ids.counter} className="text-sm text-muted-foreground">
            {t(`${D}.counter`, {
              n: textLength(reason),
              max: REVERSAL_REASON_MAX,
            })}
          </p>
          <FieldError id={ids.error} message={message(error ?? undefined)} />
        </div>
        <p className="text-sm text-muted-foreground">{t(`${D}.password`)}</p>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            className="min-h-11 md:min-h-9"
            disabled={submitting}
            onClick={() => {
              onClose('cancelled', failedRef.current);
              failedRef.current = 'none';
            }}
          >
            {t('payroll.cancel')}
          </Button>
          <Button
            type="submit"
            variant="destructive"
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
    </DialogContent>
  );
}
