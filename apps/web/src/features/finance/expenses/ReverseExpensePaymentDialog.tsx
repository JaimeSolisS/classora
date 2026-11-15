import { useId, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import type { AttemptFailure } from '@/features/billing/accounts/rules';
import { SHEET_CLASS } from '@/features/payroll/sheet';
import {
  useFieldMessage,
  useReturnFocus,
} from '@/features/scheduling/series/hooks';
import type { ExpensePaymentReversalCreate } from '../api';
import { FormField } from './FormField';
import { LockedDialog } from './LockedDialog';
import {
  REASON_MAX,
  reasonServerError,
  textLength,
  validateReason,
  type ExpenseMessage,
} from './rules';
import { useLockedSubmission, type LockedControl } from './useLockedSubmission';

// "Revertir" an expense payment (M7-12b D5), without any call: the reason is
// required; the password (`finance.refund_or_reversal`) is asked by apiCall,
// which resends the same body and key. `expense_payment_already_reversed`
// reaches the dialog after the page refetched. The lost-answer rule is E2
// (a): useLockedSubmission.

export interface ReverseExpensePaymentDialogProps {
  open: boolean;
  /** The payment in words: date, amount, method and reference. */
  summary: string;
  onSubmit: (body: ExpensePaymentReversalCreate, key: string) => Promise<void>;
  onSaved: () => void;
  onClose: (failed: AttemptFailure) => void;
  initialReason?: string;
}

export function ReverseExpensePaymentDialog(
  props: ReverseExpensePaymentDialogProps,
) {
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
  summary,
  onSubmit,
  onSaved,
  initialReason = '',
  busyRef,
  failedRef,
  onCancel,
}: ReverseExpensePaymentDialogProps & LockedControl) {
  const { t } = useTranslation();
  const message = useFieldMessage();
  const returnFocus = useReturnFocus();
  const id = useId();
  const submission = useLockedSubmission<ExpensePaymentReversalCreate>(
    busyRef,
    failedRef,
  );
  const [reason, setReason] = useState(initialReason);
  const [error, setError] = useState<ExpenseMessage | null>(null);
  const reasonId = `${id}-reason`;

  function onFormSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submission.submit({
      build: () => {
        const found = validateReason(reason);
        setError(found);
        if (found) {
          document.getElementById(reasonId)?.focus();
          return null;
        }
        return { reason: reason.trim() };
      },
      send: onSubmit,
      onSaved,
      onRefused: (caught) => {
        const found = reasonServerError(caught);
        if (!found) return false;
        setError(found);
        document.getElementById(reasonId)?.focus();
        return true;
      },
    });
  }

  return (
    <DialogContent
      className={SHEET_CLASS}
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
      data-expense-dialog="reverse"
    >
      <DialogHeader>
        <DialogTitle>{t(`${D}.reverse.title`)}</DialogTitle>
        <DialogDescription>{t(`${D}.reverse.help`)}</DialogDescription>
      </DialogHeader>
      {submission.failure ? (
        <ErrorAlert
          error={submission.failure}
          message={submission.lost ? t(`${D}.reverse.lost`) : undefined}
        />
      ) : null}
      <form
        method="post"
        noValidate
        onSubmit={onFormSubmit}
        className="flex min-w-0 flex-col gap-4"
      >
        <p className="text-sm break-words" data-reversal-payment>
          {t(`${D}.reverse.payment`, { payment: summary })}
        </p>
        <FormField
          id={reasonId}
          label={t(`${D}.reverse.reason`)}
          counter={t(`${D}.counter`, {
            n: textLength(reason),
            max: REASON_MAX,
          })}
          error={message(error ?? undefined)}
        >
          {(field) => (
            <textarea
              {...field}
              rows={3}
              className={TEXTAREA_CLASS}
              value={reason}
              disabled={submission.locked}
              onChange={(event) => {
                setReason(event.target.value);
                setError(null);
              }}
            />
          )}
        </FormField>
        <p className="text-sm text-muted-foreground">
          {t(`${D}.adjust.password`)}
        </p>
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
            variant="destructive"
            className="min-h-11 md:min-h-9"
            disabled={submission.submitting}
          >
            {submission.submitting ? (
              <Spinner label={t('finance.saving')} showLabel />
            ) : submission.lost ? (
              t(`${D}.reverse.resend`)
            ) : (
              t(`${D}.reverse.submit`)
            )}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  );
}
