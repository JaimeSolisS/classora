import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import {
  fetchFamilyCharge,
  fetchFamilyCharges,
  fetchFamilyChargesByIds,
  fetchFamilyPackage,
  fetchFamilyPackages,
  fetchFamilyPayment,
  fetchFamilyPayments,
  fetchLedger,
  fetchOwnFamily,
  fetchSummary,
  OWN_FAMILY_KEY,
  portalKey,
  portalKeys,
} from './api';
import { ChargeDetailDialog } from './ChargeDetailDialog';
import {
  FamilyBillingEmpty,
  FamilyBillingView,
  type LedgerState,
  type PageState,
} from './FamilyBillingView';
import { PackageUsageDialog } from './PackageUsageDialog';
import { PaymentDetailDialog } from './PaymentDetailDialog';
import type { DetailState } from './PortalDialog';
import {
  filterStatuses,
  portalView,
  portalViewParams,
  selectedStudent,
  type ChargeFilter,
} from './rules';

// /my-billing (#388), guardians only (the route handle; the API decides):
// the caller's own family first (`GET /families/own`, T2; none → the A3
// message and no other request), then its summary; once the summary is in,
// the child filter is checked against its students (an unknown or foreign
// `?student=` is "All children" and never sent, B1) and Charges, Payments,
// Packages and History load. The child filter and the charge filter are in
// the URL (`?student=`, `?status=`, the reminder link's target, H1); the
// pages of each list are local and go back to 1 when a filter changes.
// A4: a 404 on any read (the guardian was removed from the family) drops
// that family's cached reads and asks for the own family once more, then
// shows what that answer says; a second 404 for the same family shows the
// error, never a loop. Read-only: nothing here writes. Code-split.

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

function pageState<T>(query: {
  data: T | undefined;
  error: unknown;
  isError: boolean;
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

function detailState<T>(query: {
  data: T | undefined;
  error: unknown;
  isError: boolean;
  refetch: () => unknown;
}): DetailState<T> {
  const state = pageState(query);
  return state.status === 'ready'
    ? { status: 'ready', value: state.page }
    : state;
}

/** A page number that goes back to 1 whenever `key` changes. */
function useKeyedPage(key: string): [number, (page: number) => void] {
  const [state, setState] = useState({ key, page: 1 });
  const page = state.key === key ? state.page : 1;
  return [page, (next: number) => setState({ key, page: next })];
}

function Shell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="flex w-full max-w-6xl min-w-0 flex-col gap-4">
      <h1 className="text-2xl font-semibold">
        {t('familyBilling.fallbackTitle')}
      </h1>
      {children}
    </div>
  );
}

