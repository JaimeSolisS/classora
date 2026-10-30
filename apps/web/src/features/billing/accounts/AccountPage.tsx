import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams, useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { CATALOG_LINK_CLASS } from '@/features/catalog/LevelsTable';
import { todayInZone } from '@/lib/time';
import { BillingTabs } from '../BillingTabs';
import {
  dueDateChangesKey,
  fetchDueDateChanges,
  recordDueDateChange,
  updateDueOffset,
} from '../due-dates/api';
import { DueDateDialog, type HistoryState } from '../due-dates/DueDateDialog';
import { DueOffsetDialog } from '../due-dates/DueOffsetDialog';
import {
  AccountPageView,
  type LedgerState,
  type PageState,
} from './AccountPageView';
import { AdjustChargeDialog } from './AdjustChargeDialog';
import { AllocateCreditDialog } from './AllocateCreditDialog';
import {
  accountKey,
  allocateCredit,
  chargeKey,
  chargesKey,
  fetchCharge,
  fetchCharges,
  fetchChargesByIds,
  fetchLedger,
  fetchOpenCharges,
  fetchPayment,
  fetchPaymentMethods,
  fetchPayments,
  fetchSummary,
  ledgerKey,
  openChargesKey,
  PAYMENT_METHODS_KEY,
  paymentKey,
  paymentsKey,
  postAdjustment,
  postManualCharge,
  recordPayment,
  refundPayment,
  reversePayment,
  summaryKey,
  type ChargeDetail,
  type ChargeItem,
  type ChargePage,
  type PaymentDetail,
  type PaymentPage,
  type ReceiptReference,
} from './api';
import { apiCents, centsText, type Cents } from './cents';
import {
  ChargeDetailDialog,
  type ChargeDetailState,
} from './ChargeDetailDialog';
import { useAccountLabels } from './labels';
import { ManualChargeDialog } from './ManualChargeDialog';
import {
  PaymentDetailDialog,
  type PaymentDetailState,
} from './PaymentDetailDialog';
import { RecordPaymentDialog } from './RecordPaymentDialog';
import { RefundDialog } from './RefundDialog';
import { ReversePaymentDialog } from './ReversePaymentDialog';
import { accountView, accountViewParams, oldestFirst } from './rules';

// /billing/accounts/:familyId (#379 C, D, E), for billing.view holders: the
// family's summary, charges, history and payments (the student filter, the
// charge statuses and the pages in the URL), and for payment.record
// holders "Record payment" and, while the account has credit, "Apply
// account credit". The dialogs load the open charges (and the methods)
// when they open. Nothing on the page changes before the server confirms
// (#96 AC 4): after a 201 the dialog closes, the polite status reads what
// was allocated and the account credit now, and every query of the family
// is invalidated. An unknown family (404, or a malformed id: 422) is the
// not-found state. #382: billing.manage holders edit the family's due-date
// offset (sending the summary's version) and change an open charge's due
// date (its history loads when the dialog opens); after the server answers,
// the dialog closes, the polite status says what changed and the family's
// queries are invalidated. Never optimistic (spec §6, ADR-0006 E11).
// #386: "Add charge" (billing.manage), a charge's detail with "Adjust"
// (billing.manage; the password dialog comes from apiCall), and on the
// payment detail "Reverse payment" and "Refund" (finance.refund.approve).
// Each posts with the dialog's Idempotency-Key; after the 201 the action
// dialog closes, the page's status (and the open detail's own status) say
// what changed, and every query of the family is invalidated (summary,
// charges, the charge and payment details, the ledger, the payments).
// #98 H1: each payment row shows its receipt number and a download link,
// and after recording, the new receipt's number and link follow the polite
// status.

