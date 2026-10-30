import { useQuery, useQueryClient } from '@tanstack/react-query';
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
import { TEXTAREA_CLASS } from '@/features/academics/styles';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { useReturnFocus } from '@/features/scheduling/series/hooks';
import { todayInZone, type LocalDate } from '@/lib/time';
import {
  fetchPaymentMethods,
  PAYMENT_METHODS_KEY,
  type PaymentMethod,
} from '../accounts/api';
import { REASON_MAX } from '../accounts/corrections';
import {
  confirmWithdrawal,
  fetchPreview,
  invalidateAfterWithdrawal,
  previewKey,
  type SubjectRef,
  type WithdrawalConfirmed,
  type WithdrawalCreditConfirm,
  type WithdrawalPreview,
  type WithdrawalRefundConfirm,
} from './api';
import { nothingToSettle } from './rules';
import { WithdrawalSettlement } from './WithdrawalSettlement';

// The withdrawal dialog (#387 B1-B3): the settlement panel for a subject,
// optionally after a first step. From a roster, a user with
// schedule.manage and billing.view withdraws an active enrollment in two
// steps: (1) what happens and the reason (the M4 fields; the reason is
// carried to the settlement, where it is required), (2) the settlement,
// whose confirm withdraws and settles in one call (#101 C2). "Settle
// billing" on a withdrawn enrollment and "Withdraw (unused units)" on a
// package open the settlement directly. `WithdrawalDialog` has no query
// (stories render each state); `WithdrawalFlowDialog` reads the preview
// (the live query data the panel renders) and the payment methods,
// confirms, puts a stale answer's fresh preview in the query, and after
// the 201 calls `onDone` (the caller closes it, announces it in its polite
// status and refreshes its list) and invalidates the withdrawal reads and
// the family's account (B5). #387 QA D2: on a roster withdrawal with
// nothing to settle (`nothingToSettle`), the second step offers
// "Withdraw without settlement" instead (the M4 route, no password).

export type PreviewState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; preview: WithdrawalPreview };

/** The texts of the first step (a withdrawal from a roster). */
export interface WithdrawStepLabels {
  description: string;
  help?: string;
  reason: string;
  keep: string;
  next: string;
}

export interface WithdrawalDialogProps {
  open: boolean;
  /** Remounts the content per subject. */
  subjectKey: string;
  title: string;
  subjectName: string;
  /** Two steps: the withdrawal's own step first. */
  firstStep?: WithdrawStepLabels;
  state: PreviewState;
  canCredit: boolean;
  canRefund: boolean;
  methods: readonly PaymentMethod[] | null;
  today: LocalDate;
  onConfirm: (
    body: WithdrawalCreditConfirm | WithdrawalRefundConfirm,
    idempotencyKey: string,
  ) => Promise<void>;
  onStale: (fresh: WithdrawalPreview) => void;
  onRefetch: () => void;
  onClose: () => void;
  /**
   * #387 QA D2: the M4 withdrawal without a settlement (the plain route,
   * no password), offered on the settlement step of a roster withdrawal
   * when there is nothing to settle (`nothingToSettle`).
   */
  onWithdrawPlain?: (reason: string) => Promise<void>;
  /** Stories: start on the settlement step. */
  initialStep?: 'withdraw' | 'settle';
}

