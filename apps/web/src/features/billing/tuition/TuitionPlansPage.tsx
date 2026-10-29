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
import { UNBILLED_QUERY_KEY } from '../unbilled/api';
import {
  createPlan,
  fetchPerPeriodPrices,
  fetchPlans,
  fetchStudentOption,
  PER_PERIOD_PRICES_KEY,
  plansListKey,
  studentOptionKey,
  TUITION_QUERY_KEY,
  type PlanListFilters,
  type StudentOption,
} from './api';
import { TuitionPlanDialog } from './TuitionPlanDialog';
import { TuitionPlansView, type TuitionPlansState } from './TuitionPlansView';
import { planCreateBody, planListFilters, planListParams } from './rules';

// /billing/tuition-plans (#374 C), for billing.view holders: the plans
// with the status and student filters and the page in the URL (a deep
// link ?student_id= reads the student's name), and "New plan" for
// billing.manage holders (D: the per-period prices load when it opens; a
// stored plan opens its page). A create invalidates the plan queries and
// the unbilled lists (a plan that starts today or earlier may be charged
// or listed as unbilled).

export function TuitionPlansPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('billing.tuition.title'));
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const timeZone = useCenterTimeZone();
  const today = todayInZone(timeZone);
  const canManage = useCan('billing.manage');
  const canOpenFamilies = useCan('guardian.view');
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => planListFilters(searchParams), [searchParams]);
  const [picked, setPicked] = useState<StudentOption | null>(null);
  const [adding, setAdding] = useState(false);

  const list = useQuery({
    queryKey: plansListKey(filters),
    queryFn: ({ signal }) => fetchPlans(filters, signal),
    placeholderData: keepPreviousData,
  });
  const studentId = filters.studentId;
  const knownStudent =
    picked && studentId && picked.id === studentId ? picked : null;
  const studentRead = useQuery({
    queryKey: studentOptionKey(studentId ?? ''),
    queryFn: ({ signal }) => fetchStudentOption(studentId!, signal),
    enabled: studentId !== null && knownStudent === null,
    retry: false,
  });
  const prices = useQuery({
    queryKey: PER_PERIOD_PRICES_KEY,
    queryFn: ({ signal }) => fetchPerPeriodPrices(signal),
    enabled: adding,
  });

  const params = planListParams(filters).toString();
  const listSearch = params ? `?${params}` : '';

  function apply(next: PlanListFilters) {
    setSearchParams(planListParams(next));
  }

  const state: TuitionPlansState = list.data
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
      <TuitionPlansView
        state={state}
        filters={filters}
        studentName={
          studentId ? ((knownStudent ?? studentRead.data)?.name ?? null) : null
        }
        onStudent={(student) => {
          setPicked(student);
          apply({ ...filters, studentId: student?.id ?? null, page: 1 });
        }}
        onFilters={apply}
        onPage={(page) => apply({ ...filters, page })}
        canManage={canManage}
        onAdd={() => setAdding(true)}
        listSearch={listSearch}
      />
      {canManage ? (
        <TuitionPlanDialog
          open={adding}
          today={today}
          prices={prices.data ?? null}
          pricesError={prices.error ?? undefined}
          onRetryPrices={() => void prices.refetch()}
          canOpenFamilies={canOpenFamilies}
          onSubmit={async (values, price) => {
            const created = await createPlan(
              planCreateBody(values, price.period_frequency),
            );
            await Promise.all([
              queryClient.invalidateQueries({ queryKey: TUITION_QUERY_KEY }),
              queryClient.invalidateQueries({ queryKey: UNBILLED_QUERY_KEY }),
            ]);
            setAdding(false);
            void navigate(`/billing/tuition-plans/${created.id}`, {
              state: { listSearch },
            });
          }}
          onClose={() => setAdding(false)}
        />
      ) : null}
    </>
  );
}
