import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Spinner } from '@/components/feedback/Spinner';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { TEXT_LINK_CLASS } from '@/features/students/styles';
import { useFormatters } from '@/lib/formatters';
import type { CohortListItem } from './api';

// The cohorts list (#243 B2), presentational: the page passes the query
// state. Name (a link to the detail), template, subject and level, the
// tutor or the assignment status ("Offer pending", "No tutor yet"), the
// first and last session dates (local dates, as the API sends them) and
// the status as text. Loading, empty ("Clear filters" when filtered) and
// error states; "Load more" follows the API's cursor. The shared Table
// scrolls (a focusable region) when it doesn't fit, as at 320 px.

export type CohortsTableState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly CohortListItem[];
      hasMore: boolean;
      loadingMore: boolean;
      onMore: () => void;
    };

export interface CohortsTableProps {
  state: CohortsTableState;
  /** The id of the heading that names the table. */
  labelledBy: string;
  /** Whether a filter is applied ("Clear filters" in the empty state). */
  filtered: boolean;
  onClearFilters: () => void;
}

/** The tutor cell: the tutor, "…: offer pending" or "No tutor yet". */
function useTutorText() {
  const { t } = useTranslation();
  return (item: Pick<CohortListItem, 'tutor' | 'assignment_status'>) => {
    if (!item.tutor) {
      return t('courses.cohorts.assignment.noTutor');
    }
    return item.assignment_status === 'pending'
      ? t('courses.cohorts.assignment.pendingTutor', {
          name: item.tutor.display_name,
        })
      : item.tutor.display_name;
  };
}

export function CohortsTable({
  state,
  labelledBy,
  filtered,
  onClearFilters,
}: CohortsTableProps) {
  const { t } = useTranslation();
  const { formatLocalDate } = useFormatters();
  const tutorText = useTutorText();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={5} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  if (state.items.length === 0) {
    return (
      <div className="flex flex-col items-start gap-3">
        <p>{t('courses.cohorts.list.empty')}</p>
        {filtered ? (
          <Button type="button" variant="outline" onClick={onClearFilters}>
            {t('courses.cohorts.list.clearFilters')}
          </Button>
        ) : null}
      </div>
    );
  }
  const date = (value: string | null) =>
    value ? formatLocalDate(value) : t('courses.cohorts.list.noDate');
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('courses.cohorts.list.name')}</TableHead>
            <TableHead scope="col">
              {t('courses.cohorts.list.template')}
            </TableHead>
            <TableHead scope="col">
              {t('courses.cohorts.list.subject')}
            </TableHead>
            <TableHead scope="col">{t('courses.cohorts.list.tutor')}</TableHead>
            <TableHead scope="col">{t('courses.cohorts.list.first')}</TableHead>
            <TableHead scope="col">{t('courses.cohorts.list.last')}</TableHead>
            <TableHead scope="col">
              {t('courses.cohorts.list.status')}
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {state.items.map((item) => (
            <TableRow key={item.id}>
              <TableCell>
                <Link
                  to={`/courses/cohorts/${encodeURIComponent(item.id)}`}
                  className={TEXT_LINK_CLASS}
                >
                  {item.name}
                </Link>
              </TableCell>
              <TableCell>{item.template.name}</TableCell>
              <TableCell>
                {t('courses.cohorts.subjectLevel', {
                  subject: item.subject.name,
                  level: item.subject.level.name,
                })}
              </TableCell>
              <TableCell>{tutorText(item)}</TableCell>
              <TableCell>{date(item.first_session_on)}</TableCell>
              <TableCell>{date(item.last_session_on)}</TableCell>
              <TableCell>
                {t(`courses.cohorts.status.${item.status}`)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {state.hasMore ? (
        <div>
          <Button
            type="button"
            variant="outline"
            disabled={state.loadingMore}
            onClick={state.onMore}
          >
            {state.loadingMore ? <Spinner /> : null}
            {t('courses.cohorts.list.loadMore')}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
