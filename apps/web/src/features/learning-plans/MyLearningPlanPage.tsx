import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import { fetchPlan, myPlansPath, planKey } from './api';
import { LearningPlanView } from './LearningPlanView';

// /my-learning-plans/:planId (#310 E3, E4; the notification target),
// students and guardians: the shared LearningPlanView read-only (the family
// audience renders no action and no progress control, whatever the flags
// say) and "Back to learning plans" with the plan's student selected. A
// 403 student_consent_required shows the consent message; a 404 (a draft, a
// plan discontinued before activation, another family's plan) shows "This
// learning plan isn't available." with a link to /my-learning-plans, not
// the global not-found page.

function Back({ studentId }: { studentId?: string | null }) {
  const { t } = useTranslation();
  return (
    <Link
      to={myPlansPath(studentId)}
      className={`${SMALL_LINK_CLASS} self-start`}
    >
      {t('learningPlans.family.back')}
    </Link>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <Back />
      <h1 className="text-2xl font-semibold">{t('learningPlans.planTitle')}</h1>
      {children}
    </div>
  );
}

export function MyLearningPlanPage() {
  const { t } = useTranslation();
  const { planId = '' } = useParams();
  const detail = useQuery({
    queryKey: planKey(planId),
    queryFn: ({ signal }) => fetchPlan(planId, signal),
    retry: false,
  });
  useDocumentTitle(detail.data?.title ?? t('learningPlans.planTitle'));

  if (
    detail.error instanceof ApiError &&
    detail.error.code === 'student_consent_required'
  ) {
    return (
      <Shell>
        <ConsentRequired name="" title={t('academics.history.consentTitle')} />
      </Shell>
    );
  }
  if (detail.error instanceof ApiError && detail.error.status === 404) {
    return (
      <Shell>
        <p role="status" className="text-sm">
          {t('learningPlans.family.planMissing')}
        </p>
      </Shell>
    );
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
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Back studentId={detail.data.student_id} />
      <LearningPlanView plan={detail.data} audience="family" />
    </div>
  );
}

export default MyLearningPlanPage;
