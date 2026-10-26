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
import { NotFoundPage } from '@/pages/NotFoundPage';
import { useFamilyOnly } from './access';
import {
  approveReport,
  deleteReport,
  fetchReport,
  invalidateReportLists,
  myReportPath,
  reportKey,
  reportsPath,
  requestReportChanges,
  retractReport,
  studentReportsPath,
  submitReport,
  updateReport,
  type ProgressReport,
} from './api';
import { ReportView } from './ReportView';

// /progress-reports/:reportId (#315 C, E; #83's review_path and the
// author's notification target), for tutors and progress_report.review
// holders: GET /progress-reports/{id}; an unknown, deleted or hidden report
// shows the not-found page. Each write sends the version R3 returned when
// the page loaded or last refetched, stores the returned view and
// invalidates the lists and the approval inbox. A deleted report goes
// (replace) to the student's reports with "Report deleted.". Reload
// refetches, then remounts the view, which focuses its heading. Arriving
// from the start step focuses its notice. A student or guardian goes to the
// family page of the same report. Code-split.

const NOTICES: Record<string, string> = {
  started: 'progressReports.notices.started',
};

function Shell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <h1 className="text-2xl font-semibold">
        {t('progressReports.reportTitle')}
      </h1>
      {children}
    </div>
  );
}

export function ReportPage() {
  const { t } = useTranslation();
  const tAny = t as unknown as (key: string) => string;
  const { reportId = '' } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const familyOnly = useFamilyOnly();
  const [generation, setGeneration] = useState(0);
  const [missing, setMissing] = useState(false);
  const key = reportKey(reportId);
  const detail = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => fetchReport(reportId, signal),
    retry: false,
    enabled: !familyOnly,
  });
  useDocumentTitle(
    detail.data
      ? t('progressReports.titleFor', {
          name: detail.data.student.display_name,
        })
      : t('progressReports.reportTitle'),
  );
  const arrived = (location.state as { notice?: string } | null)?.notice;

  if (familyOnly) {
    return <Navigate replace to={myReportPath(reportId)} />;
  }
  if (
    missing ||
    (detail.error instanceof ApiError && detail.error.status === 404)
  ) {
    return <NotFoundPage />;
  }
  if (!detail.data && detail.isError) {
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

  const current = () => client.getQueryData<ProgressReport>(key) ?? detail.data;

  /** A write: stores the view, refreshes the lists; a 404 shows the
   * not-found page. */
  async function write(
    call: () => Promise<ProgressReport>,
  ): Promise<ProgressReport> {
    try {
      const stored = await call();
      client.setQueryData(key, stored);
      void invalidateReportLists(client);
      return stored;
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setMissing(true);
      }
      throw caught;
    }
  }

  const noticeKey = arrived ? NOTICES[arrived] : undefined;
  const studentId = detail.data.student.id;
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Link to={reportsPath()} className={`${SMALL_LINK_CLASS} self-start`}>
        {t('progressReports.backToList')}
      </Link>
      <ReportView
        key={`${reportId}-${generation}`}
        report={detail.data}
        focusHeadingOnMount={generation > 0}
        initialNotice={noticeKey && generation === 0 ? tAny(noticeKey) : null}
        actions={{
          onSave: (body) =>
            write(() =>
              updateReport(reportId, { ...body, version: current().version }),
            ),
          onSubmit: () => write(() => submitReport(current())),
          onApprove: () => write(() => approveReport(current())),
          onRequestChanges: (reason) =>
            write(() => requestReportChanges(current(), reason)),
          onRetract: (reason) => write(() => retractReport(current(), reason)),
          onDelete: async () => {
            try {
              await deleteReport(current());
            } catch (caught) {
              if (caught instanceof ApiError && caught.status === 404) {
                setMissing(true);
              }
              throw caught;
            }
            client.removeQueries({ queryKey: key });
            void invalidateReportLists(client);
            void navigate(studentReportsPath(studentId), {
              replace: true,
              state: { notice: 'reportDeleted' },
            });
          },
          onRefreshFacts: async () => {
            const result = await detail.refetch();
            if (result.error) {
              throw result.error;
            }
          },
          onReload: () => {
            // Remount once the fresh report is in the cache: the view then
            // focuses its heading on mount (deterministic, #308 QA D4).
            void detail.refetch().then(() => setGeneration((n) => n + 1));
          },
        }}
      />
    </div>
  );
}

export default ReportPage;
