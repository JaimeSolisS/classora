import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
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
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { useFormatters } from '@/lib/formatters';
import { useIdempotentSubmission } from '@/lib/idempotency';
import type { PayrollPeriodReview } from './api';
import { isCode, isUnknownResult } from './periodRules';

// "Finalize" a prepared period (#110b D6-D9), without any call. It repeats the
// tutor, the dates and the total of the review the page showed when it opened
// (a snapshot, so a refresh underneath never changes what is confirmed), and
// says plainly that the period then can't change. It sends that review's
// `prepared_at` (#110 E4).
//
// The key: one per opening of the dialog, computed before the call from the
// body, so apiCall's retry after the password dialog and a press after a lost
// response resend it; a new opening mounts a new body and gets a new key (D9).
// A cancelled password dialog leaves the dialog as it was, with no alert
// (D8). A lost response (no answer, or a 5xx) keeps the dialog open with a
// line saying the period may already be final and that finalizing again here
// is safe, while the page refreshes: the button waits for that refresh
// (`refreshing`). Any settled refusal is the page's to show: `onSubmit`
// closes the dialog itself then. Nothing retries by itself.

export interface FinalizePeriodDialogProps {
  open: boolean;
  /** The review the page showed when the dialog opened. */
  review: Pick<PayrollPeriodReview, 'period' | 'total'> | null;
  /** Finalizes; rejects with the error to show in the dialog. */
  onSubmit: (preparedAt: string, idempotencyKey: string) => Promise<void>;
  onClose: () => void;
  /** The page is reloading: the button waits. */
  refreshing: boolean;
}

export function FinalizePeriodDialog(props: FinalizePeriodDialogProps) {
  const busy = useRef(false);
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose();
      }}
    >
      {props.open && props.review ? (
        <Body {...props} review={props.review} busyRef={busy} />
      ) : null}
    </Dialog>
  );
}

function Body({
  review,
  onSubmit,
  onClose,
  refreshing,
  busyRef,
}: FinalizePeriodDialogProps & {
  review: NonNullable<FinalizePeriodDialogProps['review']>;
  busyRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const { formatLocalDate, formatMoney } = useFormatters();
  const returnFocus = useReturnFocus();
  const submission = useIdempotentSubmission();
  const [submitting, setSubmitting] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const { period } = review;

  async function confirm() {
    if (busyRef.current || period.prepared_at === null) return;
    const preparedAt = period.prepared_at;
    // Outside the call (#88): every attempt of this opening resends it.
    const key = submission.keyFor({
      period_id: period.id,
      prepared_at: preparedAt,
    });
    busyRef.current = true;
    setSubmitting(true);
    setUnknown(false);
    try {
      await onSubmit(preparedAt, key);
      submission.succeeded();
    } catch (caught) {
      if (isCode(caught, 'reauth_required')) {
        // The password dialog was cancelled: as it was, no alert (D8).
      } else if (isUnknownResult(caught)) {
        setUnknown(true);
      }
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  const dates = t('payroll.periods.dates', {
    start: formatLocalDate(period.starts_on),
    end: formatLocalDate(period.ends_on),
  });
  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-xl [&>*]:min-w-0"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
    >
      <DialogHeader>
        <DialogTitle>{t('payroll.finalizeDialog.title')}</DialogTitle>
        <DialogDescription>
          {t('payroll.finalizeDialog.warning')}
        </DialogDescription>
      </DialogHeader>
      <dl className="grid grid-cols-1 gap-1 text-sm" data-finalize-summary>
        <div className="min-w-0">
          <dt className="font-medium">{t('payroll.period.head.tutor')}</dt>
          <dd className="break-words">{period.tutor_display_name}</dd>
        </div>
        <div className="min-w-0">
          <dt className="font-medium">{t('payroll.period.head.dates')}</dt>
          <dd>{dates}</dd>
        </div>
        <div className="min-w-0">
          <dt className="font-medium">{t('payroll.period.head.total')}</dt>
          <dd className="font-medium" data-finalize-total={review.total}>
            {formatMoney(review.total)}
          </dd>
        </div>
      </dl>
      <p className="text-sm text-muted-foreground">
        {t('payroll.finalizeDialog.password')}
      </p>
      {unknown ? (
        <p
          role="alert"
          className="rounded-md border border-destructive p-3 text-sm"
          data-finalize-unknown
        >
          {t('payroll.finalizeDialog.unknown')}
        </p>
      ) : null}
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          className="min-h-11 md:min-h-9"
          disabled={submitting}
          onClick={onClose}
        >
          {t('payroll.finalizeDialog.cancel')}
        </Button>
        <Button
          type="button"
          className="min-h-11 md:min-h-9"
          disabled={submitting || refreshing}
          onClick={() => void confirm()}
        >
          {submitting ? (
            <Spinner label={t('payroll.finalizeDialog.submitting')} showLabel />
          ) : (
            t('payroll.finalizeDialog.submit')
          )}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
