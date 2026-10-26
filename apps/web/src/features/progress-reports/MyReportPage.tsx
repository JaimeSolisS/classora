import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import type { FamilyStudent } from '@/features/my-schedule/api';
import { approvedReportKey, fetchApprovedReport, myReportsPath } from './api';
import { useFamilyStudent } from './family';
import { ProgressReportReadView } from './ProgressReportReadView';

// /my-progress-reports/:reportId?student= (#315 F5, F6; the family's
// approved-notification target), students and guardians: the shared read
// view of an approved report and "Back to reports" with the same student.
// Without ?student= (or with a foreign one) it uses the picker's default
// student. A 403 student_consent_required shows the consent message; a 404
// (retracted, deleted, never approved, another family's) shows "This report
// is no longer available." with a link to /my-progress-reports, inline
// (not the global not-found page). "Try again" refetches, then focuses the
// heading.

function Back({ studentId }: { studentId?: string | null }) {
  const { t } = useTranslation();
  return (
    <Link
      to={myReportsPath(studentId)}
      className={`${SMALL_LINK_CLASS} self-start`}
    >
      {t('progressReports.family.back')}
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
        {t('progressReports.reportTitle')}
      </h1>
      {children}
    </div>
  );
}

function FamilyReport({
  student,
  reportId,
}: {
  student: FamilyStudent;
  reportId: string;
}) {
  const { t } = useTranslation();
  const studentId = student.student_id;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [retried, setRetried] = useState(0);
  const detail = useQuery({
    queryKey: approvedReportKey(studentId, reportId),
    queryFn: ({ signal }) => fetchApprovedReport(studentId, reportId, signal),
    retry: false,
  });

  useEffect(() => {
    if (retried === 0 || !detail.data) {
      return;
    }
    const timer = window.setTimeout(() => headingRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [retried, detail.data]);

  if (
    detail.error instanceof ApiError &&
    detail.error.code === 'student_consent_required'
  ) {
    return (
      <Shell studentId={studentId}>
        <ConsentRequired
          name={student.display_name}
          title={t('academics.history.consentTitle')}
        />
      </Shell>
    );
  }
  if (detail.error instanceof ApiError && detail.error.status === 404) {
    return (
      <Shell studentId={studentId}>
        <div className="flex min-w-0 flex-col items-start gap-2">
          <p role="status" className="text-sm">
            {t('progressReports.family.reportMissing')}
          </p>
          <Link to={myReportsPath()} className={SMALL_LINK_CLASS}>
            {t('progressReports.family.toList')}
          </Link>
        </div>
      </Shell>
    );
  }
  if (detail.isError) {
    return (
      <Shell studentId={studentId}>
        <ErrorAlert
          error={detail.error}
          onRetry={() => {
            void detail.refetch().then(() => setRetried((n) => n + 1));
          }}
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
      <Back studentId={studentId} />
      <ProgressReportReadView report={detail.data} headingRef={headingRef} />
    </div>
  );
}

export function MyReportPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('progressReports.reportTitle'));
  const { reportId = '' } = useParams();
  const { students, selected } = useFamilyStudent();

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
  return (
    <FamilyReport
      key={selected.student_id}
      student={selected}
      reportId={reportId}
    />
  );
}

export default MyReportPage;
