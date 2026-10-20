import { useTranslation } from 'react-i18next';
import { Link } from 'react-router';
import { ErrorAlert } from '@/components/feedback/ErrorAlert';
import { LoadingState } from '@/components/feedback/LoadingState';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { StateBadge } from '@/features/users/admin/StateBadge';
import type { TutorListPage } from './api';

// The tutor list (#43 E2): name (a link to the profile), username, state,
// email and phone, then Previous/Next and the range. Loading, empty and
// error states. Presentational: the page passes the query state. Below `md`
// the table scrolls inside its own container (SC 1.4.10 data-table
// exception); the page itself never scrolls sideways.

export type TutorsTableState =
  | { status: 'loading' }
  | { status: 'error'; error: unknown; onRetry: () => void }
  | { status: 'ready'; page: TutorListPage };

interface TutorsTableProps {
  state: TutorsTableState;
  /** The id of the heading that names the table. */
  labelledBy: string;
  /** Goes to another 1-based page. */
  onPage: (page: number) => void;
}

export function TutorsTable({ state, labelledBy, onPage }: TutorsTableProps) {
  const { t } = useTranslation();

  if (state.status === 'loading') {
    return <LoadingState layout="table" rows={5} />;
  }
  if (state.status === 'error') {
    return <ErrorAlert error={state.error} onRetry={state.onRetry} />;
  }
  const { items, total, limit, offset } = state.page;
  if (items.length === 0) {
    return <p>{t('tutors.list.empty')}</p>;
  }
  const page = Math.floor(offset / limit) + 1;
  const notSet = t('tutors.profile.notSet');
  return (
    <div className="flex flex-col gap-4">
      <Table aria-labelledby={labelledBy}>
        <TableHeader>
          <TableRow>
            <TableHead scope="col">{t('tutors.list.displayName')}</TableHead>
            <TableHead scope="col">{t('tutors.list.username')}</TableHead>
            <TableHead scope="col">{t('tutors.list.state')}</TableHead>
            <TableHead scope="col">{t('tutors.list.email')}</TableHead>
            <TableHead scope="col">{t('tutors.list.phone')}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((item) => (
            <TableRow key={item.user_id}>
              <TableCell>
                <Link
                  to={`/tutors/${item.user_id}`}
                  className="inline-block min-h-6 font-medium underline underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring"
                >
                  {item.display_name}
                </Link>
              </TableCell>
              <TableCell>{item.username}</TableCell>
              <TableCell>
                <StateBadge state={item.state} />
              </TableCell>
              <TableCell>{item.email ?? notSet}</TableCell>
              <TableCell>{item.phone ?? notSet}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <nav
        aria-label={t('tutors.list.pagination')}
        className="flex flex-wrap items-center gap-3"
      >
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={offset === 0}
          onClick={() => onPage(page - 1)}
        >
          {t('tutors.list.previous')}
        </Button>
        <p className="text-sm">
          {t('tutors.list.range', {
            from: offset + 1,
            to: offset + items.length,
            total,
          })}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={offset + items.length >= total}
          onClick={() => onPage(page + 1)}
        >
          {t('tutors.list.next')}
        </Button>
      </nav>
    </div>
  );
}
