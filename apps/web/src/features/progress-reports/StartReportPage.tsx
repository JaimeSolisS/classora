import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { useCenterTimeZone } from '@/features/auth/useCenterTimeZone';
import {
  fetchSubjectOptions,
  SUBJECT_OPTIONS_KEY,
} from '@/features/courses/api';
import { fetchStudent, studentDetailKey } from '@/features/students/api';
import { fullName } from '@/features/students/rules';
import { todayInZone } from '@/lib/time';
import { NotFoundPage } from '@/pages/NotFoundPage';
import {
  createHintsKey,
  createReport,
  fetchApprovedList,
  invalidateReportLists,
  reportKey,
  reportPath,
  studentReportsPath,
} from './api';
import { StartReportForm } from './StartReportForm';

// /students/:studentId/progress-reports/new (#315 B, tutors): the h1 "New
// progress report" and the student's name (GET /students/{id}; a 404 shows
// the not-found page), and R10 (limit 1) for can_create and the subjects
// the caller teaches the student (AM2). Without can_create the page says
// so and shows no form. After the create the page goes (replace) to the
// report's editor, whose notice takes focus. "Today" is the center's.
// Code-split by the router.

function Shell({
  studentId,
  name,
  children,
}: {
  studentId: string;
  name?: string | null;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex w-full max-w-xl min-w-0 flex-col gap-6">
      <Link
        to={studentReportsPath(studentId)}
        className={`${SMALL_LINK_CLASS} self-start`}
      >
        {t('progressReports.backToStudentReports')}
      </Link>
      <div className="flex min-w-0 flex-col gap-1">
        <h1 className="text-2xl font-semibold">
          {t('progressReports.start.title')}
        </h1>
        {name ? <p className="text-base break-words">{name}</p> : null}
      </div>
      {children}
    </div>
  );
}

export function StartReportPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('progressReports.start.title'));
  const { studentId = '' } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const timeZone = useCenterTimeZone();
  const [missing, setMissing] = useState(false);
  const student = useQuery({
    queryKey: studentDetailKey(studentId),
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
    retry: false,
  });
  const hints = useQuery({
    queryKey: createHintsKey(studentId),
    queryFn: ({ signal }) => fetchApprovedList(studentId, null, 1, signal),
    retry: false,
  });
  // The creatable subjects carry names only; this read gives their levels,
  // so two subjects of one name (Matemáticas in two levels) stay
  // distinguishable (the #310 precedent).
  const subjects = useQuery({
    queryKey: SUBJECT_OPTIONS_KEY,
    queryFn: ({ signal }) => fetchSubjectOptions(signal),
    enabled: hints.data?.can_create === true,
  });

  if (
    missing ||
    (student.error instanceof ApiError && student.error.status === 404) ||
    (hints.error instanceof ApiError && hints.error.status === 404)
  ) {
    return <NotFoundPage />;
  }
  const name = student.data ? fullName(student.data) : null;
  const failed = student.isError ? student : hints.isError ? hints : null;
  if (failed) {
    return (
      <Shell studentId={studentId} name={name}>
        <ErrorAlert
          error={failed.error}
          onRetry={() => void failed.refetch()}
        />
      </Shell>
    );
  }
  if (!student.data || !hints.data) {
    return (
      <Shell studentId={studentId}>
        <LoadingState rows={5} />
      </Shell>
    );
  }
  if (!hints.data.can_create) {
    return (
      <Shell studentId={studentId} name={name}>
        <p role="status" className="text-sm">
          {t('progressReports.start.cannotCreate')}
        </p>
      </Shell>
    );
  }
  const levels = new Map(
    (subjects.data ?? []).map((subject) => [subject.id, subject.levelName]),
  );
  const choices = hints.data.creatable_subjects.map((subject) => {
    const level = levels.get(subject.id);
    return {
      id: subject.id,
      label: level
        ? t('academics.subjectOption', { subject: subject.name, level })
        : subject.name,
    };
  });
  return (
    <Shell studentId={studentId} name={name}>
      <StartReportForm
        studentId={studentId}
        today={todayInZone(timeZone)}
        subjects={choices}
        onStart={async (body) => {
          let created;
          try {
            created = await createReport(body);
          } catch (caught) {
            if (caught instanceof ApiError && caught.status === 404) {
              setMissing(true);
            }
            throw caught;
          }
          client.setQueryData(reportKey(created.id), created);
          void invalidateReportLists(client);
          void navigate(reportPath(created.id), {
            replace: true,
            state: { notice: 'started' },
          });
        }}
      />
    </Shell>
  );
}

export default StartReportPage;
