import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, Navigate, useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { fetchStudent, studentDetailKey } from '@/features/students/api';
import { fullName } from '@/features/students/rules';
import { LINK_BUTTON_CLASS } from '@/features/students/styles';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { useFamilyOnly } from './access';
import {
  fetchPlanList,
  myPlansPath,
  newPlanPath,
  planListKey,
  planPath,
} from './api';
import { PlanList, type PlanListState } from './PlanList';

// /students/:studentId/learning-plans (#310 B), for learning_plan.manage
// holders and tutors (the API decides): the h1 "Learning plans" with the
// student's name once GET /students/{id} answers (without it otherwise: a
// former tutor still reads the plans they created), "Back to student",
// "New plan" when the list's can_create says so (AM1), and the plans. A 404
// on the list shows the not-found page. A student or guardian is sent to
// their own page. After a draft is deleted, "Draft deleted." takes focus.
// Code-split by the router.

export function LearningPlansListPage() {
  const { t } = useTranslation();
  const { studentId = '' } = useParams();
  const location = useLocation();
  const familyOnly = useFamilyOnly();
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const notice = (location.state as { notice?: string } | null)?.notice;
  const student = useQuery({
    queryKey: studentDetailKey(studentId),
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
    retry: false,
    enabled: !familyOnly,
  });
  const list = useQuery({
    queryKey: planListKey(studentId),
    queryFn: ({ signal }) => fetchPlanList(studentId, signal),
    retry: false,
    enabled: !familyOnly,
  });
  const name = student.data ? fullName(student.data) : null;
  const title = name
    ? t('learningPlans.titleFor', { name })
    : t('learningPlans.title');
  useDocumentTitle(title);

  useEffect(() => {
    if (notice !== 'draftDeleted') {
      return;
    }
    // The notice renders with the page: focus it once it is there.
    const timer = window.setTimeout(() => noticeRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [notice]);

  if (familyOnly) {
    return <Navigate replace to={myPlansPath(studentId)} />;
  }
  if (list.error instanceof ApiError && list.error.status === 404) {
    return <NotFoundPage />;
  }

  const state: PlanListState = list.data
    ? {
        status: 'ready',
        items: list.data.items,
        truncated: list.data.truncated,
      }
    : list.isError
      ? {
          status: 'error',
          error: list.error,
          onRetry: () => void list.refetch(),
        }
      : { status: 'loading' };

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      {student.data ? (
        <Link
          to={`/students/${encodeURIComponent(studentId)}`}
          className={`${SMALL_LINK_CLASS} self-start`}
        >
          {t('learningPlans.backToStudent')}
        </Link>
      ) : null}
      <h1 className="text-2xl font-semibold break-words">{title}</h1>
      {notice === 'draftDeleted' ? (
        <p
          ref={noticeRef}
          role="status"
          tabIndex={-1}
          className="text-sm font-medium outline-none"
        >
          {t('learningPlans.notices.draftDeleted')}
        </p>
      ) : null}
      {list.data?.can_create ? (
        <Link
          to={newPlanPath(studentId)}
          className={`${LINK_BUTTON_CLASS} self-start`}
        >
          {t('learningPlans.new')}
        </Link>
      ) : null}
      <PlanList
        state={state}
        linkTo={planPath}
        emptyText={t('learningPlans.empty')}
      />
    </div>
  );
}

export default LearningPlansListPage;
