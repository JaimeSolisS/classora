import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import type { FamilyStudent } from '@/features/my-schedule/api';
import { useRoles } from './access';
import {
  fetchStudentAssignment,
  invalidateAssignmentLists,
  myHomeworkPath,
  putSubmission,
  studentItemKey,
} from './api';
import { useFamilyStudent } from './family';
import { StudentAssignmentView } from './StudentAssignmentView';

// /my-homework/:assignmentId?student= (#313 E4, E6-E8; the notification
// target), students and guardians: the student `?student=` names (else the
// first family student, with no request for a foreign one), the shared
// StudentAssignmentView and "Back to homework" with the same student. The
// student's own login (role student, relation self) may hand in; a
// guardian never sees the form. A consent-required child (from the picker
// list or a 403) shows the consent message; a 404 (a deleted homework, or
// the student was removed from it) shows "This homework is no longer
// available." with a link to /my-homework, not the global not-found page.

function Back({ studentId }: { studentId?: string | null }) {
  const { t } = useTranslation();
  return (
    <Link
      to={myHomeworkPath(studentId)}
      className={`${SMALL_LINK_CLASS} self-start`}
    >
      {t('assignments.student.back')}
    </Link>
  );
}

function Shell({
  studentId,
  children,
}: {
  studentId?: string | null;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <Back studentId={studentId} />
      <h1 className="text-2xl font-semibold">
        {t('assignments.student.itemTitle')}
      </h1>
      {children}
    </div>
  );
}

export function MyHomeworkDetailPage() {
  const { t } = useTranslation();
  const { students, selected } = useFamilyStudent();
  useDocumentTitle(t('assignments.student.itemTitle'));

  if (students.isError) {
    return (
      <Shell>
        <ErrorAlert
          error={students.error}
          onRetry={() => void students.refetch()}
        />
      </Shell>
    );
  }
  if (!students.data) {
    return (
      <Shell>
        <LoadingState rows={6} />
      </Shell>
    );
  }
  if (!selected) {
    return (
      <Shell>
        <p role="status" className="text-sm">
          {t('academics.history.noStudents')}
        </p>
      </Shell>
    );
  }
  if (selected.schedule_access === 'consent_required') {
    return (
      <Shell studentId={selected.student_id}>
        <ConsentRequired
          name={selected.display_name}
          title={t('academics.history.consentTitle')}
        />
      </Shell>
    );
  }
  return <FamilyItem key={selected.student_id} student={selected} />;
}

function FamilyItem({ student }: { student: FamilyStudent }) {
  const { t } = useTranslation();
  const { assignmentId = '' } = useParams();
  const client = useQueryClient();
  const roles = useRoles();
  const [generation, setGeneration] = useState(0);
  const [gone, setGone] = useState(false);
  const key = studentItemKey(student.student_id, assignmentId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) =>
      fetchStudentAssignment(student.student_id, assignmentId, signal),
    retry: false,
  });
  useDocumentTitle(detail.data?.title ?? t('assignments.student.itemTitle'));
  const self = student.relation === 'self' && roles.includes('student');

  if (
    detail.error instanceof ApiError &&
    detail.error.code === 'student_consent_required'
  ) {
    return (
      <Shell studentId={student.student_id}>
        <ConsentRequired
          name={student.display_name}
          title={t('academics.history.consentTitle')}
        />
      </Shell>
    );
  }
  if (
    gone ||
    (detail.error instanceof ApiError && detail.error.status === 404)
  ) {
    return (
      <Shell studentId={student.student_id}>
        <p role="status" className="text-sm">
          {t('assignments.student.itemMissing')}
        </p>
        <Link
          to={myHomeworkPath()}
          className={`${SMALL_LINK_CLASS} self-start`}
        >
          {t('assignments.student.toList')}
        </Link>
      </Shell>
    );
  }
  if (detail.isError) {
    return (
      <Shell studentId={student.student_id}>
        <ErrorAlert
          error={detail.error}
          onRetry={() => void detail.refetch()}
        />
      </Shell>
    );
  }
  if (!detail.data) {
    return (
      <Shell studentId={student.student_id}>
        <LoadingState rows={6} />
      </Shell>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Back studentId={student.student_id} />
      <StudentAssignmentView
        key={generation}
        item={detail.data}
        audience={self ? 'self' : 'guardian'}
        studentName={student.display_name}
        focusHeadingOnMount={generation > 0}
        onReload={() => {
          void detail.refetch().then(() => setGeneration((n) => n + 1));
        }}
        onSubmit={
          self
            ? async (body) => {
                try {
                  const stored = await putSubmission(
                    assignmentId,
                    student.student_id,
                    body,
                  );
                  client.setQueryData(key, stored);
                  void invalidateAssignmentLists(client);
                } catch (caught) {
                  if (caught instanceof ApiError && caught.status === 404) {
                    setGone(true);
                  }
                  throw caught;
                }
              }
            : undefined
        }
      />
    </div>
  );
}

export default MyHomeworkDetailPage;
