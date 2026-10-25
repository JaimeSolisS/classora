import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useParams,
} from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { fetchStudent, studentDetailKey } from '@/features/students/api';
import { fullName } from '@/features/students/rules';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { useFamilyOnly } from './access';
import {
  deletePlan,
  fetchPlan,
  invalidatePlanLists,
  myPlanPath,
  planKey,
  setMilestoneStatus,
  studentPlansPath,
  transitionPlan,
  type LearningPlan,
} from './api';
import { LearningPlanView } from './LearningPlanView';

// /learning-plans/:planId (#310 C), for learning_plan.manage holders and
// tutors (the API's record policy decides; an unknown, deleted or hidden
// plan shows the not-found page). Each write stores the returned plan and
// invalidates the student's list; a deleted draft goes (replace) to the
// student's plans with "Draft deleted.". Reload after a refusal refetches,
// then remounts the view, which focuses its heading. Arriving from the
// editor ("Draft saved." / "Plan saved.") focuses that notice. A student or
// guardian goes to the family page of the same plan. Code-split.

const NOTICES: Record<string, string> = {
  draftSaved: 'learningPlans.notices.draftSaved',
  planSaved: 'learningPlans.notices.planSaved',
};

function Shell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold">{t('learningPlans.planTitle')}</h1>
      {children}
    </div>
  );
}

export function LearningPlanPage() {
  const { t } = useTranslation();
  const tAny = t as unknown as (key: string) => string;
  const { planId = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const familyOnly = useFamilyOnly();
  const [generation, setGeneration] = useState(0);
  const [missing, setMissing] = useState(false);
  const key = planKey(planId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchPlan(planId, signal),
    retry: false,
    enabled: !familyOnly,
  });
  const studentId = detail.data?.student_id ?? '';
  const student = useQuery({
    queryKey: studentDetailKey(studentId),
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
    retry: false,
    enabled: studentId !== '' && !familyOnly,
  });
  useDocumentTitle(detail.data?.title ?? t('learningPlans.planTitle'));
  const arrived = (location.state as { notice?: string } | null)?.notice;

  if (familyOnly) {
    return <Navigate replace to={myPlanPath(planId)} />;
  }
  if (
    missing ||
    (detail.error instanceof ApiError && detail.error.status === 404)
  ) {
    return <NotFoundPage />;
  }
  if (detail.isError) {
    return (
      <Shell>
        <ErrorAlert
          error={detail.error}
          onRetry={() => void detail.refetch()}
        />
      </Shell>
    );
  }
  if (!detail.data) {
    return (
      <Shell>
        <LoadingState rows={6} />
      </Shell>
    );
  }

  const current = () => client.getQueryData<LearningPlan>(key) ?? detail.data;

  /** A write: stores the plan, refreshes the lists; a 404 on the plan shows
   * the not-found page. */
  async function write(
    call: () => Promise<LearningPlan>,
    { milestone = false } = {},
  ): Promise<LearningPlan> {
    try {
      const stored = await call();
      client.setQueryData(key, stored);
      void invalidatePlanLists(client);
      return stored;
    } catch (caught) {
      if (!milestone && caught instanceof ApiError && caught.status === 404) {
        setMissing(true);
      }
      throw caught;
    }
  }

  const noticeKey = arrived ? NOTICES[arrived] : undefined;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Link
        to={studentPlansPath(detail.data.student_id)}
        className={`${SMALL_LINK_CLASS} self-start`}
      >
        {t('learningPlans.backToPlans')}
      </Link>
      <LearningPlanView
        key={`${planId}-${generation}`}
        plan={detail.data}
        audience="staff"
        studentName={student.data ? fullName(student.data) : null}
        focusHeadingOnMount={generation > 0}
        initialNotice={noticeKey && generation === 0 ? tAny(noticeKey) : null}
        actions={{
          onTransition: (action) =>
            write(() => transitionPlan(current(), action)),
          onProgress: (milestone, status) =>
            write(() => setMilestoneStatus(milestone, status), {
              milestone: true,
            }),
          onDelete: async () => {
            const plan = current();
            try {
              await deletePlan(plan);
            } catch (caught) {
              if (caught instanceof ApiError && caught.status === 404) {
                setMissing(true);
              }
              throw caught;
            }
            client.removeQueries({ queryKey: key });
            void invalidatePlanLists(client);
            void navigate(studentPlansPath(plan.student_id), {
              replace: true,
              state: { notice: 'draftDeleted' },
            });
          },
          onReload: () => {
            // Remount once the fresh plan is in the cache: the view then
            // focuses its heading on mount (deterministic, #308 QA D4).
            void detail.refetch().then(() => setGeneration((n) => n + 1));
          },
        }}
      />
    </div>
  );
}

export default LearningPlanPage;