function NotFoundOrError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const notFound =
    error instanceof ApiError && (error.status === 404 || error.status === 422);
  return (
    <div className="flex flex-col gap-8">
      <BillingTabs />
      <div className="flex flex-col gap-4">
        <h1 className="text-2xl font-semibold">
          {notFound
            ? t('billing.accounts.page.notFound')
            : t('billing.accounts.page.fallbackTitle')}
        </h1>
        {notFound ? (
          <p>{t('billing.accounts.page.notFoundHelp')}</p>
        ) : (
          <ErrorAlert error={error} onRetry={onRetry} />
        )}
        <p>
          <Link to="/billing/accounts" className={CATALOG_LINK_CLASS}>
            {t('billing.accounts.back')}
          </Link>
        </p>
      </div>
    </div>
  );
}

export function AccountPage() {
  const { t } = useTranslation();
  const { familyId = '' } = useParams();
  const queryClient = useQueryClient();
  const labels = useAccountLabels();
  const timeZone = useCenterTimeZone();
  const today = todayInZone(timeZone);
  const canRecord = useCan('payment.record');
  const canManage = useCan('billing.manage');
  const canApproveRefunds = useCan('finance.refund.approve');
  const [searchParams, setSearchParams] = useSearchParams();
  const view = useMemo(() => accountView(searchParams), [searchParams]);
  const [recording, setRecording] = useState(false);
  const [crediting, setCrediting] = useState(false);
  const [paymentId, setPaymentId] = useState<string | null>(null);
  const [editingOffset, setEditingOffset] = useState(false);
  const [dueCharge, setDueCharge] = useState<ChargeItem | null>(null);
  const [addingCharge, setAddingCharge] = useState(false);
  const [chargeId, setChargeId] = useState<string | null>(null);
  // The ids only: the open action dialog reads the charge or payment from
  // its query, so a refetch (after a 409) shows the current amounts.
  const [adjustingId, setAdjustingId] = useState<string | null>(null);
  const [reversingId, setReversingId] = useState<string | null>(null);
  const [refundingId, setRefundingId] = useState<string | null>(null);
  // What an action changed, read inside the open detail (#386).
  const [chargeNotice, setChargeNotice] = useState('');
  const [paymentNotice, setPaymentNotice] = useState('');
  const [announcement, setAnnouncementText] = useState('');
  // The receipt of the payment just recorded (#98 H1); any other
  // announcement (or a new action) replaces it.
  const [recordedReceipt, setRecordedReceipt] =
    useState<ReceiptReference | null>(null);
  const setAnnouncement = (
    text: string,
    receipt: ReceiptReference | null = null,
  ) => {
    setAnnouncementText(text);
    setRecordedReceipt(receipt);
  };

  const summary = useQuery({
    queryKey: summaryKey(familyId),
    queryFn: ({ signal }) => fetchSummary(familyId, signal),
    retry: false,
  });
  const ready = summary.data !== undefined;
  const chargeFilters = {
    studentId: view.studentId,
    statuses: view.statuses,
    page: view.page,
  };
  const charges = useQuery({
    queryKey: chargesKey(familyId, chargeFilters),
    queryFn: ({ signal }) => fetchCharges(familyId, chargeFilters, signal),
    placeholderData: keepPreviousData,
    enabled: ready,
  });
  const ledger = useInfiniteQuery({
    queryKey: ledgerKey(familyId, view.studentId),
    queryFn: ({ pageParam, signal }) =>
      fetchLedger(familyId, view.studentId, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled: ready,
    retry: false,
  });
  const payments = useQuery({
    queryKey: paymentsKey(familyId, view.paymentsPage),
    queryFn: ({ signal }) => fetchPayments(familyId, view.paymentsPage, signal),
    placeholderData: keepPreviousData,
    enabled: ready,
  });
  const openCharges = useQuery({
    queryKey: openChargesKey(familyId),
    queryFn: ({ signal }) => fetchOpenCharges(familyId, signal),
    enabled: recording || crediting,
    staleTime: 0,
  });
  const sortedOpen = useMemo(
    () => (openCharges.data ? oldestFirst(openCharges.data) : null),
    [openCharges.data],
  );
  const methods = useQuery({
    queryKey: PAYMENT_METHODS_KEY,
    queryFn: ({ signal }) => fetchPaymentMethods(signal),
    enabled: recording || refundingId !== null,
  });
  const payment = useQuery({
    queryKey: paymentKey(familyId, paymentId ?? ''),
    queryFn: ({ signal }) => fetchPayment(familyId, paymentId!, signal),
    enabled: paymentId !== null,
  });
  const chargeDetail = useQuery({
    queryKey: chargeKey(familyId, chargeId ?? ''),
    queryFn: ({ signal }) => fetchCharge(familyId, chargeId!, signal),
    enabled: chargeId !== null,
    retry: false,
  });
  const dueDateChanges = useQuery({
    queryKey: dueDateChangesKey(familyId, dueCharge?.id ?? ''),
    queryFn: ({ signal }) =>
      fetchDueDateChanges(familyId, dueCharge!.id, signal),
    enabled: dueCharge !== null,
    staleTime: 0,
    retry: false,
  });
  const allocatedIds = useMemo(
    () =>
      [
        ...new Set(
          (payment.data?.allocations ?? []).map((item) => item.charge_id),
        ),
      ].sort(),
    [payment.data],
  );
  const allocatedCharges = useQuery({
    queryKey: [...accountKey(familyId), 'charges-by-id', allocatedIds],
    queryFn: ({ signal }) => fetchChargesByIds(familyId, allocatedIds, signal),
    enabled: paymentId !== null && payment.data !== undefined,
    retry: false,
  });

  // C3: a stale cursor (`400 invalid_cursor`) starts the history again.
  const ledgerError = ledger.error;
  useEffect(() => {
    if (
      ledgerError instanceof ApiError &&
      ledgerError.code === 'invalid_cursor'
    ) {
      void queryClient.resetQueries({
        queryKey: ledgerKey(familyId, view.studentId),
      });
    }
  }, [ledgerError, queryClient, familyId, view.studentId]);

  useDocumentTitle(
    summary.data
      ? t('billing.accounts.page.title', { name: summary.data.family.name })
      : summary.isError
        ? t('billing.accounts.page.notFound')
        : t('billing.accounts.page.fallbackTitle'),
  );

  if (summary.isError) {
    return (
      <NotFoundOrError
        error={summary.error}
        onRetry={() => void summary.refetch()}
      />
    );
  }
  if (!summary.data) {
    return (
      <div className="flex flex-col gap-8">
        <BillingTabs />
        <LoadingState />
      </div>
    );
  }

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: accountKey(familyId) });
  }

  function pageState<T>(query: {
    data: T | undefined;
    isError: boolean;
    error: unknown;
    refetch: () => unknown;
  }): PageState<T> {
    if (query.data !== undefined) return { status: 'ready', page: query.data };
    if (query.isError) {
      return {
        status: 'error',
        error: query.error,
        onRetry: () => void query.refetch(),
      };
    }
    return { status: 'loading' };
  }

  const ledgerState: LedgerState = ledger.data
    ? {
        status: 'ready',
        items: ledger.data.pages.flatMap((page) => page.items),
        hasMore: ledger.hasNextPage,
        loadingMore: ledger.isFetchingNextPage,
        onLoadMore: () => void ledger.fetchNextPage(),
      }
    : ledger.isError
      ? {
          status: 'error',
          error: ledger.error,
          onRetry: () => void ledger.refetch(),
        }
      : { status: 'loading' };

  const paymentState: PaymentDetailState = payment.data
    ? {
        status: 'ready',
        payment: payment.data,
        charges:
          allocatedCharges.data ?? (allocatedCharges.isError ? {} : null),
      }
    : payment.isError
      ? {
          status: 'error',
          error: payment.error,
          onRetry: () => void payment.refetch(),
        }
      : { status: 'loading' };

  const adjusting: ChargeDetail | null =
    adjustingId !== null && chargeDetail.data?.id === adjustingId
      ? chargeDetail.data
      : null;
  const paymentFor = (id: string | null): PaymentDetail | null =>
    id !== null && payment.data?.id === id ? payment.data : null;
  const reversing = paymentFor(reversingId);
  const refunding = paymentFor(refundingId);

  const chargeState: ChargeDetailState = chargeDetail.data
    ? { status: 'ready', charge: chargeDetail.data }
    : chargeDetail.isError
      ? {
          status: 'error',
          error: chargeDetail.error,
          onRetry: () => void chargeDetail.refetch(),
        }
      : { status: 'loading' };

  const historyState: HistoryState = dueDateChanges.data
    ? { status: 'ready', items: dueDateChanges.data }
    : dueDateChanges.isError
      ? {
          status: 'error',
          error: dueDateChanges.error,
          onRetry: () => void dueDateChanges.refetch(),
        }
      : { status: 'loading' };

  const studentNames = Object.fromEntries(
    summary.data.students.map((item) => [item.student.id, item.student.name]),
  );

  return (
    <>
      <AccountPageView
        summary={summary.data}
        view={view}
        onView={(next) => setSearchParams(accountViewParams(next))}
        charges={pageState<ChargePage>(charges)}
        ledger={ledgerState}
        payments={pageState<PaymentPage>(payments)}
        canRecord={canRecord}
        onRecord={() => {
          setAnnouncement('');
          setRecording(true);
        }}
        onApplyCredit={() => {
          setAnnouncement('');
          setCrediting(true);
        }}
        onOpenPayment={(id) => {
          setPaymentNotice('');
          setPaymentId(id);
        }}
        onOpenCharge={(charge) => {
          setChargeNotice('');
          setChargeId(charge.id);
        }}
        onAddCharge={() => {
          setAnnouncement('');
          setAddingCharge(true);
        }}
        canManage={canManage}
        onEditOffset={() => {
          setAnnouncement('');
          setEditingOffset(true);
        }}
        onChangeDueDate={(charge) => {
          setAnnouncement('');
          setDueCharge(charge);
        }}
        announcement={announcement}
        recordedReceipt={recordedReceipt}
      />
      {canManage ? (
        <>
          <DueOffsetDialog
            open={editingOffset}
            current={summary.data.due_offset_days}
            onSubmit={async (days) => {
              const saved = await updateDueOffset(familyId, {
                version: summary.data.version,
                due_offset_days: days,
              });
              setEditingOffset(false);
              setAnnouncement(
                t('billing.dueDates.offset.done', {
                  count: saved.due_offset_days,
                }),
              );
              void queryClient.invalidateQueries({
                queryKey: summaryKey(familyId),
              });
            }}
            onConflict={() => void summary.refetch()}
            onClose={() => setEditingOffset(false)}
          />
          <ManualChargeDialog
            open={addingCharge}
            today={today}
            students={summary.data.students.map((item) => item.student)}
            onSubmit={async (body, key) => {
              const posted = await postManualCharge(familyId, body, key);
              setAddingCharge(false);
              setAnnouncement(
                t('billing.accounts.manualCharge.done', {
                  amount: labels.money(posted.charge.amount),
                  student: posted.charge.student.name,
                }),
              );
              refresh();
            }}
            onClose={() => setAddingCharge(false)}
          />
          <AdjustChargeDialog
            open={adjustingId !== null}
            charge={adjusting}
            onSubmit={async (body, key) => {
              const posted = await postAdjustment(
                familyId,
                adjustingId!,
                body,
                key,
              );
              setAdjustingId(null);
              const done = t('billing.accounts.adjust.done', {
                amount: labels.signed(posted.adjustment.amount),
                net: labels.money(posted.charge.net),
              });
              setChargeNotice(done);
              setAnnouncement(done);
              refresh();
            }}
            onStale={refresh}
            onClose={() => setAdjustingId(null)}
          />
          <DueDateDialog
            open={dueCharge !== null}
            charge={dueCharge}
            today={summary.data.today}
            history={historyState}
            onSubmit={async (body) => {
              const changed = await recordDueDateChange(
                familyId,
                dueCharge!.id,
                body,
              );
              setDueCharge(null);
              setAnnouncement(
                t('billing.dueDates.change.done', {
                  date: labels.date(changed.due_date),
                  status: t(`billing.tuition.chargeStatus.${changed.status}`),
                }),
              );
              refresh();
            }}
            onStale={() =>
              void queryClient.invalidateQueries({
                queryKey: [...accountKey(familyId), 'charges'],
              })
            }
            onClose={() => setDueCharge(null)}
          />
        </>
      ) : null}
      {canRecord ? (
        <>
          <RecordPaymentDialog
            open={recording}
            today={today}
            methods={methods.data ?? null}
            methodsError={methods.error ?? undefined}
            onRetryMethods={() => void methods.refetch()}
            charges={sortedOpen}
            chargesError={openCharges.error ?? undefined}
            onReloadCharges={() => {
              void openCharges.refetch();
              void queryClient.invalidateQueries({
                queryKey: [...accountKey(familyId), 'charges'],
              });
            }}
            onSubmit={async (body, key) => {
              const recorded = await recordPayment(familyId, body, key);
              const allocated = (apiCents(recorded.payment.amount) -
                apiCents(recorded.unallocated_amount)) as Cents;
              setRecording(false);
              setAnnouncement(
                t('billing.accounts.record.done', {
                  amount: labels.money(recorded.payment.amount),
                  allocated: labels.money(centsText(allocated)),
                  credit: labels.money(recorded.account_credit_after),
                }),
                recorded.receipt,
              );
              refresh();
            }}
            onClose={() => setRecording(false)}
          />
          <AllocateCreditDialog
            open={crediting}
            credit={summary.data.account_credit}
            charges={sortedOpen}
            chargesError={openCharges.error ?? undefined}
            onReloadCharges={() => void openCharges.refetch()}
            onStale={() => {
              void openCharges.refetch();
              void summary.refetch();
            }}
            onSubmit={async (body, key) => {
              const allocated = await allocateCredit(familyId, body, key);
              setCrediting(false);
              setAnnouncement(
                t('billing.accounts.credit.done', {
                  allocated: labels.money(allocated.allocated_total),
                  credit: labels.money(allocated.account_credit_after),
                }),
              );
              refresh();
            }}
            onClose={() => setCrediting(false)}
          />
        </>
      ) : null}
      <ChargeDetailDialog
        open={chargeId !== null}
        state={chargeState}
        canManage={canManage}
        onAdjust={(charge) => setAdjustingId(charge.id)}
        onViewPayment={(id) => {
          setChargeId(null);
          setPaymentNotice('');
          setPaymentId(id);
        }}
        notice={chargeNotice}
        onClose={() => setChargeId(null)}
      />
      <PaymentDetailDialog
        open={paymentId !== null}
        state={paymentState}
        studentNames={studentNames}
        onClose={() => setPaymentId(null)}
        canApproveRefunds={canApproveRefunds}
        onReverse={(item) => setReversingId(item.id)}
        onRefund={(item) => setRefundingId(item.id)}
        notice={paymentNotice}
      />
      {canApproveRefunds ? (
        <>
          <ReversePaymentDialog
            open={reversingId !== null}
            payment={reversing}
            onSubmit={async (body, key) => {
              const reversed = await reversePayment(
                familyId,
                reversingId!,
                body,
                key,
              );
              setReversingId(null);
              const done = t('billing.accounts.reverse.done', {
                amount: labels.money(reversed.payment.amount),
                credit: labels.money(reversed.account_credit_after),
              });
              setPaymentNotice(done);
              setAnnouncement(done);
              refresh();
            }}
            onStale={refresh}
            onClose={() => setReversingId(null)}
          />
          <RefundDialog
            open={refundingId !== null}
            today={today}
            payment={refunding}
            methods={methods.data ?? null}
            methodsError={methods.error ?? undefined}
            onRetryMethods={() => void methods.refetch()}
            onSubmit={async (body, key) => {
              const refunded = await refundPayment(
                familyId,
                refundingId!,
                body,
                key,
              );
              setRefundingId(null);
              const done = t('billing.accounts.refund.done', {
                amount: labels.money(refunded.refund.amount),
                credit: labels.money(refunded.account_credit_after),
              });
              setPaymentNotice(done);
              setAnnouncement(done);
              refresh();
            }}
            onStale={refresh}
            onClose={() => setRefundingId(null)}
          />
        </>
      ) : null}
    </>
  );
}
