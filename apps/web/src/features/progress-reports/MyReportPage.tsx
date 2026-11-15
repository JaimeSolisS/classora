import { useQueries } from '@tanstack/react-query';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router';
import { ApiError } from '@/app/api';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { ConsentRequired } from '@/features/my-schedule/ConsentRequired';
import type { FamilyStudent } from '@/features/my-schedule/api';
import {
  approvedReportKey,
  fetchApprovedReport,
  myReportPath,
  myReportsPath,
} from './api';
import { useFamilyStudent } from './family';
import { FAMILY_NOT_FOUND_META } from './queries';
import { ProgressReportReadView } from './ProgressReportReadView';

// /my-progress-reports/:reportId?student= (#315 F5, F6; the family's
// approved-notification target), students and guardians: the shared read
// view of an approved report and "Back to reports" with the same student.
// A ?student= naming one of the family is trusted: one request (#341 E2).
// Without one (a staff link's redirect) or with a foreign or stale one, the
// page asks the same route for each family student it may read, in
// parallel, and never for anyone else (#341 E1): the one that answers 200
// is the report's student, the URL is replaced to name them, and the report
// renders from that request. A 403 student_consent_required shows the
// consent message; a 404 (retracted, deleted, never approved, another
// family's; every probe's) shows "This report is no longer available." with
// a link to /my-progress-reports, inline (not the global not-found page).
// "Try again" refetches the failed requests, then focuses the heading.

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
  candidates,
  reportId,
  resolve,
}: {
  /** The named student, or every readable one when probing (never empty). */
  candidates: FamilyStudent[];
  reportId: string;
  /** Probing: replace the URL with the student that answers 200. */
  resolve: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [retried, setRetried] = useState(0);
  // The same keys as a named visit, so once the URL names the report's
  // student the observer and its data are reused, with no second request.
  const probes = useQueries({
    queries: candidates.map(({ student_id: studentId }) => ({
      queryKey: approvedReportKey(studentId, reportId),
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        fetchApprovedReport(studentId, reportId, signal),
      retry: false,
      // A retracted, deleted or never-approved report, or another child's:
      // shown inline, not reported (QA D1).
      meta: FAMILY_NOT_FOUND_META,
    })),
  });
  const found = probes.findIndex((probe) => probe.data);
  const report = found >= 0 ? probes[found]!.data : undefined;
  const student =
    found >= 0
      ? candidates[found]
      : candidates.length === 1
        ? candidates[0]
        : undefined;
  const studentId = student?.student_id;
  const resolvedId = found >= 0 ? candidates[found]!.student_id : undefined;

  useEffect(() => {
    if (resolve && resolvedId) {
      void navigate(myReportPath(reportId, resolvedId), { replace: true });
    }
  }, [resolve, resolvedId, reportId, navigate]);

  useEffect(() => {
    if (retried === 0 || !report) {
      return;
    }
    const timer = window.setTimeout(() => headingRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [retried, report]);

  if (report) {
    return (
      <div className="flex min-w-0 flex-col gap-4">
        <Back studentId={studentId} />
        <ProgressReportReadView report={report} headingRef={headingRef} />
      </div>
    );
  }
  if (probes.some((probe) => probe.isPending)) {
    return (
      <Shell studentId={studentId}>
        <LoadingState rows={6} />
      </Shell>
    );
  }
  const failed = probes.filter(
    (probe) => !(probe.error instanceof ApiError && probe.error.status === 404),
  );
  const error = failed[0]?.error;
  if (
    student &&
    error instanceof ApiError &&
    error.code === 'student_consent_required'
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
  if (error) {
    return (
      <Shell studentId={studentId}>
        <ErrorAlert
          error={error}
          onRetry={() => {
            void Promise.all(failed.map((probe) => probe.refetch())).then(() =>
              setRetried((n) => n + 1),
            );
          }}
        />
      </Shell>
    );
  }
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

export function MyReportPage() {
  const { t } = useTranslation();
  useDocumentTitle(t('progressReports.reportTitle'));
  const { reportId = '' } = useParams();
  const { students, items, selected, named } = useFamilyStudent();

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
  // #341 E2: a named family student alone; otherwise every readable one
  // (never a foreign ?student= value, never an adult who hasn't shared).
  const readable = (student: FamilyStudent) =>
    student.schedule_access === 'allowed';
  const candidates = named ? [named].filter(readable) : items.filter(readable);
  if (candidates.length === 0) {
    const shown = named ?? selected;
    return (
      <Shell studentId={shown.student_id}>
        <ConsentRequired
          name={shown.display_name}
          title={t('academics.history.consentTitle')}
        />
      </Shell>
    );
  }
  return (
    <FamilyReport
      key={reportId}
      candidates={candidates}
      reportId={reportId}
      resolve={!named}
    />
  );
}

export default MyReportPage;
