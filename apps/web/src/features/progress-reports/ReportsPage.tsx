import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, Navigate, useSearchParams } from 'react-router';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Button } from '@/components/ui/button';
import { SMALL_LINK_CLASS } from '@/features/academics/styles';
import { SELECT_CLASS } from '@/features/students/styles';
import { useFamilyOnly, useReportReviewer, useRoles } from './access';
import {
  myReportsPath,
  REPORT_KINDS,
  reportPath,
  type ReportStatus,
} from './api';
import { useWorkingList } from './queries';
import { CardList, WorkingCard } from './ReportCards';
import { listFilters, statusLabelKey } from './rules';

// /progress-reports (#315 E1), for tutors (their own reports) and
// progress_report.review holders (every non-draft report; the API
// decides): the h1 "Progress reports", the filters kept in the URL
// (?status=, ?kind=, and ?mine=true for a reviewer who is also a tutor). A
// reviewer's default is the review queue (status=submitted, oldest first);
// a tutor's is every status, with Draft offered. Invalid values are
// ignored. The rows are cards linking to /progress-reports/:id, with "Load
// more". Empty: "No reports are waiting for review." (the queue), "You
// haven't written any progress reports yet." + "Open Students" (a tutor),
// or "No reports match these filters." + "Clear filters" (focus then goes
// to the heading). A student or guardian goes to /my-progress-reports.
// Code-split.

const REVIEW_STATUSES: readonly ReportStatus[] = [
  'submitted',
  'changes_requested',
  'approved_for_family',
  'retracted',
];

export function ReportsPage() {
  const { t } = useTranslation();
  const title = t('progressReports.title');
  useDocumentTitle(title);
  const id = useId();
  const familyOnly = useFamilyOnly();
  const reviewer = useReportReviewer();
  const tutor = useRoles().includes('tutor');
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = listFilters(searchParams, { reviewer, tutor });
  const { state } = useWorkingList(
    {
      status: filters.status,
      kind: filters.kind,
      studentId: null,
      mine: filters.mine,
    },
    !familyOnly,
  );
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [focus, setFocus] = useState(0);

  useEffect(() => {
    if (focus === 0) {
      return;
    }
    const timer = window.setTimeout(() => headingRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [focus]);

  if (familyOnly) {
    return <Navigate replace to={myReportsPath()} />;
  }

  function update(name: string, value: string | null) {
    const next = new URLSearchParams(searchParams);
    if (value === null) {
      next.delete(name);
    } else {
      next.set(name, value);
    }
    setSearchParams(next, { replace: true });
  }

  const statuses: (ReportStatus | 'all')[] = [
    ...(reviewer ? REVIEW_STATUSES : []),
    ...(tutor ? (['draft'] as const) : []),
    ...(!reviewer ? REVIEW_STATUSES : []),
    'all',
  ];
  const defaultStatusValue = reviewer ? 'submitted' : 'all';

  let empty;
  if (filters.filtered) {
    empty = (
      <div className="flex min-w-0 flex-col items-start gap-2">
        <p className="text-sm">{t('progressReports.list.emptyFiltered')}</p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            setSearchParams(new URLSearchParams(), { replace: true });
            setFocus((n) => n + 1);
          }}
        >
          {t('progressReports.list.clearFilters')}
        </Button>
      </div>
    );
  } else if (reviewer) {
    empty = <p className="text-sm">{t('progressReports.list.emptyQueue')}</p>;
  } else {
    empty = (
      <div className="flex min-w-0 flex-col items-start gap-2">
        <p className="text-sm">{t('progressReports.list.emptyTutor')}</p>
        <Link to="/students" className={SMALL_LINK_CLASS}>
          {t('progressReports.list.openStudents')}
        </Link>
      </div>
    );
  }

  return (
    <div className="flex w-full max-w-3xl min-w-0 flex-col gap-6">
      <h1
        ref={headingRef}
        tabIndex={-1}
        className="text-2xl font-semibold outline-none"
      >
        {title}
      </h1>
      <div className="flex min-w-0 flex-wrap items-end gap-4">
        <div className="flex min-w-0 flex-col gap-1.5">
          <label htmlFor={`${id}-status`} className="text-sm font-medium">
            {t('progressReports.list.status')}
          </label>
          <select
            id={`${id}-status`}
            className={`${SELECT_CLASS} w-full max-w-xs`}
            value={filters.statusValue}
            onChange={(event) =>
              update(
                'status',
                event.target.value === defaultStatusValue
                  ? null
                  : event.target.value,
              )
            }
          >
            {statuses.map((status) => (
              <option key={status} value={status}>
                {status === 'all'
                  ? t('progressReports.list.allStatuses')
                  : t(statusLabelKey(status))}
              </option>
            ))}
          </select>
        </div>
        <div className="flex min-w-0 flex-col gap-1.5">
          <label htmlFor={`${id}-kind`} className="text-sm font-medium">
            {t('progressReports.list.kind')}
          </label>
          <select
            id={`${id}-kind`}
            className={`${SELECT_CLASS} w-full max-w-xs`}
            value={filters.kind ?? ''}
            onChange={(event) => update('kind', event.target.value || null)}
          >
            <option value="">{t('progressReports.list.allKinds')}</option>
            {REPORT_KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {t(`progressReports.kinds.${kind}`)}
              </option>
            ))}
          </select>
        </div>
        {reviewer && tutor ? (
          <label className="flex min-h-9 items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4"
              checked={filters.mine}
              onChange={(event) =>
                update('mine', event.target.checked ? 'true' : null)
              }
            />
            {t('progressReports.list.onlyMine')}
          </label>
        ) : null}
      </div>
      <CardList
        state={state}
        empty={empty}
        render={(report) => (
          <WorkingCard
            key={report.id}
            report={report}
            showAuthor={reviewer}
            to={reportPath(report.id)}
          />
        )}
      />
    </div>
  );
}

export default ReportsPage;
