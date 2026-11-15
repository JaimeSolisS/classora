import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, Navigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { useCan } from '@/features/auth/useSession';
import { fetchStudent, studentDetailKey } from '@/features/students/api';
import { fullName } from '@/features/students/rules';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { useFamilyOnly } from './access';
import {
  fetchStudentAssignment,
  myHomeworkItemPath,
  myHomeworkPath,
  rosterPath,
  studentAssignmentPath,
  studentAssignmentsPath,
  studentItemKey,
} from './api';
import { StudentAssignmentsList } from './StudentAssignmentsList';
import { StudentAssignmentView } from './StudentAssignmentView';

// /students/:studentId/assignments (#313 D1) and
// /students/:studentId/assignments/:assignmentId (#313 D2), for
// assignment.review holders and tutors (the API decides: a current tutor
// reads that student's homework, a former creator only their own). The
// list's h1 is "Homework" with the student's name once GET /students/{id}
// answers (without it otherwise), with "Back to student"; the rows open the
// read-only detail, which offers "Open roster" to the creator (can_grade)
// and holders. A student or guardian goes to their own page. Code-split.

function useStudentName(studentId: string, enabled: boolean) {
  const student = useQuery({
    queryKey: studentDetailKey(studentId),
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
    retry: false,
    enabled,
  });
  return student.data ? fullName(student.data) : null;
}

export function StudentAssignmentsPage() {
  const { t } = useTranslation();
  const { studentId = '' } = useParams();
  const familyOnly = useFamilyOnly();
  const name = useStudentName(studentId, !familyOnly);
  const title = name
    ? t('assignments.student.titleFor', { name })
    : t('assignments.student.title');
  useDocumentTitle(title);
  if (familyOnly) {
    return <Navigate replace to={myHomeworkPath(studentId)} />;
  }
  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      {name ? (
        <Link
          to={`/students/${encodeURIComponent(studentId)}`}
          className={`${SMALL_LINK_CLASS} self-start`}
        >
          {t('assignments.backToStudent')}
        </Link>
      ) : null}
      <h1 className="text-2xl font-semibold break-words">{title}</h1>
      <StudentAssignmentsList
        studentId={studentId}
        studentName={name ?? ''}
        linkTo={(assignmentId) =>
          studentAssignmentPath(studentId, assignmentId)
        }
        emptyText={t('assignments.student.emptyStaff')}
      />
    </div>
  );
}

function Shell({
  studentId,
  children,
}: {
  studentId: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <Link
        to={studentAssignmentsPath(studentId)}
        className={`${SMALL_LINK_CLASS} self-start`}
      >
        {t('assignments.student.back')}
      </Link>
      <h1 className="text-2xl font-semibold">
        {t('assignments.student.itemTitle')}
      </h1>
      {children}
    </div>
  );
}

export function StudentAssignmentPage() {
  const { t } = useTranslation();
  const { studentId = '', assignmentId = '' } = useParams();
  const familyOnly = useFamilyOnly();
  const canReview = useCan('assignment.review');
  const name = useStudentName(studentId, !familyOnly);
  const detail = useQuery({
    queryKey: studentItemKey(studentId, assignmentId),
    queryFn: ({ signal }) =>
      fetchStudentAssignment(studentId, assignmentId, signal),
    retry: false,
    enabled: !familyOnly,
  });
  useDocumentTitle(detail.data?.title ?? t('assignments.student.itemTitle'));

  if (familyOnly) {
    return (
      <Navigate replace to={myHomeworkItemPath(assignmentId, studentId)} />
    );
  }
  if (detail.error instanceof ApiError && detail.error.status === 404) {
    return <NotFoundPage />;
  }
  if (detail.isError) {
    return (
      <Shell studentId={studentId}>
        <ErrorAlert
          error={detail.error}
          onRetry={() => void detail.refetch()}
        />
      </Shell>
    );
  }
  if (!detail.data) {
    return (
      <Shell studentId={studentId}>
        <LoadingState rows={6} />
      </Shell>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Link
        to={studentAssignmentsPath(studentId)}
        className={`${SMALL_LINK_CLASS} self-start`}
      >
        {t('assignments.student.back')}
      </Link>
      {name ? (
        <p className="text-sm break-words text-muted-foreground">
          {t('assignments.student.forStudent', { name })}
        </p>
      ) : null}
      <StudentAssignmentView item={detail.data} audience="staff" />
      {detail.data.can_grade || canReview ? (
        <Link
          to={rosterPath(assignmentId)}
          className={`${SMALL_LINK_CLASS} self-start`}
        >
          {t('assignments.student.openRoster')}
        </Link>
      ) : null}
    </div>
  );
}
