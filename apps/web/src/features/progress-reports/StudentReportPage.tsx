import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, Navigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { useFamilyOnly, useReportReviewer } from './access';
import {
  approvedReportKey,
  fetchApprovedReport,
  myReportPath,
  reportPath,
  studentReportsPath,
} from './api';
import { ProgressReportReadView } from './ProgressReportReadView';

// /students/:studentId/progress-reports/:reportId (#315 F3; R11), for
// reviewers and current tutors: the shared read view of an approved report
// and "Back to progress reports"; reviewers also get "Open review page"
// (where Retract is). A 404 shows the not-found page. A student or guardian
// goes to the family page of the same report. Code-split.

function Back({ studentId }: { studentId: string }) {
  const { t } = useTranslation();
  return (
    <Link
      to={studentReportsPath(studentId)}
      className={`${SMALL_LINK_CLASS} self-start`}
    >
      {t('progressReports.backToStudentReports')}
    </Link>
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
    <div className="flex min-w-0 flex-col gap-6">
      <Back studentId={studentId} />
      <h1 className="text-2xl font-semibold">
        {t('progressReports.reportTitle')}
      </h1>
      {children}
    </div>
  );
}

export function StudentReportPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('progressReports.reportTitle'));
  const { studentId = '', reportId = '' } = useParams();
  const familyOnly = useFamilyOnly();
  const reviewer = useReportReviewer();
  const detail = useQuery({
    queryKey: approvedReportKey(studentId, reportId),
    queryFn: ({ signal }) => fetchApprovedReport(studentId, reportId, signal),
    retry: false,
    enabled: !familyOnly,
  });

  if (familyOnly) {
    return <Navigate replace to={myReportPath(reportId, studentId)} />;
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
      <Back studentId={studentId} />
      <ProgressReportReadView
        report={detail.data}
        actions={
          reviewer ? (
            <Link
              to={reportPath(reportId)}
              className={`${SMALL_LINK_CLASS} self-start`}
            >
              {t('progressReports.openReviewPage')}
            </Link>
          ) : null
        }
      />
    </div>
  );
}

export default StudentReportPage;
