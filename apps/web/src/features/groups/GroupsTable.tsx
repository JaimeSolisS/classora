import { CircleStop } from 'lucide-react';
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
import { useFormatters } from '@/lib/formatters';
import type { GroupListItem, GroupStatusFilter } from './api';
import { listItemEnded, type GroupView } from './rules';

// The groups list (#234 B3, B5, B6), presentational: the page passes the
// query state. Staff: name (a link), subject and level, tutor, seats, next
// session (center zone) and "Ended" (text and an icon, never colour only)
// when the filter can show ended groups. Students: no seats column (their
// `enrolled_count` is null, and never rendered). Loading, empty ("No groups
// match these filters" with "Clear filters"; a student's own empty text)
// and error states; "Load more" follows the API's cursor (B2).

export type GroupsTableState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | {
      status: 'ready';
      items: readonly GroupListItem[];
      hasMore: boolean;
      loadingMore: boolean;
      onMore: () => void;
    };

export interface GroupsTableProps {
  state: GroupsTableState;
  view: GroupView;
  statusFilter: GroupStatusFilter;
  /** The id of the heading that names the table. */
  labelledBy: string;
  /** Whether a filter is applied ("Clear filters" in the empty state). */
  filtered: boolean;
  onClearFilters: () => void;
}

export const LINK_CLASS =
  'inline-block min-h-6 font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring';

export function EndedBadge() {
  const { t } = useTranslation();
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-xs font-medium whitespace-nowrap">
      <CircleStop aria-hidden="true" className="size-3.5" />
      {t('groups.ended')}
    </span>
  );
}

export function GroupsTable({
  state,
  view,
  statusFilter,
  labelledBy,
  filtered,
  onClearFilters,
}: GroupsTableProps) {
  const { t } = useTranslation();
  const { formatInstant } = useFormatters();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={5} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  if (state.items.length === 0) {
    if (view === 'student' && !filtered) {
      return <p>{t('groups.list.studentEmpty')}</p>;
    }
    return (
      <div className="flex flex-col items-start gap-3">
        <p>{t('groups.list.empty')}</p>
        {filtered ? (
          <Button type="button" variant="outline" onClick={onClearFilters}>
            {t('groups.list.clearFilters')}
          </Button>
        ) : null}
      </div>
    );
  }
  const staff = view === 'staff';
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('groups.list.name')}</TableHead>
            <TableHead scope="col">{t('groups.list.subject')}</TableHead>
            <TableHead scope="col">{t('groups.list.tutor')}</TableHead>
            {staff ? (
              <TableHead scope="col">{t('groups.list.seats')}</TableHead>
            ) : null}
            <TableHead scope="col">{t('groups.list.next')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {state.items.map((item) => (
            <TableRow key={item.id}>
              <TableCell>
                <div className="flex flex-wrap items-center gap-2">
                  <Link to={`/groups/${item.id}`} className={LINK_CLASS}>
                    {item.name}
                  </Link>
                  {listItemEnded(item, statusFilter) ? <EndedBadge /> : null}
                </div>
              </TableCell>
              <TableCell>
                {t('groups.subjectLevel', {
                  subject: item.subject.name,
                  level: item.subject.level.name,
                })}
              </TableCell>
              <TableCell>
                {item.tutor?.display_name ?? t('groups.noTutor')}
              </TableCell>
              {staff ? (
                <TableCell>
                  {t('groups.list.seatsValue', {
                    enrolled: item.enrolled_count ?? 0,
                    max: item.max_students,
                  })}
                </TableCell>
              ) : null}
              <TableCell>
                {item.next_starts_at
                  ? formatInstant(item.next_starts_at, 'dateTime')
                  : t('groups.list.noNext')}
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
            {t('groups.list.loadMore')}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
