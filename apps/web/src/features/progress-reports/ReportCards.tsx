import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import { useFormatters } from '@/lib/formatters';
import type { ApprovedReportItem, ProgressReportListItem } from './api';
import { StatusBadge } from './StatusBadge';
import { usePeriodText } from './text';

// The report lists as cards (#315 E1, F2, F4; phone first): a working
// report (the student or the period as the link, the kind and period, the
// subject, "By {author}" for reviewers, the status badge, "Waiting since"
// or "Approved") and an approved report (the period as the link, the kind,
// the subject, "Written by", "Approved"). "Load more" follows next_cursor.

const CARD_LINK =
  'underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring';

export function WorkingCard({
  report,
  showAuthor,
  showStudent = true,
  to,
  level = 2,
}: {
  report: ProgressReportListItem;
  showAuthor: boolean;
  showStudent?: boolean;
  to: string;
  /** The card title's heading level (3 under a section's h2). */
  level?: 2 | 3;
}) {
  const Heading = level === 2 ? 'h2' : 'h3';
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const periods = usePeriodText();
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-4">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <Heading className="min-w-0 text-base font-semibold break-words">
          <Link to={to} className={CARD_LINK}>
            {showStudent ? report.student.display_name : periods.line(report)}
          </Link>
        </Heading>
        <StatusBadge status={report.status} />
      </div>
      {showStudent ? (
        <p className="text-sm break-words">{periods.line(report)}</p>
      ) : null}
      <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span className="break-words">
          {report.subject?.name ?? t('progressReports.allSubjects')}
        </span>
        {showAuthor ? (
          <span className="break-words">
            {t('progressReports.by', { name: report.author.display_name })}
          </span>
        ) : null}
      </p>
      {report.status === 'submitted' && report.submitted_at ? (
        <p className="text-sm">
          {t('progressReports.list.waitingSince', {
            at: formatInstant(report.submitted_at, 'dateTime'),
          })}
        </p>
      ) : report.approved_at && report.status === 'approved_for_family' ? (
        <p className="text-sm">
          {t('progressReports.list.approvedAt', {
            at: formatInstant(report.approved_at, 'date'),
          })}
        </p>
      ) : null}
    </li>
  );
}

export function ApprovedCard({
  report,
  to,
  level = 2,
}: {
  report: ApprovedReportItem;
  to: string;
  /** The card title's heading level (3 under a section's h2). */
  level?: 2 | 3;
}) {
  const Heading = level === 2 ? 'h2' : 'h3';
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();
  const periods = usePeriodText();
  return (
    <li className="flex min-w-0 flex-col gap-2 rounded-md border border-border p-4">
      <Heading className="min-w-0 text-base font-semibold break-words">
        <Link to={to} className={CARD_LINK}>
          {periods.line(report)}
        </Link>
      </Heading>
      <p className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-sm text-muted-foreground">
        <span className="break-words">
          {report.subject?.name ?? t('progressReports.allSubjects')}
        </span>
        <span className="break-words">
          {t('progressReports.writtenBy', { name: report.author.display_name })}
        </span>
      </p>
      <p className="text-sm">
        {t('progressReports.list.approvedAt', {
          at: formatInstant(report.approved_at, 'date'),
        })}
      </p>
    </li>
  );
}

export interface PagedState<T> {
  status: 'loading' | 'error' | 'ready';
  error?: unknown;
  items: readonly T[];
  hasMore: boolean;
  loadingMore: boolean;
  onRetry: () => void;
  onMore: () => void;
}

/** A list of cards with its loading, error, empty and "Load more" states. */
export function CardList<T>({
  state,
  render,
  empty,
}: {
  state: PagedState<T>;
  render: (item: T) => React.ReactNode;
  empty: React.ReactNode;
}) {
  const { t } = useTranslation();
  if (state.status === 'loading') {
    return <LoadingState rows={3} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  if (state.items.length === 0) {
    return <>{empty}</>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <ul className="flex min-w-0 flex-col gap-3">
        {state.items.map((item) => render(item))}
      </ul>
      {state.hasMore ? (
        <Button
          type="button"
          variant="outline"
          className="self-start"
          disabled={state.loadingMore}
          onClick={state.onMore}
        >
          {state.loadingMore ? (
            <Spinner label={t('progressReports.list.loadingMore')} showLabel />
          ) : (
            t('progressReports.list.loadMore')
          )}
        </Button>
      ) : null}
    </div>
  );
}