export function FamilyBillingPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const view = useMemo(() => portalView(params), [params]);

  const own = useQuery({
    queryKey: OWN_FAMILY_KEY,
    queryFn: ({ signal }) => fetchOwnFamily(signal),
    retry: false,
  });
  const family = own.data?.family ?? null;
  const familyId = family?.id ?? '';
  // A4: the family a 404 came from, until /families/own answers again.
  const [gone, setGone] = useState<{ familyId: string; at: number } | null>(
    null,
  );
  const refetchedFor = useRef(new Set<string>());
  const paused =
    gone !== null && gone.familyId === familyId && own.dataUpdatedAt <= gone.at;
  const enabled = family !== null && !paused;

  const summary = useQuery({
    queryKey: portalKeys.summary(familyId),
    queryFn: ({ signal }) => fetchSummary(familyId, signal),
    enabled,
    retry: false,
  });
  const ready = enabled && summary.data !== undefined;
  const studentId = summary.data
    ? selectedStudent(summary.data, view.student)
    : null;

  const filterKey = `${studentId ?? ''}|${view.status}`;
  const [chargesPage, setChargesPage] = useKeyedPage(filterKey);
  const [packagesPage, setPackagesPage] = useKeyedPage(studentId ?? '');
  const [paymentsPage, setPaymentsPage] = useState(1);
  const [chargeId, setChargeId] = useState<string | null>(null);
  const [paymentId, setPaymentId] = useState<string | null>(null);
  const [packageId, setPackageId] = useState<string | null>(null);

  const chargeFilters = {
    studentId,
    statuses: filterStatuses(view.status),
    page: chargesPage,
  };
  const charges = useQuery({
    queryKey: portalKeys.charges(familyId, chargeFilters),
    queryFn: ({ signal }) =>
      fetchFamilyCharges(familyId, chargeFilters, signal),
    placeholderData: keepPreviousData,
    enabled: ready,
    retry: false,
  });
  const payments = useQuery({
    queryKey: portalKeys.payments(familyId, paymentsPage),
    queryFn: ({ signal }) =>
      fetchFamilyPayments(familyId, paymentsPage, signal),
    placeholderData: keepPreviousData,
    enabled: ready,
    retry: false,
  });
  const packages = useQuery({
    queryKey: portalKeys.packages(familyId, studentId, packagesPage),
    queryFn: ({ signal }) =>
      fetchFamilyPackages(familyId, studentId, packagesPage, signal),
    placeholderData: keepPreviousData,
    enabled: ready,
    retry: false,
  });
  const ledger = useInfiniteQuery({
    queryKey: portalKeys.ledger(familyId, studentId),
    queryFn: ({ pageParam, signal }) =>
      fetchLedger(familyId, studentId, pageParam, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor,
    enabled: ready,
    retry: false,
  });
  const chargeDetail = useQuery({
    queryKey: portalKeys.charge(familyId, chargeId ?? ''),
    queryFn: ({ signal }) => fetchFamilyCharge(familyId, chargeId!, signal),
    enabled: enabled && chargeId !== null,
    retry: false,
  });
  const payment = useQuery({
    queryKey: portalKeys.payment(familyId, paymentId ?? ''),
    queryFn: ({ signal }) => fetchFamilyPayment(familyId, paymentId!, signal),
    enabled: enabled && paymentId !== null,
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
    queryKey: portalKeys.paymentCharges(familyId, allocatedIds),
    queryFn: ({ signal }) =>
      fetchFamilyChargesByIds(familyId, allocatedIds, signal),
    enabled: enabled && paymentId !== null && payment.data !== undefined,
    retry: false,
  });
  const packageDetail = useQuery({
    queryKey: portalKeys.package(familyId, packageId ?? ''),
    queryFn: ({ signal }) => fetchFamilyPackage(familyId, packageId!, signal),
    enabled: enabled && packageId !== null,
    retry: false,
  });

  // A4: removed from the family meanwhile.
  const notFound = [
    summary.error,
    charges.error,
    payments.error,
    packages.error,
    ledger.error,
    chargeDetail.error,
    payment.error,
    allocatedCharges.error,
    packageDetail.error,
  ].some(isNotFound);
  const refetchOwn = own.refetch;
  const ownUpdatedAt = own.dataUpdatedAt;
  useEffect(() => {
    if (!notFound || familyId === '' || refetchedFor.current.has(familyId)) {
      return;
    }
    refetchedFor.current.add(familyId);
    setGone({ familyId, at: ownUpdatedAt });
    setChargeId(null);
    setPaymentId(null);
    setPackageId(null);
    queryClient.removeQueries({ queryKey: portalKey(familyId) });
    void refetchOwn();
  }, [notFound, familyId, ownUpdatedAt, queryClient, refetchOwn]);

  // G1: a stale cursor (`400 invalid_cursor`) starts the history again.
  const ledgerError = ledger.error;
  useEffect(() => {
    if (
      ledgerError instanceof ApiError &&
      ledgerError.code === 'invalid_cursor'
    ) {
      void queryClient.resetQueries({
        queryKey: portalKeys.ledger(familyId, studentId),
      });
    }
  }, [ledgerError, queryClient, familyId, studentId]);

  useDocumentTitle(
    family && summary.data
      ? t('familyBilling.title', { name: summary.data.family.name })
      : t('familyBilling.fallbackTitle'),
  );

  if (own.isError) {
    return (
      <Shell>
        <ErrorAlert error={own.error} onRetry={() => void own.refetch()} />
      </Shell>
    );
  }
  if (own.data === undefined) {
    return (
      <Shell>
        <LoadingState rows={4} />
      </Shell>
    );
  }
  if (family === null) {
    return <FamilyBillingEmpty />;
  }
  if (summary.isError && !paused) {
    return (
      <Shell>
        <ErrorAlert
          error={summary.error}
          onRetry={() => void summary.refetch()}
        />
      </Shell>
    );
  }
  if (!summary.data || paused) {
    return (
      <Shell>
        <LoadingState rows={4} />
      </Shell>
    );
  }

  const setView = (student: string | null, status: ChargeFilter) =>
    setParams(portalViewParams({ student, status }));
  const studentNames = Object.fromEntries(
    summary.data.students.map((item) => [item.student.id, item.student.name]),
  );

  let ledgerState: LedgerState;
  if (ledger.data) {
    ledgerState = {
      status: 'ready',
      items: ledger.data.pages.flatMap((page) => page.items),
      hasMore: ledger.hasNextPage,
      loadingMore: ledger.isFetchingNextPage,
      onLoadMore: () => void ledger.fetchNextPage(),
    };
  } else if (ledger.isError) {
    ledgerState = {
      status: 'error',
      error: ledger.error,
      onRetry: () => void ledger.refetch(),
    };
  } else {
    ledgerState = { status: 'loading' };
  }

  const paymentState: DetailState<{
    payment: NonNullable<typeof payment.data>;
    charges: NonNullable<typeof allocatedCharges.data> | null;
  }> = payment.data
    ? {
        status: 'ready',
        value: {
          payment: payment.data,
          charges: allocatedCharges.data ?? null,
        },
      }
    : payment.isError
      ? {
          status: 'error',
          error: payment.error,
          onRetry: () => void payment.refetch(),
        }
      : { status: 'loading' };

  return (
    <>
      <FamilyBillingView
        summary={summary.data}
        studentId={studentId}
        onStudent={(student) => setView(student, view.status)}
        filter={view.status}
        onFilter={(status) => setView(studentId, status)}
        charges={pageState(charges)}
        onChargesPage={setChargesPage}
        payments={pageState(payments)}
        onPaymentsPage={setPaymentsPage}
        packages={pageState(packages)}
        onPackagesPage={setPackagesPage}
        ledger={ledgerState}
        onOpenCharge={setChargeId}
        onOpenPayment={setPaymentId}
        onOpenPackage={setPackageId}
      />
      <ChargeDetailDialog
        open={chargeId !== null}
        state={detailState(chargeDetail)}
        onClose={() => setChargeId(null)}
      />
      <PaymentDetailDialog
        open={paymentId !== null}
        familyId={familyId}
        state={paymentState}
        studentNames={studentNames}
        onClose={() => setPaymentId(null)}
      />
      <PackageUsageDialog
        open={packageId !== null}
        state={detailState(packageDetail)}
        today={summary.data.today}
        onClose={() => setPackageId(null)}
      />
    </>
  );
}
