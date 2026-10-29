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
import { fetchStudentOption, type StudentOption } from '../tuition/api';
import {
  ACTIVE_PRODUCTS_KEY,
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
} from './api';
import { PackagesView, type PackagesState } from './PackagesView';
import { packageListFilters, packageListParams } from './rules';
import { SellPackageDialog } from './SellPackageDialog';

// /billing/packages (#377 C, D), for billing.view holders: the sold
// packages with the status, student and family filters and the page in
// the URL (deep links ?student_id= / ?family_id= read the names), and
// "Sell package" for billing.manage holders (the active products load
// when it opens). A sale sends its Idempotency-Key, invalidates the
// package queries and opens the new package's page, which announces the
// sale in its polite status (the app has no toast; the CourseTemplatePages
// `state: { saved }` pattern).

export function PackagesPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.packages.sold.title'));
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const timeZone = useCenterTimeZone();
  const today = todayInZone(timeZone);
  const canManage = useCan('billing.manage');
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
      />
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