export function WithdrawalDialog(props: WithdrawalDialogProps) {
  const busy = useRef(false);
  const returnFocus = useReturnFocus();
  return (
    <Dialog
      open={props.open}
      onOpenChange={(next) => {
        if (!next && !busy.current) props.onClose();
      }}
    >
      {props.open ? (
        <DialogContent
          className="max-h-[90vh] overflow-y-auto sm:max-w-4xl [&>*]:min-w-0"
          onOpenAutoFocus={returnFocus.onOpenAutoFocus}
          onCloseAutoFocus={returnFocus.onCloseAutoFocus}
          onInteractOutside={(event) => event.preventDefault()}
        >
          <Steps key={props.subjectKey} {...props} busy={busy} />
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function Steps({
  title,
  subjectName,
  firstStep,
  state,
  canCredit,
  canRefund,
  methods,
  today,
  onConfirm,
  onStale,
  onRefetch,
  onClose,
  onWithdrawPlain,
  initialStep,
  busy,
}: WithdrawalDialogProps & { busy: { current: boolean } }) {
  const { t } = useTranslation();
  const id = useId();
  const [step, setStep] = useState<'withdraw' | 'settle'>(
    initialStep ?? (firstStep ? 'withdraw' : 'settle'),
  );
  const [reason, setReason] = useState('');

  function next(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStep('settle');
  }

  if (step === 'withdraw' && firstStep) {
    return (
      <>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{firstStep.description}</DialogDescription>
          {firstStep.help ? <p className="text-sm">{firstStep.help}</p> : null}
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          {t('billing.withdrawals.dialog.stepOne')}
        </p>
        <form
          noValidate
          onSubmit={next}
          className="flex min-w-0 flex-col gap-4"
        >
          <div className="flex flex-col gap-2">
            <label htmlFor={`${id}-reason`} className="text-sm font-medium">
              {firstStep.reason}
            </label>
            <textarea
              id={`${id}-reason`}
              rows={3}
              maxLength={REASON_MAX}
              className={TEXTAREA_CLASS}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {firstStep.keep}
            </Button>
            <Button type="submit">{firstStep.next}</Button>
          </DialogFooter>
        </form>
      </>
    );
  }

  let body;
  if (state.status === 'loading') {
    body = <LoadingState rows={6} />;
  } else if (state.status === 'error') {
    body = <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  } else if (firstStep && onWithdrawPlain && nothingToSettle(state.preview)) {
    body = (
      <PlainWithdrawal
        preview={state.preview}
        reason={reason}
        onWithdraw={onWithdrawPlain}
        onBack={() => setStep('withdraw')}
        onCancel={onClose}
        busyRef={busy}
      />
    );
  } else {
    body = (
      <WithdrawalSettlement
        preview={state.preview}
        subjectName={subjectName}
        canCredit={canCredit}
        canRefund={canRefund}
        methods={methods}
        today={today}
        initialReason={reason}
        onConfirm={onConfirm}
        onStale={onStale}
        onRefetch={onRefetch}
        onCancel={onClose}
        onBack={firstStep ? () => setStep('withdraw') : undefined}
        busyRef={busy}
      />
    );
  }
  return (
    <>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>
          {firstStep
            ? t('billing.withdrawals.dialog.stepTwo')
            : t('billing.withdrawals.dialog.help')}
        </DialogDescription>
      </DialogHeader>
      {body}
    </>
  );
}

/**
 * #387 QA D2: nothing to settle (a student without a family, or an empty
 * settlement): why, and "Withdraw without settlement", the M4 route with
 * the reason of the first step. An error stays here as an alert.
 */
function PlainWithdrawal({
  preview,
  reason,
  onWithdraw,
  onBack,
  onCancel,
  busyRef,
}: {
  preview: WithdrawalPreview;
  reason: string;
  onWithdraw: (reason: string) => Promise<void>;
  onBack: () => void;
  onCancel: () => void;
  busyRef: { current: boolean };
}) {
  const { t } = useTranslation();
  const [failure, setFailure] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const noFamily = preview.blocking.some(
    (item) => item.code === 'student_has_no_family',
  );

  async function withdraw() {
    if (busyRef.current) return;
    setFailure(null);
    busyRef.current = true;
    setSubmitting(true);
    try {
      await onWithdraw(reason);
    } catch (caught) {
      setFailure(caught);
    } finally {
      busyRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-4" data-withdrawal-plain>
      <p className="text-sm">
        {noFamily
          ? t('billing.withdrawals.plain.noFamily')
          : t('billing.withdrawals.plain.nothing')}
      </p>
      <p className="text-sm text-muted-foreground">
        {t('billing.withdrawals.plain.help')}
      </p>
      {failure ? <ErrorAlert error={failure} /> : null}
      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          disabled={submitting}
          onClick={onBack}
        >
          {t('billing.withdrawals.back')}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={submitting}
          onClick={onCancel}
        >
          {t('billing.withdrawals.cancel')}
        </Button>
        <Button
          type="button"
          variant="destructive"
          disabled={submitting}
          onClick={() => void withdraw()}
        >
          {submitting ? (
            <Spinner
              label={t('billing.withdrawals.plain.submitting')}
              showLabel
            />
          ) : (
            t('billing.withdrawals.plain.submit')
          )}
        </Button>
      </DialogFooter>
    </div>
  );
}

export interface WithdrawalFlowDialogProps {
  /** The subject; null: closed. */
  subject: SubjectRef | null;
  title: string;
  subjectName: string;
  firstStep?: WithdrawStepLabels;
  onDone: (confirmed: WithdrawalConfirmed) => void;
  onClose: () => void;
  /** #387 QA D2: the M4 withdrawal when there is nothing to settle. */
  onWithdrawPlain?: (reason: string) => Promise<void>;
}

export function WithdrawalFlowDialog({
  subject,
  title,
  subjectName,
  firstStep,
  onDone,
  onClose,
  onWithdrawPlain,
}: WithdrawalFlowDialogProps) {
  const client = useQueryClient();
  const timeZone = useCenterTimeZone();
  const canCredit = useCan('billing.manage');
  const canRefund = useCan('finance.refund.approve');
  const key = subject ? previewKey(subject) : null;
  const preview = useQuery({
    queryKey: key ?? ['billing', 'withdrawals', 'preview', 'none'],
    queryFn: ({ signal }) => fetchPreview(subject!, signal),
    enabled: subject !== null,
    staleTime: 0,
    retry: false,
  });
  const methods = useQuery({
    queryKey: PAYMENT_METHODS_KEY,
    queryFn: ({ signal }) => fetchPaymentMethods(signal),
    enabled: subject !== null && canRefund,
  });

  const state: PreviewState = preview.data
    ? { status: 'ready', preview: preview.data }
    : preview.isError
      ? {
          status: 'error',
          error: preview.error,
          onRetry: () => void preview.refetch(),
        }
      : { status: 'loading' };

  return (
    <WithdrawalDialog
      open={subject !== null}
      subjectKey={subject ? `${subject.kind}-${subject.id}` : 'none'}
      title={title}
      subjectName={subjectName}
      firstStep={firstStep}
      state={state}
      canCredit={canCredit}
      canRefund={canRefund}
      methods={methods.data ?? null}
      today={todayInZone(timeZone)}
      onConfirm={async (body, idempotencyKey) => {
        const confirmed = await confirmWithdrawal(body, idempotencyKey);
        onDone(confirmed);
        void invalidateAfterWithdrawal(client, confirmed.withdrawal.family_id);
      }}
      onStale={(fresh) => {
        if (key) client.setQueryData(key, fresh);
      }}
      onRefetch={() => void preview.refetch()}
      onClose={onClose}
      onWithdrawPlain={onWithdrawPlain}
    />
  );
}
