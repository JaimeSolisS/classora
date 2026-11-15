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
import { absoluteAmount, isUnknownResult } from './periodRules';

// "Carry the balance to the next period" (M7-08b C1, E2), without any call.
// It names the amount of the review the page showed when it opened (a
// snapshot) and says it moves, it isn't forgiven. One key per opening,
// computed before the call. A lost answer keeps the dialog open with a line
// saying the balance may have moved and that confirming again here is safe,
// while the page refreshes: the button waits for that refresh (`refreshing`).
// A settled refusal is the page's to show: `onSubmit` closes the dialog then.

export interface CarryForwardDialogProps {
  open: boolean;
  review: Pick<PayrollPeriodReview, 'period' | 'total'> | null;
  onSubmit: (idempotencyKey: string) => Promise<void>;
  onClose: () => void;
  refreshing: boolean;
}

export function CarryForwardDialog(props: CarryForwardDialogProps) {
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
}: CarryForwardDialogProps & {
  review: NonNullable<CarryForwardDialogProps['review']>;
  busyRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const { formatLocalDate, formatMoney } = useFormatters();
  const returnFocus = useReturnFocus();
  const submission = useIdempotentSubmission();
  const [submitting, setSubmitting] = useState(false);
  const [unknown, setUnknown] = useState(false);
  const { period } = review;
  const C = 'payroll.carryDialog';

  async function confirm() {
    if (busyRef.current) return;
    // Outside the call (#88): every attempt of this opening resends it.
    const key = submission.keyFor({
      period_id: period.id,
      total: review.total,
    });
    busyRef.current = true;
    setSubmitting(true);
    setUnknown(false);
    try {
      await onSubmit(key);
      submission.succeeded();
    } catch (caught) {
      if (isUnknownResult(caught)) setUnknown(true);
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <DialogContent
      className="max-h-[90vh] overflow-y-auto sm:max-w-xl [&>*]:min-w-0"
      onOpenAutoFocus={returnFocus.onOpenAutoFocus}
      onCloseAutoFocus={returnFocus.onCloseAutoFocus}
      onInteractOutside={(event) => event.preventDefault()}
      data-carry-dialog
    >
      <DialogHeader>
        <DialogTitle>{t(`${C}.title`)}</DialogTitle>
        <DialogDescription data-carry-amount={absoluteAmount(review.total)}>
          {t(`${C}.body`, {
            amount: formatMoney(absoluteAmount(review.total)),
            name: period.tutor_display_name,
            dates: t('payroll.periods.dates', {
              start: formatLocalDate(period.starts_on),
              end: formatLocalDate(period.ends_on),
            }),
          })}
        </DialogDescription>
      </DialogHeader>
      <p className="text-sm">{t(`${C}.notForgiven`)}</p>
      {unknown ? (
        <p
          role="alert"
          className="rounded-md border border-destructive p-3 text-sm"
          data-carry-unknown
        >
          {t(`${C}.unknown`)}
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
          {t('payroll.cancel')}
        </Button>
        <Button
          type="button"
          className="min-h-11 md:min-h-9"
          disabled={submitting || refreshing}
          onClick={() => void confirm()}
        >
          {submitting ? (
            <Spinner label={t('payroll.saving')} showLabel />
          ) : (
            t(`${C}.submit`)
          )}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
