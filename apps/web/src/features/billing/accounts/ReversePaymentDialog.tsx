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
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import type { PaymentDetail, PaymentReversalCreate } from './api';
import { CorrectionField } from './CorrectionField';
import {
  anyError,
  isCode,
  REASON_MAX,
  reversalBody,
  reversalServerErrors,
  REVERSAL_STALE_CODES,
  validateReversal,
  type ReversalErrors,
} from './corrections';
import { useAccountLabels } from './labels';
import { useCorrectionSubmission } from './useCorrectionSubmission';

// "Reverse payment" (#386 B2; #100 C3), without any call: what will happen
// (the allocations are released and their charges reopen, the remainder
// stops being account credit, the payment stays and its receipt is marked
// reversed), the #96 note on mis-allocations, and the reason. Needs a
// recent password confirmation (apiCall's dialog, the same key on the
// retry). A 409 payment_already_reversed or payment_has_refunds is an
// alert and reloads the payment.

export interface ReversePaymentDialogProps {
  open: boolean;
  payment: Pick<PaymentDetail, 'id' | 'amount' | 'received_on'> | null;
  onSubmit: (
    body: PaymentReversalCreate,
    idempotencyKey: string,
  ) => Promise<void>;
  onStale?: () => void;
  onClose: () => void;
  initialReason?: string;
  initialErrors?: ReversalErrors;
}

export function ReversePaymentDialog(props: ReversePaymentDialogProps) {
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
  payment,
  onSubmit,
  onStale,
  onClose,
  initialReason = '',
  initialErrors = {},
  busy,
}: ReversePaymentDialogProps & {
  payment: NonNullable<ReversePaymentDialogProps['payment']>;
  busy: { current: boolean };
}) {
  const { t } = useTranslation();
  const labels = useAccountLabels();
  const returnFocus = useReturnFocus();
  const id = useId();
  const submission = useCorrectionSubmission(busy);
  const [reason, setReason] = useState(initialReason);
  const [errors, setErrors] = useState<ReversalErrors>(initialErrors);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validateReversal({ reason });
    setErrors(found);
    if (anyError(found)) {
      document.getElementById(`${id}-reason`)?.focus();
      return;
    }
    await submission.run(reversalBody({ reason }), onSubmit, {
      onFields: (caught) => {
        const fromServer = reversalServerErrors(caught);
        if (!fromServer) return false;
        setErrors(fromServer);
        return true;
      },
      onStale: (caught) => {
        if (isCode(caught, ...REVERSAL_STALE_CODES)) onStale?.();
      },
    });
  }

  const { submitting } = submission;
  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-xl [&>*]:min-w-0"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('billing.accounts.reverse.title')}</DialogTitle>
        <DialogDescription>
          {t('billing.accounts.reverse.help', {
            amount: labels.money(payment.amount),
            date: labels.date(payment.received_on),
          })}
        </DialogDescription>
      </DialogHeader>
      <ul className="list-disc pl-5 text-sm">
        <li>{t('billing.accounts.reverse.effects.charges')}</li>
        <li>{t('billing.accounts.reverse.effects.credit')}</li>
        <li>{t('billing.accounts.reverse.effects.receipt')}</li>
      </ul>
      <p className="text-sm">{t('billing.accounts.reverse.misallocation')}</p>
      <p className="text-sm text-muted-foreground">
        {t('billing.accounts.reverse.passwordNote')}
      </p>
      {submission.failure ? <ErrorAlert error={submission.failure} /> : null}
      <form
        method="post"
        noValidate
        onSubmit={(event) => void submit(event)}
        className="flex min-w-0 flex-col gap-4"
      >
        <CorrectionField
          id={`${id}-reason`}
          label={t('billing.accounts.reverse.fields.reason')}
          error={errors.reason}
        >
          {(wiring) => (
            <textarea
              {...wiring}
              rows={2}
              className={TEXTAREA_CLASS}
              maxLength={REASON_MAX}
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
                setErrors({});
              }}
            />
          )}
        </CorrectionField>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={submitting}
            onClick={onClose}
          >
            {t('billing.prices.cancel')}
          </Button>
          <Button type="submit" variant="destructive" disabled={submitting}>
            {submitting ? (
              <Spinner label={t('billing.accounts.reverse.saving')} showLabel />
            ) : (
              t('billing.accounts.reverse.submit')
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
