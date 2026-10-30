import {
  keepPreviousData,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import { useCan } from '@/features/auth/useSession';
import { todayInZone } from '@/lib/time';
import { accountKey, chargeKey, fetchCharge } from '../accounts/api';
import { fetchStudentOption, type StudentOption } from '../tuition/api';
import {
  ACTIVE_PRODUCTS_KEY,
  cancelPackage,
  familyOptionKey,
  fetchActiveProducts,
  fetchFamilyOption,
  fetchPackages,
  PACKAGES_QUERY_KEY,
  packagesListKey,
  purchasePackage,
  studentOptionKey,
  type NamedOption,
  type PackageListFilters,
  type SoldPackage,
} from './api';
import {
  CancelPackageDialog,
  type PurchaseChargeState,
} from './CancelPackageDialog';
import { usePackageLabels } from './labels';
import { PackagesView, type PackagesState } from './PackagesView';
import { packageListFilters, packageListParams } from './rules';
import { SellPackageDialog } from './SellPackageDialog';
import { WithdrawalFlowDialog } from '../withdrawals/WithdrawalDialog';

// /billing/packages (#377 C, D), for billing.view holders: the sold
// packages with the status, student and family filters and the page in
// the URL (deep links ?student_id= / ?family_id= read the names), and
// "Sell package" for billing.manage holders (the active products load
// when it opens). A sale sends its Idempotency-Key, invalidates the
// package queries and opens the new package's page, which announces the
// sale in its polite status (the app has no toast; the CourseTemplatePages
// `state: { saved }` pattern). #386 C1: billing.manage holders cancel an
// active package (its purchase charge's net loads when the dialog opens,
// for the estimate); after the 201 the dialog closes, the page's polite
// status says what was credited, and the package and family account
// queries are invalidated. #387 B3: billing.manage or finance.refund.approve
// holders withdraw an active package's unused units ("Withdraw (unused
// units)": the settlement panel); after the 201 the page's polite status
// says what was credited and refunded, and the package queries are
// invalidated (the panel invalidates the withdrawals and the account).

export function PackagesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.packages.sold.title'));
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const timeZone = useCenterTimeZone();
  const today = todayInZone(timeZone);
  const canManage = useCan('billing.manage');
  const canApproveRefunds = useCan('finance.refund.approve');
  const canWithdraw = canManage || canApproveRefunds;
  const canOpenStudents = useCan('student.view');
  const canFilterFamilies = useCan('guardian.view');
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(
    () => packageListFilters(searchParams),
    [searchParams],
  );
  const [pickedStudent, setPickedStudent] = useState<StudentOption | null>(
    null,
  );
  const [pickedFamily, setPickedFamily] = useState<NamedOption | null>(null);
  const [selling, setSelling] = useState(false);
  const [cancelling, setCancelling] = useState<SoldPackage | null>(null);
  const [withdrawing, setWithdrawing] = useState<SoldPackage | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const labels = usePackageLabels();

  const list = useQuery({
    queryKey: packagesListKey(filters),
    queryFn: ({ signal }) => fetchPackages(filters, signal),
    placeholderData: keepPreviousData,
  });
  const { studentId, familyId } = filters;
  const knownStudent =
    pickedStudent && pickedStudent.id === studentId ? pickedStudent : null;
  const knownFamily =
    pickedFamily && pickedFamily.id === familyId ? pickedFamily : null;
  const studentRead = useQuery({
    queryKey: studentOptionKey(studentId ?? ''),
    queryFn: ({ signal }) => fetchStudentOption(studentId!, signal),
    enabled: studentId !== null && knownStudent === null,
    retry: false,
  });
  const familyRead = useQuery({
    queryKey: familyOptionKey(familyId ?? ''),
    queryFn: ({ signal }) => fetchFamilyOption(familyId!, signal),
    enabled: familyId !== null && knownFamily === null && canFilterFamilies,
    retry: false,
  });
  const products = useQuery({
    queryKey: ACTIVE_PRODUCTS_KEY,
    queryFn: ({ signal }) => fetchActiveProducts(signal),
    enabled: selling,
  });

  const cancelChargeId = cancelling?.purchase_charge_id ?? null;
  const cancelFamilyId = cancelling?.family.id ?? '';
  const purchaseCharge = useQuery({
    queryKey: chargeKey(cancelFamilyId, cancelChargeId ?? ''),
    queryFn: ({ signal }) =>
      fetchCharge(cancelFamilyId, cancelChargeId!, signal),
    enabled: cancelChargeId !== null,
    staleTime: 0,
    retry: false,
  });
  const chargeState: PurchaseChargeState =
    cancelling !== null && cancelChargeId === null
      ? { status: 'ready', net: null }
      : purchaseCharge.data
        ? { status: 'ready', net: purchaseCharge.data.net }
        : purchaseCharge.isError
          ? {
              status: 'error',
              error: purchaseCharge.error,
              onRetry: () => void purchaseCharge.refetch(),
            }
          : { status: 'loading' };

  const params = packageListParams(filters).toString();
  const listSearch = params ? `?${params}` : '';

  function apply(next: PackageListFilters) {
    setSearchParams(packageListParams(next));
  }

  const state: PackagesState = list.data
    ? { status: 'ready', page: list.data }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <>
      <PackagesView
        state={state}
        filters={filters}
        studentName={
          studentId ? ((knownStudent ?? studentRead.data)?.name ?? null) : null
        }
        familyName={
          familyId ? ((knownFamily ?? familyRead.data)?.name ?? null) : null
        }
        onStudent={(student) => {
          setPickedStudent(student);
          apply({ ...filters, studentId: student?.id ?? null, page: 1 });
        }}
        onFamily={(family) => {
          setPickedFamily(family);
          apply({ ...filters, familyId: family?.id ?? null, page: 1 });
        }}
        onFilters={apply}
        onPage={(page) => apply({ ...filters, page })}
        canManage={canManage}
        canFilterFamilies={canFilterFamilies}
        onSell={() => setSelling(true)}
        listSearch={listSearch}
        onCancel={(pkg) => {
          setAnnouncement('');
          setCancelling(pkg);
        }}
        canWithdraw={canWithdraw}
        onWithdraw={(pkg) => {
          setAnnouncement('');
          setWithdrawing(pkg);
        }}
        announcement={announcement}
      />
      {canManage ? (
        <CancelPackageDialog
          open={cancelling !== null}
          pkg={cancelling}
          charge={chargeState}
          onSubmit={async (body, key) => {
            const pkg = cancelling!;
            const cancelled = await cancelPackage(pkg.id, body, key);
            setCancelling(null);
            setAnnouncement(
              t('billing.packages.cancel.done', {
                name: pkg.product.name,
                student: pkg.student.name,
                credit: labels.money(cancelled.credit),
                after: labels.money(cancelled.account_credit_after),
              }),
            );
            void queryClient.invalidateQueries({
              queryKey: PACKAGES_QUERY_KEY,
            });
            void queryClient.invalidateQueries({
              queryKey: accountKey(pkg.family.id),
            });
          }}
          onStale={() =>
            void queryClient.invalidateQueries({
              queryKey: PACKAGES_QUERY_KEY,
            })
          }
          onClose={() => setCancelling(null)}
        />
      ) : null}
      {canWithdraw ? (
        <WithdrawalFlowDialog
          subject={withdrawing ? { kind: 'package', id: withdrawing.id } : null}
          title={t('billing.withdrawals.package.title')}
          subjectName={withdrawing?.product.name ?? ''}
          onDone={(confirmed) => {
            const pkg = withdrawing!;
            setWithdrawing(null);
            setAnnouncement(
              t('billing.withdrawals.done', {
                student: pkg.student.name,
                subject: pkg.product.name,
                credit: labels.money(confirmed.withdrawal.amount),
                refund: labels.money(confirmed.withdrawal.refund_amount),
                after: labels.money(confirmed.account_credit_after),
              }),
            );
            void queryClient.invalidateQueries({
              queryKey: PACKAGES_QUERY_KEY,
            });
          }}
          onClose={() => setWithdrawing(null)}
        />
      ) : null}
      {canManage ? (
        <SellPackageDialog
          open={selling}
          today={today}
          products={products.data ?? null}
          productsError={products.error ?? undefined}
          onRetryProducts={() => void products.refetch()}
          canOpenStudents={canOpenStudents}
          onSubmit={async (body, key) => {
            const sold = await purchasePackage(body, key);
            await queryClient.invalidateQueries({
              queryKey: PACKAGES_QUERY_KEY,
            });
            setSelling(false);
            void navigate(`/billing/packages/${sold.id}`, {
              state: { listSearch, sold: true },
            });
          }}
          onClose={() => setSelling(false)}
        />
      ) : null}
    </>
  );
}
