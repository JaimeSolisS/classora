import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { SessionUser } from '@/features/auth/session';
import {
  fetchPaymentMethods,
  PAYMENT_METHODS_KEY,
} from '@/features/billing/accounts/api';
import type { LocalDate } from '@/lib/time';
import {
  fetchPeriodPayments,
  PAYROLL_PERIODS_KEY,
  periodPaymentsKey,
  recordPayrollPayment,
  reversePayrollPayment,
  type PayrollPayment,
  type PayrollPaymentCreate,
  type PayrollPaymentReversalCreate,
  type PayrollPeriodDetail,
} from './api';
import { usePaymentSummary } from './labels';
import {
  paymentCapabilities,
  remainingOf,
  REFETCH_AFTER_PAYMENT,
  type AttemptFailure,
  type PaymentDialogOutcome,
} from './paymentForm';
import { PeriodPaymentsView, type PaymentsState } from './PeriodPaymentsView';
import { isCode, isUnknownResult } from './periodRules';
import {
  RecordPayrollPaymentDialog,
  type PaymentMethodOptions,
} from './RecordPayrollPaymentDialog';
import { ReversePayrollPaymentDialog } from './ReversePayrollPaymentDialog';

// The "Pagos" section of a finalized period's review (M7-09b E1), with its
// query and its two dialogs. Who sees what (A1, A2): payroll.payment.record
// holders log a payment while something is left to pay; finance.refund.approve
// holders reverse a payment that isn't reversed; neither on a non-Owner's own
// pay (the API's 403 is still shown). Everyone else who reads the period
// (a delegated Administrator) reads the section only.
//
// A success refetches the payments and the period: the answers carry no
// amount (#112 E6). A refusal that means the list moved (the cap, a duplicate,
// already reversed) refetches before the dialog shows it, so the duplicate can
// name the payment already logged. A lost response refetches when the dialog
// closes, by whatever route (E2), and so does closing after any failed
// attempt; until that refetch lands, nothing can be submitted again. A
// refetch that fails says the rows may be out of date and never claims
// they reloaded (D4). The app has no toast: outcomes go to the section's
// status region.

export interface PeriodPaymentsSectionProps {
  period: PayrollPeriodDetail;
  user: SessionUser | null;
  today: LocalDate;
  /** The page runs an action or may be out of date: nothing starts. */
  locked: boolean;
}

type OpenDialog =
  { kind: 'record' } | { kind: 'reverse'; payment: PayrollPayment };

export function PeriodPaymentsSection({
  period,
  user,
  today,
  locked,
}: PeriodPaymentsSectionProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const summary = usePaymentSummary();
  const can = paymentCapabilities(user, period);
  const [dialog, setDialog] = useState<OpenDialog | null>(null);
  const [announcement, setAnnouncement] = useState('');

  const payments = useQuery({
    queryKey: periodPaymentsKey(period.id),
    queryFn: ({ signal }) => fetchPeriodPayments(period.id, signal),
  });
  const methods = useQuery({
    queryKey: PAYMENT_METHODS_KEY,
    queryFn: ({ signal }) => fetchPaymentMethods(signal),
    enabled: dialog?.kind === 'record',
  });

  const state: PaymentsState = payments.data
    ? {
        status: 'ready',
        items: payments.data,
        refreshError: payments.isError ? payments.error : null,
        onRefresh: () => void payments.refetch(),
      }
    : payments.isError
      ? {
          status: 'error',
          error: payments.error,
          onRetry: () => void payments.refetch(),
        }
      : { status: 'loading' };
  const methodOptions: PaymentMethodOptions = methods.data
    ? { status: 'ready', methods: methods.data }
    : methods.isError
      ? {
          status: 'error',
          error: methods.error,
          onRetry: () => void methods.refetch(),
        }
      : { status: 'loading' };

  /** The payments, the period and the list. Never rejects. */
  async function refresh(): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: PAYROLL_PERIODS_KEY });
  }

  /** After a refusal or a lost answer that may mean the list moved. */
  async function refreshAfter(caught: unknown): Promise<void> {
    if (isUnknownResult(caught) || isCode(caught, ...REFETCH_AFTER_PAYMENT)) {
      await refresh();
    }
  }

  async function record(body: PayrollPaymentCreate, key: string) {
    try {
      await recordPayrollPayment(period.id, body, key);
    } catch (caught) {
      await refreshAfter(caught);
      throw caught;
    }
    void refresh();
  }

  async function reverse(
    payment: PayrollPayment,
    body: PayrollPaymentReversalCreate,
    key: string,
  ) {
    try {
      await reversePayrollPayment(payment.id, body, key);
    } catch (caught) {
      if (!isCode(caught, 'reauth_required')) await refreshAfter(caught);
      throw caught;
    }
    void refresh();
  }

  function closed(
    outcome: PaymentDialogOutcome,
    failed: AttemptFailure,
    words: { saved: string; unknown: string },
  ) {
    setDialog(null);
    if (outcome === 'saved') {
      setAnnouncement(words.saved);
    } else if (failed === 'unknown') {
      // A lost answer may have been stored: the refetch shows it, or says the
      // rows may be out of date. Nothing here claims they reloaded.
      setAnnouncement(words.unknown);
    }
    if (failed !== 'none') void refresh();
  }

  const describePayment = (paymentId: string): string | null => {
    const found = payments.data?.find((item) => item.id === paymentId);
    return found ? summary(found) : null;
  };

  return (
    <>
      <PeriodPaymentsView
        period={period}
        payments={state}
        can={can}
        busy={locked || payments.isFetching || dialog !== null}
        announcement={announcement}
        onRecord={() => {
          setAnnouncement('');
          setDialog({ kind: 'record' });
        }}
        onReverse={(payment) => {
          setAnnouncement('');
          setDialog({ kind: 'reverse', payment });
        }}
      />
      {can.record || dialog?.kind === 'record' ? (
        <RecordPayrollPaymentDialog
          open={dialog?.kind === 'record'}
          tutorName={period.tutor_display_name}
          startsOn={period.starts_on}
          endsOn={period.ends_on}
          today={today}
          remaining={remainingOf(period) ?? '0.00'}
          methods={methodOptions}
          onReloadMethods={() => void methods.refetch()}
          describePayment={describePayment}
          onSubmit={record}
          onClose={(outcome, failed) =>
            closed(outcome, failed, {
              saved: t('payroll.payments.saved'),
              unknown: t('payroll.payments.closedUnknown'),
            })
          }
        />
      ) : null}
      {dialog?.kind === 'reverse' ? (
        <ReversePayrollPaymentDialog
          key={dialog.payment.id}
          open
          summary={summary(dialog.payment)}
          onSubmit={(body, key) => reverse(dialog.payment, body, key)}
          onClose={(outcome, failed) =>
            closed(outcome, failed, {
              saved: t('payroll.payments.reversed'),
              unknown: t('payroll.payments.closedUnknownReversal'),
            })
          }
        />
      ) : null}
    </>
  );
}
