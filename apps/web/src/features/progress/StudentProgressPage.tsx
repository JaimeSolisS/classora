import { useQuery } from '@tanstack/react-query';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, Navigate, useParams, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { useCan } from '@/features/auth/useSession';
import { fetchStudent, studentDetailKey } from '@/features/students/api';
import { fullName } from '@/features/students/rules';
import { myProgressPath, useProgressFamilyOnly } from './access';
import { ProgressLinks } from './ProgressLinks';
import { ProgressView } from './ProgressView';

// /students/:studentId/progress (#322 A2, B6, D1, E2, F1), for
// evaluation.manage holders and tutors (the API decides): the h1
// "Progress" with the student's name once GET /students/{id} answers
// (without it otherwise: a former tutor gets 404 there and still reads the
// classes they taught), "Back to student", the related links, and the
// shared progress view. Staff with schedule.view get "Session details" in
// the class log. A student or guardian is sent to /my-progress (#308 QA
// D2). Code-split by the router.

export function StudentProgressPage() {
  const { t } = useTranslation();
  const { studentId = '' } = useParams();
  const [params] = useSearchParams();
  const familyOnly = useProgressFamilyOnly();
  const showsSessionLink = useCan('schedule.view');
  const heading = useRef<HTMLHeadingElement>(null);
  const student = useQuery({
    queryKey: studentDetailKey(studentId),
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
    retry: false,
    enabled: !familyOnly,
    // A former tutor's 404 is a designed state (E2), not an error.
    meta: { expectedStatuses: [404] },
  });
  const name = student.data ? fullName(student.data) : null;
  const title = name ? t('progress.titleFor', { name }) : t('progress.title');
  useDocumentTitle(title);

  if (familyOnly) {
    const next = new URLSearchParams(params);
    next.set('student', studentId);
    return <Navigate replace to={`${myProgressPath()}?${next.toString()}`} />;
  }

  return (
    <div className="flex w-full max-w-6xl min-w-0 flex-col gap-6">
      <div className="flex min-w-0 flex-col gap-3">
        {student.data ? (
          <Link
            to={`/students/${encodeURIComponent(studentId)}`}
            className={`${SMALL_LINK_CLASS} self-start`}
          >
            {t('progress.backToStudent')}
          </Link>
        ) : null}
        <h1
          ref={heading}
          tabIndex={-1}
          className="text-2xl font-semibold break-words outline-none"
        >
          {title}
        </h1>
      </div>
      <ProgressView
        key={studentId}
        studentId={studentId}
        audience="staff"
        studentName={name ?? ''}
        headingRef={heading}
        links={<ProgressLinks studentId={studentId} audience="staff" />}
        showsSessionLink={showsSessionLink}
      />
    </div>
  );
}

export default StudentProgressPage;
