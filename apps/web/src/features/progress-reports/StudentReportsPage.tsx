import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, Navigate, useLocation, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { fetchStudent, studentDetailKey } from '@/features/students/api';
import { fullName } from '@/features/students/rules';
import { LINK_BUTTON_CLASS } from '@/features/students/styles';
import { NotFoundPage } from '@/pages/NotFoundPage';
import { useFamilyOnly, useReportReviewer } from './access';
import {
  myReportsPath,
  newReportPath,
  reportPath,
  studentReportPath,
} from './api';
import { useApprovedList, useWorkingList } from './queries';
import { ApprovedCard, CardList, WorkingCard } from './ReportCards';
import { ReportSchedulePanel } from './ReportSchedulePanel';

// /students/:studentId/progress-reports (#315 F2), for reviewers and tutors
// (the API decides): the h1 "Progress reports" with the student's name
// once GET /students/{id} answers (without it otherwise: a former author
// still loads the approved list), "Back to student", "New report" when R10's
// can_create says so (AM2), "In progress and in review" (R2 for this
// student: a tutor's own reports, drafts included; a reviewer's every
// non-draft report; hidden when empty) and "Approved" (R10, with "Load
// more"). A 404 on R10 shows the not-found page. After a delete, "Report
// deleted." takes focus. A student or guardian goes to their own page. #316:
// the "Report schedule" panel for reviewers, below the header. Code-split.

export function StudentReportsPage() {
  const { t } = useTranslation();
  const { studentId = '' } = useParams();
  const location = useLocation();
  const familyOnly = useFamilyOnly();
  const reviewer = useReportReviewer();
  const noticeRef = useRef<HTMLParagraphElement>(null);
  const notice = (location.state as { notice?: string } | null)?.notice;
  const student = useQuery({
    queryKey: studentDetailKey(studentId),
    queryFn: ({ signal }) => fetchStudent(studentId, signal),
    retry: false,
    enabled: !familyOnly,
  });
  const approved = useApprovedList(studentId, !familyOnly);
  const working = useWorkingList(
    { status: null, kind: null, studentId, mine: false },
    !familyOnly,
  );
  const name = student.data ? fullName(student.data) : null;
  const title = name
    ? t('progressReports.titleFor', { name })
    : t('progressReports.title');
  useDocumentTitle(title);

  useEffect(() => {
    if (notice !== 'reportDeleted') {
      return;
    }
    const timer = window.setTimeout(() => noticeRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [notice]);

  if (familyOnly) {
    return <Navigate replace to={myReportsPath(studentId)} />;
  }
  if (
    approved.query.error instanceof ApiError &&
    approved.query.error.status === 404
  ) {
    return <NotFoundPage />;
  }

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      {student.data ? (
        <Link
          to={`/students/${encodeURIComponent(studentId)}`}
          className={`${SMALL_LINK_CLASS} self-start`}
        >
          {t('progressReports.backToStudent')}
        </Link>
      ) : null}
      <h1 className="text-2xl font-semibold break-words">{title}</h1>
      {notice === 'reportDeleted' ? (
        <p
          ref={noticeRef}
          role="status"
          tabIndex={-1}
          className="text-sm font-medium outline-none"
        >
          {t('progressReports.notices.deleted')}
        </p>
      ) : null}
      {approved.canCreate ? (
        <Link
          to={newReportPath(studentId)}
          className={`${LINK_BUTTON_CLASS} self-start`}
        >
          {t('progressReports.new')}
        </Link>
      ) : null}
      {reviewer ? (
        <ReportSchedulePanel studentId={studentId} studentName={name} />
      ) : null}
      {working.state.status === 'ready' &&
      working.state.items.length === 0 ? null : (
        <section className="flex min-w-0 flex-col gap-3">
          <h2 className="text-lg font-semibold">
            {t('progressReports.student.working')}
          </h2>
          <CardList
            state={working.state}
            empty={null}
            render={(report) => (
              <WorkingCard
                key={report.id}
                report={report}
                showAuthor={reviewer}
                showStudent={false}
                level={3}
                to={reportPath(report.id)}
              />
            )}
          />
        </section>
      )}
      <section className="flex min-w-0 flex-col gap-3">
        <h2 className="text-lg font-semibold">
          {t('progressReports.student.approved')}
        </h2>
        <CardList
          state={approved.state}
          empty={
            <p className="text-sm">{t('progressReports.student.noApproved')}</p>
          }
          render={(report) => (
            <ApprovedCard
              key={report.id}
              report={report}
              level={3}
              to={studentReportPath(studentId, report.id)}
            />
          )}
        />
      </section>
    </div>
  );
}

export default StudentReportsPage;
